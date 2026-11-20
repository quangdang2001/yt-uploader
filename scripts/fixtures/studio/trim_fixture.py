"""Cat bot ban chup Studio de commit: chi giu cay con duoi cac accessibilityId can doc,
bo moi khoa trackingParams / clickTrackingParams / *Token.

Dung: python trim_fixture.py [thu_muc_raw [thu_muc_dich]]
Mac dinh: raw_spike/ -> parse/ (raw_spike/ nam trong .gitignore, khong commit).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from studio_parse import ID_IMPRESSIONS_CARD, ID_TABLE, KEEP_IDS, _TOTAL_LABELS, _norm, _nodes  # noqa: E402

DROP_EXACT = {"trackingParams", "clickTrackingParams"}


def _drop(key, val, keep_series):
    if key in DROP_EXACT or key.endswith("Token"):
        return True
    if isinstance(val, str) and val.startswith(("http://", "https://")):
        return True  # moi URL (anh, thumbnail, lien ket) la du lieu ben thu ba
    if key in ("image", "leadingImage", "sources"):
        return True
    # chuoi diem du lieu: chi giu o the Impressions (parser doc cuoi chuoi tich luy)
    return key in ("dataPoints", "dataHovercards") and not keep_series


def _scrub(obj, keep_series=False):
    if isinstance(obj, dict):
        return {k: _scrub(v, keep_series) for k, v in obj.items() if not _drop(k, v, keep_series)}
    if isinstance(obj, list):
        return [_scrub(v, keep_series) for v in obj]
    return obj


def _trim_table(node):
    """The basic_table_card: chi giu id, tieu de + dong phu (ky/trang thai A/B) va dong tong luu luong.
    Moi dong khac (tu khoa, kenh, tieu de video, trang ngoai) la du lieu ben thu ba hoac ca nhan."""
    head = ((node.get("topContent") or {}).get("sectionHeaderViewModel")) or {}
    out = {"accessibilityId": node.get("accessibilityId"),
           "topContent": {"sectionHeaderViewModel": {k: _scrub(head[k]) for k in ("headline", "sublabel") if k in head}}}
    for n in _nodes(node):
        item = n.get("listItemViewModel")
        title = ((item or {}).get("title") or {}).get("content")
        if isinstance(title, str) and _norm(title) in _TOTAL_LABELS:
            out["bodyContent"] = {"linearLayoutViewModel": {"items": [
                {"listItemViewModel": {"title": _scrub(item["title"]), "trailingLabel": _scrub(item["trailingLabel"])}}]}}
            break
    return out


def _collect(obj, out):
    """Nut ngoai cung mang accessibilityId trong KEEP_IDS; khong di sau vao nut da giu."""
    if isinstance(obj, dict):
        aid = obj.get("accessibilityId")
        if aid == ID_TABLE:
            out.append(_trim_table(obj))
            return
        if aid in KEEP_IDS:
            out.append(_scrub(obj, keep_series=(aid == ID_IMPRESSIONS_CARD)))
            return
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)


def trim(payload):
    kept = []
    _collect(payload, kept)
    return {"trimmed": kept}


def main(argv):
    src = argv[1] if len(argv) > 1 else os.path.join(HERE, "raw_spike")
    dst = argv[2] if len(argv) > 2 else os.path.join(HERE, "parse")
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        if not name.endswith(".json") or "youtubei_v1_browse" not in name:
            continue
        with open(os.path.join(src, name), encoding="utf-8") as fh:
            data = json.load(fh)
        out = trim(data)
        with open(os.path.join(dst, name), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print(name, len(out["trimmed"]), "cards")


if __name__ == "__main__":
    main(sys.argv)
