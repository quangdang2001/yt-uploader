"""So quyet dinh cua "trai tim feedback": CSV chi them dong, moi tac vu ghi quyet dinh theo tung video.

    from loop.book import append, read, merged, BookError
    python -m loop.book ghi --tim <dir> --config loop/heart_config.json --tac-vu dung --slug <s> ...

Ghi co khoa file (<book>.lock, O_CREAT|O_EXCL); khoa cu hon 300 giay thi bi pha. Chi stdlib.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from loop.heart_config import ConfigError, arms, load_config

BOOK_NAME = "so-quyet-dinh.csv"
BOOK_COLUMNS = (
    "id", "thoi_diem", "tac_vu", "slug", "phien_ban_chi_thi", "dong", "khuon_tieu_de", "ten_quen",
    "thumb_sang", "thumb_chu", "hook", "gio_dang", "bien_thu", "nhanh_thu", "lech", "ly_do",
    "du_doan_7d", "tieu_de", "gio_hen", "footage_pct", "sec_per_cut", "render_pct", "thumb_brightness",
    "script_words", "runtime_s", "ghi_chu",
)
TAC_VU = ("ke-hoach", "dung", "so-do", "doi-luat", "nhan-ban", "bo")
_PRIORITY = {"ke-hoach": 0, "dung": 1, "so-do": 2}
_NO_MERGE = {"id", "thoi_diem", "tac_vu"}

LOCK_WAIT_S = 30.0
LOCK_STALE_S = 300.0
_RANGE = re.compile(r"^\d+-\d+$")


class BookError(Exception):
    pass


def _bad_arm(var: str, val: str, cfg: dict) -> BookError:
    return BookError(f"gia tri {val!r} cua bien {var} sai; nhanh hop le: {', '.join(arms(cfg, var))}")


def _validate(row: dict, cfg: dict) -> None:
    unknown = set(row) - set(BOOK_COLUMNS)
    if unknown:
        raise BookError(f"cot la {sorted(unknown)}; cot hop le: {', '.join(BOOK_COLUMNS)}")
    if row.get("tac_vu") not in TAC_VU:
        raise BookError(f"tac vu {row.get('tac_vu')!r} sai; hop le: {', '.join(TAC_VU)}")
    if not str(row.get("slug", "")).strip():
        raise BookError("slug rong")
    for var in cfg["bien"]:
        val = str(row.get(var, "") or "")
        if val and val not in arms(cfg, var):
            raise _bad_arm(var, val, cfg)
    bien, nhanh = str(row.get("bien_thu", "") or ""), str(row.get("nhanh_thu", "") or "")
    if bien or nhanh:
        if bien not in cfg["bien"]:
            raise BookError(f"bien_thu {bien!r} khong phai bien; bien hop le: {', '.join(cfg['bien'])}")
        if nhanh not in arms(cfg, bien):
            raise _bad_arm(bien, nhanh, cfg)
    dd = str(row.get("du_doan_7d", "") or "")
    if dd and not _RANGE.match(dd):
        raise BookError(f"du_doan_7d {dd!r} phai co dang lo-hi, vi du 1000-5000")
    if str(row.get("lech", "") or "") and not str(row.get("ly_do", "") or "").strip():
        raise BookError("lech co ma ly_do rong: ghi ly do khi lech khoi chi thi")
    if row.get("tac_vu") == "bo" and not str(row.get("ly_do", "") or "").strip():
        raise BookError("bo co ma ly_do rong: ghi so lieu nao lam chu de het phu hop")


def dropped(rows: list[dict]) -> set[str]:
    """Slug bi bo khoi hang cho: dong `bo` dung sau dong `ke-hoach` cuoi cung va chua co dong `dung`."""
    last: dict[str, str] = {}
    built = set()
    for r in rows:
        tv, slug = r.get("tac_vu"), r.get("slug")
        if tv in ("ke-hoach", "bo") and slug:
            last[slug] = tv
        elif tv == "dung" and slug:
            built.add(slug)
    return {s for s, tv in last.items() if tv == "bo" and s not in built}


class _Lock:
    def __init__(self, book: Path, wait_s: float | None = None):
        self.path = Path(str(book) + ".lock")
        self.wait_s = LOCK_WAIT_S if wait_s is None else wait_s

    def _break_if_stale(self) -> None:
        """Pha khoa cu bang rename (chi mot nguoi thang), khong unlink truc tiep."""
        try:
            if time.time() - self.path.stat().st_mtime <= LOCK_STALE_S:
                return
        except OSError:
            return
        grave = Path(f"{self.path}.stale.{os.getpid()}")
        try:
            os.replace(self.path, grave)
        except OSError:
            return  # nguoi khac da pha, hoac Windows tu choi: thu lai o vong sau
        try:
            if time.time() - grave.stat().st_mtime <= LOCK_STALE_S:
                # vua rename nham khoa moi cua nguoi khac: tra lai neu cho con trong
                try:
                    os.link(grave, self.path)
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            try:
                grave.unlink()
            except OSError:
                pass

    def __enter__(self):
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (FileExistsError, PermissionError):  # Windows: PermissionError khi file cho xoa
                self._break_if_stale()
                if time.monotonic() > deadline:
                    raise BookError(f"cho khoa {self.path} qua {int(self.wait_s)} giay") from None
                time.sleep(0.02)
                continue
            try:
                os.write(fd, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n".encode())
            except BaseException:
                os.close(fd)
                try:
                    self.path.unlink()
                except OSError:
                    pass
                raise
            os.close(fd)
            return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def append(book: Path, row: dict, cfg: dict, now: datetime) -> str:
    book = Path(book)
    _validate(row, cfg)
    rid = f"{now.strftime('%Y%m%d%H%M%S')}-{row['slug']}-{row['tac_vu']}"
    out = {c: str(row.get(c, "") or "") for c in BOOK_COLUMNS}
    out["id"] = rid
    out["thoi_diem"] = now.isoformat(timespec="seconds")
    book.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(book):
        new = not book.exists() or book.stat().st_size == 0
        with open(book, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=BOOK_COLUMNS)
            if new:
                w.writeheader()
            w.writerow(out)
            f.flush()
            os.fsync(f.fileno())
    return rid


def read(book: Path) -> list[dict]:
    book = Path(book)
    if not book.exists():
        return []
    with open(book, encoding="utf-8", newline="") as f:
        return [{c: (r.get(c) or "") for c in BOOK_COLUMNS} for r in csv.DictReader(f)]


def merged(rows: list[dict]) -> dict[str, dict]:
    """Moi slug: gia tri khong rong moi nhat cua tung cot; so-do > dung > ke-hoach; bo doi-luat."""
    by_slug: dict[str, list[tuple[int, int, dict]]] = {}
    for i, r in enumerate(rows):
        pr = _PRIORITY.get(r.get("tac_vu", ""))
        if pr is None or not r.get("slug"):
            continue
        by_slug.setdefault(r["slug"], []).append((pr, i, r))
    out: dict[str, dict] = {}
    for slug, items in by_slug.items():
        m = {c: "" for c in BOOK_COLUMNS if c not in _NO_MERGE}
        for _, _, r in sorted(items, key=lambda t: (t[0], t[1])):
            for c in m:
                if r.get(c):
                    m[c] = r[c]
        out[slug] = m
    return out


def _kv(items: list[str] | None, flag: str) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise BookError(f"{flag} {it!r} phai co dang khoa=gia_tri")
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m loop.book")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("ghi")
    g.add_argument("--tim", required=True)
    g.add_argument("--config", required=True)
    g.add_argument("--tac-vu", required=True)
    g.add_argument("--slug", required=True)
    g.add_argument("--gia-tri", action="extend", nargs="+")
    g.add_argument("--thu")
    g.add_argument("--du-doan")
    g.add_argument("--lech", action="store_true")
    g.add_argument("--ly-do", default="")
    g.add_argument("--tieu-de", default="")
    g.add_argument("--gio-hen", default="")
    g.add_argument("--so-do", action="extend", nargs="+")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(Path(a.config))
        row = {"tac_vu": a.tac_vu, "slug": a.slug}
        row.update(_kv(a.gia_tri, "--gia-tri"))
        row.update(_kv(a.so_do, "--so-do"))
        if a.thu:
            if "=" not in a.thu:
                raise BookError(f"--thu {a.thu!r} phai co dang bien=nhanh")
            row["bien_thu"], row["nhanh_thu"] = a.thu.split("=", 1)
        if a.du_doan:
            row["du_doan_7d"] = a.du_doan
        if a.lech:
            row["lech"] = "1"
        if a.ly_do:
            row["ly_do"] = a.ly_do
        if a.tieu_de:
            row["tieu_de"] = a.tieu_de
        if a.gio_hen:
            row["gio_hen"] = a.gio_hen
        rid = append(Path(a.tim) / BOOK_NAME, row, cfg, datetime.now())
    except (BookError, ConfigError) as e:
        print(f"LOI so quyet dinh: {e}", file=sys.stderr)
        return 2
    print(rid)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(_main(sys.argv[1:]))
