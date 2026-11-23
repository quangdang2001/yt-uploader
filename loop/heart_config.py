"""Cau hinh bien va nhanh cua "trai tim feedback" (vong thi nghiem hang dem). Chi doc, chi stdlib.

    from loop.heart_config import load_config, arms, enabled_vars, default, ConfigError

load_config kiem het: hang so du, moi bien du khoa va khong co khoa la, mac_dinh thuoc nhanh.
"""
from __future__ import annotations

import json
from pathlib import Path


class ConfigError(Exception):
    pass


# hang so goc: ten -> kieu (list = danh sach 2 so nguyen)
_ROOT_NUM = ("giai_doan", "ti_le_thu", "ti_le_thu_san", "san_thu", "n_thang", "n_loai", "thu_lai_ngay",
             "bo_qua_toi_da_ngay", "chi_thi_han_ngay", "thumb_tu_doi_gio_toi_da", "gio_hen_sau_nhan_gio")
_ROOT_OPTIONAL = ("ti_le_thu_san",)   # config cu khong co khoa nay van chay duoc
_ROOT_PAIR = ("cua_so_ctr_gio", "cua_so_view_gio", "gio_nhan_vps")
_ROOT_KEYS = set(_ROOT_NUM) | set(_ROOT_PAIR) | {"bien"}

_VAR_REQUIRED = {"nhanh", "mac_dinh", "can_cu", "ai_quyet", "do", "bat"}
_VAR_OPTIONAL = {"khong_kha_dung", "cot_ledger", "thu"}
_AI_QUYET = {"chu-de", "dung", "dang"}
_DO = {"views_7d", "apv_pct"}


def _check_var(name: str, v) -> None:
    if not isinstance(v, dict):
        raise ConfigError(f"bien {name}: phai la object")
    missing = _VAR_REQUIRED - set(v)
    if missing:
        raise ConfigError(f"bien {name}: thieu khoa {sorted(missing)}")
    extra = set(v) - _VAR_REQUIRED - _VAR_OPTIONAL
    if extra:
        raise ConfigError(f"bien {name}: khoa la {sorted(extra)}")
    nhanh = v["nhanh"]
    if (not isinstance(nhanh, list) or not nhanh or not all(isinstance(a, str) and a for a in nhanh)
            or len(set(nhanh)) != len(nhanh)):
        raise ConfigError(f"bien {name}: nhanh phai la danh sach chuoi khong rong, khong trung")
    if v["mac_dinh"] not in nhanh:
        raise ConfigError(f"bien {name}: mac_dinh {v['mac_dinh']!r} khong thuoc nhanh")
    if not isinstance(v["can_cu"], str) or not v["can_cu"].strip():
        raise ConfigError(f"bien {name}: can_cu rong")
    if v["ai_quyet"] not in _AI_QUYET:
        raise ConfigError(f"bien {name}: ai_quyet {v['ai_quyet']!r} sai")
    if v["do"] not in _DO:
        raise ConfigError(f"bien {name}: do {v['do']!r} sai")
    if not isinstance(v["bat"], bool):
        raise ConfigError(f"bien {name}: bat phai la true/false")
    if "khong_kha_dung" in v and not isinstance(v["khong_kha_dung"], bool):
        raise ConfigError(f"bien {name}: khong_kha_dung phai la true/false")
    if "thu" in v and not isinstance(v["thu"], bool):
        raise ConfigError(f"bien {name}: thu phai la true/false")
    cl = v.get("cot_ledger")
    if cl is not None and not isinstance(cl, str):
        raise ConfigError(f"bien {name}: cot_ledger phai la chuoi hoac null")


def load_config(path: Path) -> dict:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"khong doc duoc {path}: {e}") from e
    if not isinstance(cfg, dict):
        raise ConfigError("goc phai la object")
    extra = set(cfg) - _ROOT_KEYS
    if extra:
        raise ConfigError(f"khoa goc la {sorted(extra)}")
    for k in _ROOT_NUM:
        if k not in cfg and k in _ROOT_OPTIONAL:
            continue
        if k not in cfg:
            raise ConfigError(f"thieu hang so {k}")
        if isinstance(cfg[k], bool) or not isinstance(cfg[k], (int, float)):
            raise ConfigError(f"hang so {k} phai la so")
    for k in _ROOT_PAIR:
        if k not in cfg:
            raise ConfigError(f"thieu hang so {k}")
        p = cfg[k]
        if (not isinstance(p, list) or len(p) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in p)):
            raise ConfigError(f"hang so {k} phai la [so, so]")
    bien = cfg.get("bien")
    if not isinstance(bien, dict) or not bien:
        raise ConfigError("thieu bien")
    for name, v in bien.items():
        _check_var(name, v)
    return cfg


def _var(cfg: dict, var: str) -> dict:
    try:
        return cfg["bien"][var]
    except KeyError:
        raise ConfigError(f"khong co bien {var}") from None


def arms(cfg: dict, var: str) -> list[str]:
    return list(_var(cfg, var)["nhanh"])


def default(cfg: dict, var: str) -> str:
    return _var(cfg, var)["mac_dinh"]


def enabled_vars(cfg: dict) -> list[str]:
    return [n for n, v in cfg["bien"].items() if v["bat"] and not v.get("khong_kha_dung", False)]


def testable_vars(cfg: dict) -> list[str]:
    """Bien bat VA duoc lam bien thu. `thu: false` = bien van nam trong chi thi (nhanh thang / mac dinh) nhung
    khong bao gio duoc boc de thu: moi tuan chi co ~7 phim, thu nhieu bien cung luc thi khong bien nao du mau."""
    return [n for n in enabled_vars(cfg) if cfg["bien"][n].get("thu", True)]
