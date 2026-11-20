"""Doc JSON cac tab phan tich KENH (analytics/hub_*) thanh mot tai lieu JSON moi ngay.

Module thuan: chi thu vien chuan + studio_parse. Khong Playwright, khong Google.

Moi the (card) duoc doc chung mot cach, theo accessibilityId, khong theo vi tri:
  id (rut gon), tieu de, ky (sublabel), gia tri chinh, cac chi so phu, cac dong (nhan + gia tri),
  va cac chuoi bieu do theo ngay (lineChartViewModel).
Tab Nghien cuu (hub_research) la luoi video cua kenh khac: doc tung lockupViewModel.
"""
import re
import unicodedata
from datetime import datetime, timezone

import studio_parse as sp

DOC_VERSION = 1
CHANNEL_TABS = ("hub_overview", "hub_content", "hub_audience", "hub_revenue")
RESEARCH_FEEDS = {
    "research_outliers": "LANDING_PAGE_FEED_TYPE_CHANNEL_RELEVANT_OUTLIERS",
    "research_audience": "LANDING_PAGE_FEED_TYPE_CHANNEL_AUDIENCE_OVERLAP",
    "research_recent": "LANDING_PAGE_FEED_TYPE_CHANNEL_RELEVANT_AND_RECENT",
}
# Cay con bo qua khi doc the: chu thich tren bieu do (video da dang) va the noi khi re chuot.
_SKIP_KEYS = ("annotations", "hovercard", "hoverCardViewModel", "tooltip", "tooltipViewModel")
_PREFIX_RE = re.compile(r"^(analytics_hub\.)?(FEanalytics_hub_channel_[a-z]+\.)?")
_UNIT_RE = re.compile(r"\s*(US\$|\$|lần|người đăng ký|lượt xem|subscribers|views|x)\s*$", re.I)


def short_id(aid: str) -> str:
    return _PREFIX_RE.sub("", aid)


def _walk(obj, skip=_SKIP_KEYS):
    """Moi dict theo thu tu tai lieu, bo qua cac cay con trong `skip`."""
    if isinstance(obj, dict):
        yield obj
        for k, v in obj.items():
            if k in skip:
                continue
            yield from _walk(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, skip)


def _txt(text):
    return sp._clean(sp._BIDI.sub("", text)) if isinstance(text, str) else None


def parse_value(text):
    """'447,17 US$' -> 447.17, '+235' -> 235, '-3' -> -3, '6,9%' -> 0.069, '21 lần' -> 21,
    '3:38' -> 218, '129,2 N' -> 129200; chu (vd 'Rất cao', 'Nam') -> None."""
    t = _txt(text)
    if not t:
        return None
    sign = 1
    if t[0] in "+-−":
        sign = -1 if t[0] in "-−" else 1
        t = t[1:].strip()
    t = _UNIT_RE.sub("", t)
    try:
        return sign * sp.parse_number(t)
    except sp.ParseError:
        return None


def _utc_day(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _series(node):
    out = []
    for n in _walk(node):
        lc = n.get("lineChartViewModel")
        if not isinstance(lc, dict):
            continue
        for s in lc.get("series") or []:
            pts = []
            for dp in s.get("dataPoints") or []:
                if isinstance(dp, dict) and isinstance(dp.get("domain"), (int, float)) \
                        and isinstance(dp.get("measure"), (int, float)):
                    pts.append([_utc_day(dp["domain"]), dp["measure"]])
            if pts:
                out.append({"label": _txt(s.get("label")) or "", "points": pts})
    return out


_LEVEL_RE = re.compile(r"_(\d+)$")


def _heatmap(node):
    """Bang 'Thoi diem khan gia xem YouTube': [[thu, gio, muc mau]] (muc cao = nhieu khan gia)."""
    out = []
    for n in _walk(node, skip=()):
        hm = n.get("audienceOnlineHeatmapViewModel")
        if not isinstance(hm, dict):
            continue
        for day in hm.get("days") or []:
            label = _txt(day.get("dayLabelMessage"))
            for i, cell in enumerate(day.get("cells") or []):
                ref = ((cell.get("color") or {}).get("ytSysColorBaselineReference") or "")
                m = _LEVEL_RE.search(ref)
                out.append([label, i, int(m.group(1)) if m else 0])
    return out


def _card(node, cid):
    head = sp._content(node, "topContent", "sectionHeaderViewModel", "headline")
    sub = sp._content(node, "topContent", "sectionHeaderViewModel", "sublabel")
    metrics, rows = [], []
    for n in _walk(node):
        vm = n.get("simpleMetricViewModel")
        if isinstance(vm, dict):
            vt = sp._content(vm, "value")
            if vt is not None:
                metrics.append({"label": _txt(sp._content(vm, "subtitle") or sp._content(vm, "title") or ""),
                                "text": _txt(vt), "value": parse_value(vt)})
        it = n.get("listItemViewModel")
        if isinstance(it, dict):
            title = _txt(sp._content(it, "title"))
            trail = _txt(sp._content(it, "trailingLabel"))
            if title and not any(r["label"] == title and r["text"] == trail for r in rows):
                rows.append({"label": title, "text": trail, "value": parse_value(trail) if trail else None})
        fv = n.get("funnelViewModel")
        if isinstance(fv, dict):
            for r in fv.get("rows") or []:
                label, vt = _txt(sp._content(r, "label")), _txt(sp._content(r, "formattedValue"))
                if label and vt:
                    rows.append({"label": label, "text": vt, "value": parse_value(vt)})
    series = _series(node)
    heat = _heatmap(node)
    if not (metrics or rows or series or heat):
        return None
    main = metrics[0] if metrics else None
    card = {
        "id": cid,
        "headline": _txt(head),
        "period": _txt(sub),
        "value_text": main["text"] if main else None,
        "value": main["value"] if main else None,
        "metrics": metrics[1:] if main else [],
        "rows": rows,
        "series": series,
    }
    if heat:
        card["heatmap"] = heat
    return card


# The co nhieu ban cung id (cac nut chon ben trong the), theo thu tu hien tren trang.
VARIANT_NAMES = {
    "how_viewers_find_your_videos": ("tong_the", "ben_ngoai", "tim_kiem_youtube", "video_de_xuat",
                                     "danh_sach_phat"),
    "audience_key_moments": ("doan_mo_dau", "moc_sut_giam"),
}
# The cap nhat truc tiep (so lieu 48 gio): giu ban moi nhat, khong tao bien the.
_LIVE_MARK = "updating_live"


def _slug(text):
    t = unicodedata.normalize("NFKD", sp._norm(text or "").replace("đ", "d"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t or "the"


def parse_cards(bodies):
    """Moi cardViewModel trong mot tab. Id = accessibilityId rut gon (hoac tieu de neu thieu).
    The trung id: ban giong het bo qua; ban khac la mot bien the (id#ten hoac id#2, #3...)."""
    cards, seen = [], {}
    for body in bodies:
        for n in sp._nodes(body):
            cv = n.get("cardViewModel")
            if not isinstance(cv, dict):
                continue
            aid = cv.get("accessibilityId")
            base = short_id(aid) if isinstance(aid, str) and aid else \
                "the_" + _slug(sp._content(cv, "topContent", "sectionHeaderViewModel", "headline"))
            card = _card(cv, base)
            if card is None:
                continue
            same = seen.setdefault(base, [])
            if same and _LIVE_MARK in base:
                cards[cards.index(same[0])] = card
                same[0] = card
                continue
            body_of = lambda c: (c["value_text"], c["rows"], c["series"], c.get("heatmap"), c["metrics"])
            if any(body_of(c) == body_of(card) for c in same):
                continue
            if same:
                names = VARIANT_NAMES.get(base, ())
                k = len(same)
                card["id"] = f"{base}#{names[k]}" if k < len(names) else f"{base}#{k + 1}"
                if k == 1 and names and same[0]["id"] == base:
                    same[0]["id"] = f"{base}#{names[0]}"
            same.append(card)
            cards.append(card)
    return cards


def _parts(row):
    return [_txt(sp._content(p, "text")) for p in (row.get("metadataParts") or [])]


def parse_research(bodies):
    """Video trong luoi Nghien cuu: id, tieu de, kenh, so dang ky, view, tuoi, boi so 'lan', do dai."""
    items, seen = [], set()
    for body in bodies:
        for n in sp._nodes(body):
            lk = n.get("lockupViewModel")
            if not isinstance(lk, dict) or lk.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
                continue
            vid = lk.get("contentId")
            if not isinstance(vid, str) or vid in seen:
                continue
            meta = ((lk.get("metadata") or {}).get("lockupMetadataViewModel") or {})
            title = _txt(sp._content(meta, "title"))
            rows = (((meta.get("metadata") or {}).get("contentMetadataViewModel") or {})
                    .get("metadataRows") or [])
            r0 = _parts(rows[0]) if len(rows) > 0 else []
            r1 = _parts(rows[1]) if len(rows) > 1 else []
            badge = None
            for row in rows:
                for b in row.get("badges") or []:
                    t = (b.get("badgeViewModel") or {}).get("badgeText")
                    if isinstance(t, str):
                        badge = _txt(t)
            dur = None
            for m in sp._nodes(lk.get("contentImage") or {}):
                tb = m.get("thumbnailBadgeViewModel")
                if isinstance(tb, dict) and isinstance(tb.get("text"), str):
                    dur = _txt(tb["text"])
                    break
            if not title:
                continue
            seen.add(vid)
            items.append({
                "video_id": vid, "title": title,
                "channel": r0[0] if len(r0) > 0 else None,
                "subs_text": r0[1] if len(r0) > 1 else None,
                "subs": parse_value(r0[1]) if len(r0) > 1 else None,
                "views_text": r1[0] if len(r1) > 0 else None,
                "views": parse_value(r1[0]) if len(r1) > 0 else None,
                "age_text": r1[1] if len(r1) > 1 else None,
                "outlier_text": badge,
                "outlier": parse_value(badge) if badge else None,
                "duration": dur,
                "duration_s": parse_value(dur) if dur else None,
            })
    return items


def parse_channel(tab_bodies: dict, captured_at_utc: str) -> dict:
    """tab_bodies: {ten tab: [body JSON,...]}. Tab thieu hoac rong thi bo qua (khong loi)."""
    doc = {"version": DOC_VERSION, "captured_at_utc": captured_at_utc, "tabs": {}, "research": {}}
    for tab in CHANNEL_TABS:
        if tab_bodies.get(tab):
            doc["tabs"][tab] = parse_cards(tab_bodies[tab])
    for key in RESEARCH_FEEDS:
        if tab_bodies.get(key):
            doc["research"][key] = parse_research(tab_bodies[key])
    return doc


def card_count(doc: dict) -> int:
    return sum(len(v) for v in doc.get("tabs", {}).values())
