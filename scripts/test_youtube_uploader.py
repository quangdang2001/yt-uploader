#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit test cho youtube_uploader.py — khong goi mang, khong ton quota.

Chay:
    cd scripts
    python -m unittest test_youtube_uploader -v

Trong tam la cach `process_one` phan luong file giua pending/, done/, failed/
va cach no giu hay xoa state. Day la cho da tung co hai loi that:

  1. Het quota bi coi la loi cua file  -> ca pending/ bi don vao failed/
  2. state.clear() chay ca khi move hong -> lan sau upload lai, trung video

Moi loi co mot test rieng ben duoi, danh dau ro.
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import youtube_uploader as yu  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

def setUpModule():
    # process_one co log.exception o nhanh loi — test nao cung di qua do, de
    # nguyen thi output day traceback khong phai loi that.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


FOLDERS = {"pending": "P", "done": "D", "failed": "F"}
VIDEO_BYTES = b"x" * 2048


class FakeResp:
    """Du cho HttpError: no chi doc .status va .reason."""

    def __init__(self, status: int):
        self.status = status
        self.reason = "test"


def http_error(status: int, reason: str) -> HttpError:
    body = json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}})
    return HttpError(FakeResp(status), body.encode())


def meta_json(**over) -> str:
    base = {"title": "Video test", "categoryId": "27"}
    base.update(over)
    return json.dumps(base)


class Harness:
    """Thay toan bo cho goi mang trong process_one.

    Chi patch cac ham o muc module, nen logic phan luong cua process_one van
    chay that — do moi la thu dang kiem tra.
    """

    def __init__(self, tmp: Path, *, with_thumb=False, upload_raises=None,
                 move_fails_for=(), thumb_raises=None, meta=None,
                 no_video=False, short_download=False):
        self.tmp = tmp
        self.with_thumb = with_thumb
        self.no_video = no_video
        self.short_download = short_download
        self.upload_raises = upload_raises
        self.move_fails_for = set(move_fails_for)
        self.thumb_raises = thumb_raises
        self.meta = meta if meta is not None else meta_json()
        self.moves: list[tuple[str, str]] = []   # (file_id, dest)
        self.uploads = 0
        self.notices: list[str] = []

    # --- cac ham duoc patch ---
    def download_text(self, drive, file_id):
        return self.meta

    def find_media(self, drive, pending, base):
        if self.no_video:
            return None, None
        video = {"id": "vid-file", "name": f"{base}.mp4", "size": str(len(VIDEO_BYTES))}
        thumb = {"id": "thumb-file", "name": f"{base}.jpg"} if self.with_thumb else None
        return video, thumb

    def download_to(self, drive, file_id, dest, label):
        if file_id != "vid-file":
            dest.write_bytes(b"jpg")
        elif self.short_download:
            dest.write_bytes(VIDEO_BYTES[:10])   # rot mang giua chung
        else:
            dest.write_bytes(VIDEO_BYTES)

    def upload_video(self, youtube, path, body):
        self.uploads += 1
        if self.upload_raises:
            raise self.upload_raises
        return "YTID123"

    def set_thumbnail(self, youtube, video_id, path):
        if self.thumb_raises:
            raise self.thumb_raises

    def move_file(self, drive, file_id, src, dest):
        if file_id in self.move_fails_for:
            raise OSError(f"mang hong khi chuyen {file_id}")
        self.moves.append((file_id, dest))

    def notify(self, message):
        self.notices.append(message)

    def patches(self):
        return mock.patch.multiple(
            yu,
            download_text=self.download_text,
            find_media=self.find_media,
            download_to=self.download_to,
            upload_video=self.upload_video,
            set_thumbnail=self.set_thumbnail,
            move_file=self.move_file,
            notify=self.notify,
        )

    def dests(self) -> set[str]:
        return {d for _, d in self.moves}


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.state = yu.State(self.tmp / "state.json")

    def item(self, name="v001.json", fid="json-file"):
        return {"id": fid, "name": name}

    def run_one(self, h, item=None):
        with h.patches():
            return yu.process_one(mock.Mock(), mock.Mock(), FOLDERS,
                                  item or self.item(), self.state, self.tmp, "kenh")


# ------------------------------------------------------- loi 1: het quota

class TestQuota(Base):
    def test_nhan_dien_quota(self):
        for reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"):
            self.assertTrue(yu.is_quota_error(http_error(403, reason)), reason)
        self.assertTrue(yu.is_quota_error(http_error(429, "quotaExceeded")))

    def test_khong_nham_loi_khac_thanh_quota(self):
        self.assertFalse(yu.is_quota_error(http_error(403, "forbidden")))
        self.assertFalse(yu.is_quota_error(http_error(500, "backendError")))
        # rateLimitExceeded la loi tam thoi, da co retry rieng -> khong tinh
        self.assertFalse(yu.is_quota_error(http_error(403, "rateLimitExceeded")))
        self.assertFalse(yu.is_quota_error(OSError("mang hong")))

    def test_quota_de_file_o_lai_pending(self):
        """LOI 1: truoc day file bi day sang failed/ du no khong co loi gi."""
        h = Harness(self.tmp, upload_raises=http_error(403, "quotaExceeded"))
        with self.assertRaises(yu.QuotaExceeded):
            self.run_one(h)
        self.assertEqual(h.moves, [], "khong duoc chuyen file nao khi het quota")
        self.assertIsNone(self.state.get("v001"), "chua upload thi khong co state")

    def test_quota_dung_ca_vong_lap_va_khong_dung_file_con_lai(self):
        """LOI 1: 3 file cho, het quota ngay file dau -> ca 3 con nguyen."""
        h = Harness(self.tmp, upload_raises=http_error(403, "quotaExceeded"))
        items = [self.item(f"v{i}.json", f"json-{i}") for i in range(3)]
        args = mock.Mock(dry_run=False, max_per_run=5, temp_dir=None)
        with h.patches(), mock.patch.object(yu, "list_pending_json", return_value=items):
            ok = yu.run_once(args, mock.Mock(), mock.Mock(), FOLDERS, self.state, "kenh")
        self.assertEqual(ok, 0)
        self.assertEqual(h.uploads, 1, "phai dung ngay, khong thu tiep 2 file sau")
        self.assertEqual(h.moves, [])
        self.assertTrue(any("3 file con nguyen" in n for n in h.notices), h.notices)

    def test_loi_403_thuong_van_sang_failed(self):
        """403 khong phai quota thi van la loi cua file."""
        h = Harness(self.tmp, upload_raises=http_error(403, "forbidden"))
        self.assertFalse(self.run_one(h))
        self.assertEqual(h.dests(), {"F"})


# ------------------------------------- loi 2: state bi xoa khi move that bai

class TestState(Base):
    def test_giu_state_khi_move_hong_sau_upload(self):
        """LOI 2: state bi xoa -> lan sau upload lai, trung video + 1600 quota."""
        h = Harness(self.tmp, move_fails_for={"json-file"})
        self.assertFalse(self.run_one(h))          # move hong -> bao that bai
        saved = self.state.get("v001")
        self.assertIsNotNone(saved, "con file trong pending/ thi PHAI giu state")
        self.assertEqual(saved["videoId"], "YTID123")

    def test_lan_chay_sau_khong_upload_lai(self):
        """Tiep theo test tren: chay lai phai bo qua buoc upload."""
        h1 = Harness(self.tmp, move_fails_for={"json-file"})
        self.run_one(h1)
        h2 = Harness(self.tmp)                     # mang da thong
        self.assertTrue(self.run_one(h2))
        self.assertEqual(h2.uploads, 0, "video da len roi, khong duoc upload lan hai")
        self.assertEqual(h2.dests(), {"D"})
        self.assertIsNone(self.state.get("v001"), "xong han thi moi xoa state")

    def test_xoa_state_khi_da_chuyen_het_file(self):
        h = Harness(self.tmp, thumb_raises=OSError("mang hong"), with_thumb=True)
        self.assertFalse(self.run_one(h))
        self.assertEqual(h.dests(), {"D"}, "video da len -> done/, khong phai failed/")
        self.assertIsNone(self.state.get("v001"), "chuyen het file roi thi xoa state")


# --------------------------------------------------------------- move_all

class TestMoveAll(Base):
    def test_bo_khoi_list_cai_da_chuyen(self):
        h = Harness(self.tmp, move_fails_for={"b"})
        items = [{"id": "a", "name": "a.json"}, {"id": "b", "name": "b.mp4"}]
        with h.patches():
            stuck = yu.move_all(mock.Mock(), items, "P", "D")
        self.assertEqual(stuck, ["b.mp4"])
        self.assertEqual([i["id"] for i in items], ["b"], "chi con cai ket lai")

    def test_goi_lan_hai_khong_chuyen_trung(self):
        h = Harness(self.tmp, move_fails_for={"b"})
        items = [{"id": "a", "name": "a.json"}, {"id": "b", "name": "b.mp4"}]
        with h.patches():
            yu.move_all(mock.Mock(), items, "P", "D")
            h.move_fails_for.clear()
            self.assertEqual(yu.move_all(mock.Mock(), items, "P", "D"), [])
        self.assertEqual([m[0] for m in h.moves], ["a", "b"], "moi file dung 1 lan")


# ------------------------------------------------------ cac luong con lai

class TestLuongChinh(Base):
    def test_duong_thanh_cong(self):
        h = Harness(self.tmp, with_thumb=True)
        self.assertTrue(self.run_one(h))
        self.assertEqual(len(h.moves), 3, "json + mp4 + jpg")
        self.assertEqual(h.dests(), {"D"})
        self.assertIsNone(self.state.get("v001"))
        self.assertTrue(any("YTID123" in n for n in h.notices))

    def test_thieu_mp4_thi_sang_failed(self):
        h = Harness(self.tmp, no_video=True)
        self.assertFalse(self.run_one(h))
        self.assertEqual(h.dests(), {"F"})
        self.assertEqual(h.uploads, 0)

    def test_json_khong_hop_le_thi_sang_failed(self):
        for bad in ("khong phai json", "[1,2]", json.dumps({"title": "  "})):
            with self.subTest(bad=bad):
                self.state.clear("v001")
                h = Harness(self.tmp, meta=bad)
                self.assertFalse(self.run_one(h))
                self.assertEqual(h.uploads, 0)
                self.assertEqual(h.dests(), {"F"})

    def test_tai_thieu_byte_thi_khong_upload(self):
        h = Harness(self.tmp, short_download=True)
        self.assertFalse(self.run_one(h))
        self.assertEqual(h.uploads, 0, "file tai thieu thi khong duoc upload")
        self.assertEqual(h.dests(), {"F"})

    def test_thumbnail_403_khong_lam_hong_ca_video(self):
        h = Harness(self.tmp, with_thumb=True, thumb_raises=http_error(403, "forbidden"))
        self.assertTrue(self.run_one(h), "thieu thumbnail khong phai that bai")
        self.assertEqual(h.dests(), {"D"})

    def test_ten_json_hoa_thuong_lan_lon(self):
        h = Harness(self.tmp)
        self.assertTrue(self.run_one(h, self.item("V001.JSON")))
        self.assertEqual(h.dests(), {"D"})


class TestBuildBody(unittest.TestCase):
    def test_bat_buoc_co_title(self):
        for bad in ({}, {"title": ""}, {"title": "   "}):
            with self.assertRaises(ValueError):
                yu.build_body(bad)

    def test_cat_title_100_ky_tu(self):
        body = yu.build_body({"title": "a" * 250})
        self.assertEqual(len(body["snippet"]["title"]), 100)

    def test_publishAt_ep_private(self):
        body = yu.build_body({"title": "t", "privacyStatus": "public",
                              "publishAt": "2026-11-05T12:00:00Z"})
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], "2026-11-05T12:00:00Z")

    def test_publishAt_doi_ve_utc(self):
        body = yu.build_body({"title": "t", "publishAt": "2026-11-05T19:00:00+07:00"})
        self.assertEqual(body["status"]["publishAt"], "2026-11-05T12:00:00Z")

    def test_publishAt_thieu_mui_gio_thi_loi(self):
        with self.assertRaises(ValueError):
            yu.build_body({"title": "t", "publishAt": "2026-11-05T12:00:00"})

    def test_privacy_la_private(self):
        self.assertEqual(yu.build_body({"title": "t"})["status"]["privacyStatus"], "private")
        self.assertEqual(
            yu.build_body({"title": "t", "privacyStatus": "bua"})["status"]["privacyStatus"],
            "private")

    def test_tags_cat_cho_vua_gioi_han(self):
        body = yu.build_body({"title": "t", "tags": ["x" * 100] * 10})
        self.assertLessEqual(len(",".join(body["snippet"]["tags"])), 480)

    def test_tags_dang_chuoi(self):
        body = yu.build_body({"title": "t", "tags": "a, b , ,c"})
        self.assertEqual(body["snippet"]["tags"], ["a", "b", "c"])

    def test_co_khai_synthetic_va_madeForKids(self):
        body = yu.build_body({"title": "t", "containsSyntheticMedia": True,
                              "madeForKids": True})
        self.assertTrue(body["status"]["containsSyntheticMedia"])
        self.assertTrue(body["status"]["selfDeclaredMadeForKids"])

    def test_khong_khai_thi_khong_gui_field_synthetic(self):
        status = yu.build_body({"title": "t"})["status"]
        self.assertNotIn("containsSyntheticMedia", status)
        self.assertFalse(status["selfDeclaredMadeForKids"])


class TestEscapeQ(unittest.TestCase):
    def test_escape_dau_nhay(self):
        self.assertEqual(yu.escape_q("a'b"), "a\\'b")
        self.assertEqual(yu.escape_q("a\\b"), "a\\\\b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
