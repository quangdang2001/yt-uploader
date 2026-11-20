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
from studio_parse import KEEP_IDS  # noqa: E402

DROP_EXACT = {"trackingParams", "clickTrackingParams"}


def _drop(key):
    return key in DROP_EXACT or key.endswith("Token")


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if not _drop(k)}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _collect(obj, out):
    """Nut ngoai cung mang accessibilityId trong KEEP_IDS; khong di sau vao nut da giu."""
    if isinstance(obj, dict):
        if obj.get("accessibilityId") in KEEP_IDS:
            out.append(_scrub(obj))
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
