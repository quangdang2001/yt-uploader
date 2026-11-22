"""Khoi muc_tieu cua bang dieu khien:  python -m pytest -q loop/test_dashboard_goal.py"""
import json

from loop import dashboard as db

ROW = {"nhom": "kenh", "ma": "view_thang", "ten": "Lượt xem trong tháng", "hien_tai": 300000,
       "du_bao": 930000, "muc_tieu": 1000000, "dat": False}


def test_goal_block_read_from_directive(tmp_path):
    d = {"che_do": "san-video-troi", "nhan_ban": [{"video_id": "a"}],
         "muc_tieu": {"thang": "2026-12", "trang_thai": "thieu", "chi_so": [ROW], "view_thang": 300000}}
    (tmp_path / "chi-thi.json").write_text(json.dumps(d), encoding="utf-8")
    out = db.build_goal(tmp_path)
    assert out == {"thang": "2026-12", "trang_thai": "thieu", "che_do": "san-video-troi",
                   "chi_so": [ROW], "nhan_ban": [{"video_id": "a"}], "view_can_moi_ngay": None,
                   "sap_toi": False, "ngay_co_so": None, "ngay_trong_thang": None,
                   "thang_muc_tieu": "2026-12"}


def test_goal_block_pacing_fields(tmp_path):
    d = {"che_do": "san-video-troi", "nhan_ban": [],
         "muc_tieu": {"thang": "2026-11", "trang_thai": "thieu", "chi_so": [], "view_can_moi_ngay": 100399,
                      "ngay_co_so": 20, "ngay_trong_thang": 30,
                      "muc_tieu": {"sap_toi": True, "thang": "2026-11", "thang_moc": "2026-12"}}}
    (tmp_path / "chi-thi.json").write_text(json.dumps(d), encoding="utf-8")
    out = db.build_goal(tmp_path)
    assert (out["view_can_moi_ngay"], out["sap_toi"], out["ngay_co_so"], out["ngay_trong_thang"]) == \
        (100399, True, 20, 30)
    assert out["thang"] == "2026-11" and out["thang_muc_tieu"] == "2026-12"


def test_goal_block_empty_when_missing_or_without_goal(tmp_path):
    assert db.build_goal(tmp_path / "khong-co") == {}
    assert db.build_goal(tmp_path) == {}
    (tmp_path / "chi-thi.json").write_text(json.dumps({"muc": []}), encoding="utf-8")
    assert db.build_goal(tmp_path) == {}
    (tmp_path / "chi-thi.json").write_text("{broken", encoding="utf-8")
    assert db.build_goal(tmp_path) == {}
