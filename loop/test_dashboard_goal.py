"""Khoi muc_tieu cua bang dieu khien:  python -m pytest -q loop/test_dashboard_goal.py"""
import json

from loop import dashboard as db

ROW = {"nhom": "kenh", "ma": "view_thang", "ten": "Lượt xem trong tháng", "hien_tai": 300000,
       "du_bao": 930000, "muc_tieu": 1000000, "dat": False}


def test_goal_block_read_from_directive(tmp_path):
    d = {"che_do": "san-video-troi", "nhan_ban": [{"video_id": "a"}],
         "muc_tieu": {"thang": "2026-12", "trang_thai": "thieu", "chi_so": [ROW], "view_thang": 300000}}
    (tmp_path / "chi-thi.json").write_text(json.dumps(d), encoding="utf-8")
    assert db.build_goal(tmp_path) == {"thang": "2026-12", "trang_thai": "thieu", "che_do": "san-video-troi",
                                       "chi_so": [ROW], "nhan_ban": [{"video_id": "a"}]}


def test_goal_block_empty_when_missing_or_without_goal(tmp_path):
    assert db.build_goal(tmp_path / "khong-co") == {}
    assert db.build_goal(tmp_path) == {}
    (tmp_path / "chi-thi.json").write_text(json.dumps({"muc": []}), encoding="utf-8")
    assert db.build_goal(tmp_path) == {}
    (tmp_path / "chi-thi.json").write_text("{broken", encoding="utf-8")
    assert db.build_goal(tmp_path) == {}
