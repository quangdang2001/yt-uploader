"""Test loop/heart_config.py (chay tu goc repo):  python -m pytest -q loop/test_heart_config.py"""
import copy
import json
from pathlib import Path

import pytest

from loop import heart_config as hc

SHIPPED = Path(__file__).resolve().parent / "heart_config.json"


def shipped() -> dict:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def write(tmp_path, cfg) -> Path:
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_loads_shipped_config():
    cfg = hc.load_config(SHIPPED)
    assert sorted(cfg["bien"]) == sorted(
        ["dong", "khuon_tieu_de", "ten_quen", "thumb_sang", "thumb_chu", "hook", "gio_dang"])
    assert len(cfg["bien"]) == 7
    # 2026-11-24: gio_dang bat (may tu hen gio) nhung khong lam bien thu; chi dong + khuon_tieu_de duoc thu
    assert len(hc.enabled_vars(cfg)) == 7
    assert hc.testable_vars(cfg) == ["dong", "khuon_tieu_de"]
    assert cfg["bien"]["gio_dang"]["bat"] is True and cfg["bien"]["gio_dang"]["thu"] is False
    assert hc.arms(cfg, "dong") == ["gia-chuyen-hang", "lineup", "xep-hang"]
    assert hc.default(cfg, "dong") == "gia-chuyen-hang"
    assert hc.default(cfg, "khuon_tieu_de") == "price-sacrifice"
    assert "moi" in hc.arms(cfg, "khuon_tieu_de")
    assert len(hc.arms(cfg, "khuon_tieu_de")) == 12
    assert cfg["bien"]["hook"]["do"] == "apv_pct"
    assert cfg["bien"]["gio_dang"]["khong_kha_dung"] is False


def test_thu_must_be_bool(tmp_path):
    cfg = shipped()
    cfg["bien"]["hook"]["thu"] = "no"
    with pytest.raises(hc.ConfigError):
        hc.load_config(write(tmp_path, cfg))


def test_default_must_be_an_arm(tmp_path):
    cfg = shipped()
    cfg["bien"]["dong"]["mac_dinh"] = "khong-co"
    with pytest.raises(hc.ConfigError):
        hc.load_config(write(tmp_path, cfg))


def test_constants_match_spec():
    cfg = hc.load_config(SHIPPED)
    assert cfg["giai_doan"] == 1
    assert cfg["ti_le_thu"] == 0.3
    assert cfg["san_thu"] == 0.1
    assert cfg["n_thang"] == 3
    assert cfg["n_loai"] == 6
    assert cfg["thu_lai_ngay"] == 30
    assert cfg["bo_qua_toi_da_ngay"] == 7
    assert cfg["cua_so_ctr_gio"] == [18, 36]
    assert cfg["cua_so_view_gio"] == [144, 216]
    assert cfg["chi_thi_han_ngay"] == 3
    assert cfg["thumb_tu_doi_gio_toi_da"] == 168
    assert cfg["gio_hen_sau_nhan_gio"] == 2
    assert cfg["gio_nhan_vps"] == [9, 21]


def test_unknown_key_rejected(tmp_path):
    cfg = shipped()
    cfg["bien"]["dong"]["la_hoac"] = 1
    with pytest.raises(hc.ConfigError):
        hc.load_config(write(tmp_path, cfg))


def test_unavailable_var_not_enabled(tmp_path):
    cfg = shipped()
    cfg["bien"]["hook"]["khong_kha_dung"] = True
    c = hc.load_config(write(tmp_path, cfg))
    assert "hook" not in hc.enabled_vars(c)
    assert "dong" in hc.enabled_vars(c)


def test_missing_constant_rejected(tmp_path):
    cfg = copy.deepcopy(shipped())
    del cfg["n_loai"]
    with pytest.raises(hc.ConfigError):
        hc.load_config(write(tmp_path, cfg))


def test_bad_json_and_bad_enum(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(hc.ConfigError):
        hc.load_config(p)
    cfg = shipped()
    cfg["bien"]["dong"]["do"] = "likes"
    with pytest.raises(hc.ConfigError):
        hc.load_config(write(tmp_path, cfg))
