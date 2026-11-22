"""Test loop/book.py (chay tu goc repo):  python -m pytest -q loop/test_book.py"""
import csv
import multiprocessing
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from loop import book as bk
from loop import heart_config as hc

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "loop" / "heart_config.json"
NOW = datetime(2026, 11, 22, 6, 10, 5)


@pytest.fixture
def cfg():
    return hc.load_config(CFG_PATH)


def _worker(book: str, tag: int, n: int) -> None:
    cfg = hc.load_config(CFG_PATH)
    for i in range(n):
        bk.append(Path(book), {"tac_vu": "dung", "slug": f"p{tag}-{i}", "dong": "lineup"}, cfg,
                  datetime(2026, 11, 22, 6, 10, 5))


def test_append_and_read_roundtrip(tmp_path, cfg):
    book = tmp_path / bk.BOOK_NAME
    rid = bk.append(book, {"tac_vu": "ke-hoach", "slug": "colt_python", "dong": "lineup",
                           "tieu_de": "Giá, đắt", "du_doan_7d": "1000-5000"}, cfg, NOW)
    assert rid == "20261122061005-colt_python-ke-hoach"
    rows = bk.read(book)
    assert len(rows) == 1
    r = rows[0]
    assert list(r) == list(bk.BOOK_COLUMNS)
    assert r["id"] == rid and r["slug"] == "colt_python" and r["dong"] == "lineup"
    assert r["tieu_de"] == "Giá, đắt" and r["du_doan_7d"] == "1000-5000"
    assert r["thoi_diem"].startswith("2026-11-22")
    with open(book, encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == list(bk.BOOK_COLUMNS)


def test_rejects_unknown_arm(tmp_path, cfg):
    book = tmp_path / bk.BOOK_NAME
    with pytest.raises(bk.BookError) as e:
        bk.append(book, {"tac_vu": "dung", "slug": "x", "dong": "sai"}, cfg, NOW)
    msg = str(e.value)
    assert "dong" in msg and "sai" in msg
    for a in hc.arms(cfg, "dong"):
        assert a in msg
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "lung-tung", "slug": "x"}, cfg, NOW)
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "dung", "slug": "x", "bien_thu": "khong-co", "nhanh_thu": "a"}, cfg, NOW)
    with pytest.raises(bk.BookError) as e2:
        bk.append(book, {"tac_vu": "dung", "slug": "x", "bien_thu": "hook", "nhanh_thu": "sai"}, cfg, NOW)
    assert "gia-truoc" in str(e2.value)
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "dung", "slug": "x", "du_doan_7d": "nhieu"}, cfg, NOW)
    assert not book.exists()


def test_rejects_lech_without_reason(tmp_path, cfg):
    book = tmp_path / bk.BOOK_NAME
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "dung", "slug": "x", "lech": "1", "ly_do": ""}, cfg, NOW)
    bk.append(book, {"tac_vu": "dung", "slug": "x", "lech": "1", "ly_do": "chu kenh doi"}, cfg, NOW)
    assert bk.read(book)[0]["ly_do"] == "chu kenh doi"


def _row(tac_vu, slug, **kw):
    r = {c: "" for c in bk.BOOK_COLUMNS}
    r.update(tac_vu=tac_vu, slug=slug, **kw)
    return r


def test_merged_prefers_so_do_over_dung_over_ke_hoach():
    rows = [
        _row("so-do", "a", footage_pct="80", dong="xep-hang"),
        _row("dung", "a", footage_pct="70", dong="lineup", sec_per_cut="3"),
        _row("ke-hoach", "a", footage_pct="60", dong="gia-chuyen-hang", tieu_de="T", hook="gia-truoc"),
        _row("dung", "a", dong=""),  # rong khong de len
    ]
    m = bk.merged(rows)["a"]
    assert m["footage_pct"] == "80"
    assert m["dong"] == "xep-hang"
    assert m["sec_per_cut"] == "3"
    assert m["tieu_de"] == "T" and m["hook"] == "gia-truoc"


def test_merged_skips_doi_luat():
    rows = [_row("doi-luat", "a", dong="lineup"), _row("doi-luat", "b", dong="lineup"),
            _row("ke-hoach", "b", hook="gia-truoc")]
    m = bk.merged(rows)
    assert "a" not in m and m["b"]["dong"] == "" and m["b"]["hook"] == "gia-truoc"


def test_concurrent_appends_no_interleave(tmp_path):
    book = tmp_path / bk.BOOK_NAME
    ctx = multiprocessing.get_context("spawn")
    ps = [ctx.Process(target=_worker, args=(str(book), t, 50)) for t in (1, 2)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(120)
        assert p.exitcode == 0
    with open(book, encoding="utf-8", newline="") as f:
        raw = list(csv.reader(f))
    assert len(raw) == 101
    assert all(len(r) == len(bk.BOOK_COLUMNS) for r in raw)
    rows = bk.read(book)
    assert len(rows) == 100
    assert len({r["slug"] for r in rows}) == 100
    assert not Path(str(book) + ".lock").exists()


def test_stale_lock_is_broken(tmp_path, cfg):
    book = tmp_path / bk.BOOK_NAME
    lock = Path(str(book) + ".lock")
    lock.write_text("999 old", encoding="utf-8")
    old = time.time() - 301
    os.utime(lock, (old, old))
    t0 = time.time()
    bk.append(book, {"tac_vu": "dung", "slug": "x"}, cfg, NOW)
    assert time.time() - t0 < 10
    assert len(bk.read(book)) == 1
    assert not lock.exists()


def _cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "loop.book", "ghi", "--tim", str(tmp_path),
         "--config", str(CFG_PATH), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_cli_exit_codes(tmp_path):
    ok = _cli(tmp_path, "--tac-vu", "dung", "--slug", "thu", "--gia-tri", "dong=lineup",
              "--thu", "hook=gia-truoc", "--du-doan", "500-900", "--tieu-de", "Gia", "--so-do", "footage_pct=71")
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip().endswith("-thu-dung")
    rows = bk.read(tmp_path / bk.BOOK_NAME)
    assert rows[0]["bien_thu"] == "hook" and rows[0]["nhanh_thu"] == "gia-truoc"
    assert rows[0]["footage_pct"] == "71"
    bad = _cli(tmp_path, "--tac-vu", "dung", "--slug", "thu", "--gia-tri", "dong=sai")
    assert bad.returncode == 2
    assert "dong" in bad.stderr and "lineup" in bad.stderr
    assert len(bk.read(tmp_path / bk.BOOK_NAME)) == 1


def _stale(book: Path) -> Path:
    lock = Path(str(book) + ".lock")
    lock.write_text("999 old", encoding="utf-8")
    old = time.time() - 400
    os.utime(lock, (old, old))
    return lock


def test_two_waiters_on_stale_lock_never_overlap(tmp_path):
    import threading
    book = tmp_path / bk.BOOK_NAME
    _stale(book)
    state = {"in": 0, "max": 0}
    guard = threading.Lock()

    def waiter():
        for _ in range(15):
            with bk._Lock(book):
                with guard:
                    state["in"] += 1
                    state["max"] = max(state["max"], state["in"])
                time.sleep(0.005)
                with guard:
                    state["in"] -= 1

    ts = [threading.Thread(target=waiter) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert state["max"] == 1
    assert not Path(str(book) + ".lock").exists()
    assert not list(tmp_path.glob("*.stale.*"))


def test_unbreakable_lock_raises_after_deadline(tmp_path, cfg, monkeypatch):
    book = tmp_path / bk.BOOK_NAME
    _stale(book)

    def boom(*a, **k):
        raise PermissionError("no")
    monkeypatch.setattr(bk.os, "replace", boom)
    monkeypatch.setattr(bk, "LOCK_WAIT_S", 0.3)
    t0 = time.time()
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "dung", "slug": "x"}, cfg, NOW)
    assert 0.25 < time.time() - t0 < 5


def test_live_lock_times_out(tmp_path, cfg, monkeypatch):
    book = tmp_path / bk.BOOK_NAME
    Path(str(book) + ".lock").write_text("1 now", encoding="utf-8")
    monkeypatch.setattr(bk, "LOCK_WAIT_S", 0.2)
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "dung", "slug": "x"}, cfg, NOW)
    assert Path(str(book) + ".lock").exists()


def test_cli_several_pairs_per_flag_and_repeated_flags(tmp_path):
    a = _cli(tmp_path, "--tac-vu", "dung", "--slug", "multi", "--gia-tri", "dong=gia-chuyen-hang",
             "ten_quen=hang-lon", "--so-do", "footage_pct=55", "runtime_s=800")
    assert a.returncode == 0, a.stderr
    b = _cli(tmp_path, "--tac-vu", "so-do", "--slug", "rep", "--gia-tri", "dong=lineup",
             "--gia-tri", "ten_quen=hang-vua", "--so-do", "footage_pct=1", "--so-do", "sec_per_cut=2")
    assert b.returncode == 0, b.stderr
    r1, r2 = bk.read(tmp_path / bk.BOOK_NAME)
    assert (r1["dong"], r1["ten_quen"], r1["footage_pct"], r1["runtime_s"]) == (
        "gia-chuyen-hang", "hang-lon", "55", "800")
    assert (r2["dong"], r2["ten_quen"], r2["footage_pct"], r2["sec_per_cut"]) == (
        "lineup", "hang-vua", "1", "2")


def test_drop_needs_a_reason_and_is_tracked(tmp_path, cfg):
    book = tmp_path / bk.BOOK_NAME
    with pytest.raises(bk.BookError):
        bk.append(book, {"tac_vu": "bo", "slug": "x"}, cfg, NOW)
    bk.append(book, {"tac_vu": "ke-hoach", "slug": "x", "dong": "lineup"}, cfg, NOW)
    bk.append(book, {"tac_vu": "bo", "slug": "x", "ly_do": "CTR khuon 2,3%"}, cfg, NOW)
    bk.append(book, {"tac_vu": "ke-hoach", "slug": "y", "dong": "lineup"}, cfg, NOW)
    rows = bk.read(book)
    assert bk.dropped(rows) == {"x"}
    assert "x" not in bk.merged(rows) or bk.merged(rows)["x"]["dong"] == "lineup"
