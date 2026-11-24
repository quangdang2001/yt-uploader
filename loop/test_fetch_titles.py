"""loop.fetch keo studio/title-alts/ ve cache.  python -m pytest -q loop/test_fetch_titles.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.append(str(SCRIPTS))

import studio_store as ss  # noqa: E402
import youtube_uploader as yu  # noqa: E402
from test_studio_store import FakeDrive  # noqa: E402

from loop import fetch as lf  # noqa: E402
from loop import titles as tl  # noqa: E402


class FetchTitleAltsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "cache"
        self.drive = FakeDrive()
        self.folders = ss.studio_folders(self.drive, "CHAN")

    def test_same_folder_name_on_both_sides(self):
        self.assertEqual(lf.TITLE_ALTS_DIR, tl.ALTS_DIR)

    def test_no_folder_is_fine(self):
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(tl.load_alts(self.cache), {})

    def test_alternates_are_pulled_and_refreshed(self):
        tdir = yu.ensure_folder(self.drive, "title-alts", self.folders["studio"])
        doc = {"base": "gunA", "main": "Old Title", "alt1": "New Title"}
        fid = self.drive._add("gunA.json", [tdir], "application/json", json.dumps(doc).encode())
        self.drive._add("notes.txt", [tdir], "text/plain", b"ignore me")
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(tl.load_alts(self.cache), {"gunA": {"main": "Old Title", "alt1": "New Title"}})
        self.assertFalse((self.cache / "title-alts" / "notes.txt").exists())
        doc["alt2"] = "Third Title"
        self.drive.items[fid]["data"] = json.dumps(doc).encode()
        self.drive.items[fid]["modifiedTime"] = "2027-01-01T00:00:00Z"
        lf.fetch(self.drive, "CHAN", self.cache)
        self.assertEqual(tl.load_alts(self.cache)["gunA"]["alt2"], "Third Title")


if __name__ == "__main__":
    unittest.main()
