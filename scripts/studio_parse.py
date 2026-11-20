"""Doc JSON cua YouTube Studio (giao dien tieng Viet) thanh mot dong snapshot.

Module thuan: chi dung thu vien chuan, khong import Playwright hay Google.
May nay import theo duong dan de parse lai raw/.

Moi so duoc tim theo accessibilityId cua the, khong theo vi tri trong mang.
"""
import re
import unicodedata

SNAPSHOT_COLS = (
    "captured_at_utc", "video_id", "age_hours", "period",
    "impressions", "impr_rounded", "ctr", "views", "unique_viewers", "avd_s",
    "apv_pct", "ret_30s", "ret_60s",
    "src_browse", "src_suggested", "src_search", "src_other",
    "thumb_hash", "test_running",
)

# accessibilityId cua cac the can doc
ID_IMPRESSIONS = "analytics_hub.VideoReachKeyMetricImpressionsCard.metric_values"
ID_IMPRESSIONS_CARD = "analytics_hub.VideoReachKeyMetricImpressionsCard"
ID_VIEWS = "analytics_hub.video_reach_views.left_section"
ID_CTR = "analytics_hub.video_reach_ctr.left_section"
ID_UNIQUE = "analytics_hub.video_reach_unique_viewers.left_section"
ID_AVD = "analytics_hub.FEanalytics_hub_video_engagement.key_metrics_average_view_duration.left_section"
ID_RET_METRICS = "analytics_hub.video_audience_retention_highlights_card.metrics"
ID_RET_TAB = "analytics_hub.video_audience_retention_highlights_card.tab_metrics"
ID_TABLE = "analytics_hub.basic_table_card"

# cac the cha ma trim_fixture.py giu lai (moi the chua cac id o tren)
KEEP_IDS = (
    ID_RET_METRICS,
    ID_RET_TAB,
    ID_TABLE,
    ID_IMPRESSIONS_CARD,
    "analytics_hub.video_reach_views",
    "analytics_hub.video_reach_ctr",
    "analytics_hub.video_reach_unique_viewers",
    "analytics_hub.FEanalytics_hub_video_engagement.key_metrics_average_view_duration",
)


class ParseError(Exception):
    pass


_SPACES = re.compile(r"[\s   ]+")
_SUFFIX = re.compile(r"^([0-9.,]+)\s*(tr|n|k|m|b)$", re.IGNORECASE)
_MULT = {"n": 1e3, "k": 1e3, "tr": 1e6, "m": 1e6, "b": 1e9}


def _clean(text):
    return _SPACES.sub(" ", str(text)).strip()


def is_abbreviated(text):
    return bool(_SUFFIX.match(_clean(text)))


def _plain(num):
    """So khong hau to: dau , hoac . truoc dung 3 chu so la phan nghin, con lai la phan thap phan."""
    if not re.fullmatch(r"[0-9.,]+", num):
        raise ParseError("not a number: %r" % num)
    seps = [c for c in num if c in ".,"]
    if not seps:
        return float(num)
    if len(set(seps)) == 2:  # "1.234,5" hoac "1,234.5": dau cuoi la thap phan
        dec = num[max(num.rfind("."), num.rfind(","))]
        thou = "," if dec == "." else "."
        return float(num.replace(thou, "").replace(dec, "."))
    sep = seps[0]
    parts = num.split(sep)
    if len(seps) > 1:  # "1,234,567"
        if all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            return float("".join(parts))
        raise ParseError("ambiguous number: %r" % num)
    if len(parts[1]) == 3 and 1 <= len(parts[0]) <= 3 and parts[0] != "0":
        return float(parts[0] + parts[1])
    return float(parts[0] + "." + parts[1])


def parse_number(text):
    """'159'->159, '12,345'->12345, '10,1%'->0.101, '1,6 N'->1600, '2,3 Tr'->2300000, '3:02'->182.0."""
    t = _clean(text)
    if t in ("", "-", "—", "–"):
        raise ParseError("empty or dash value: %r" % text)
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", t):
        secs = 0
        for part in t.split(":"):
            secs = secs * 60 + int(part)
        return float(secs)
    if t.endswith("%"):
        return round(_plain(t[:-1].strip().replace(" ", "")) / 100.0, 10)
    m = _SUFFIX.match(t)
    if m:
        num = m.group(1).replace(",", ".")
        if num.count(".") > 1:
            raise ParseError("ambiguous number: %r" % text)
        return round(float(num) * _MULT[m.group(2).lower()], 6)
    return _plain(t.replace(" ", ""))


def _nodes(obj):
    """Moi dict trong cay JSON, theo thu tu tai lieu."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _nodes(v)


def _find(payloads, aid):
    for p in payloads:
        for n in _nodes(p):
            if n.get("accessibilityId") == aid:
                return n
    return None


def _first_value(node):
    for n in _nodes(node):
        vm = n.get("simpleMetricViewModel")
        if isinstance(vm, dict):
            v = vm.get("value")
            if isinstance(v, dict) and "content" in v:
                return v["content"]
    return None


def _metric_text(payloads, aid, name):
    node = _find(payloads, aid)
    if node is None:
        raise ParseError("missing metric %s (card %s)" % (name, aid))
    text = _first_value(node)
    if text is None:
        raise ParseError("metric %s has no value in card %s" % (name, aid))
    return text


def _metric(payloads, aid, name):
    text = _metric_text(payloads, aid, name)
    try:
        return parse_number(text)
    except ParseError as e:
        raise ParseError("metric %s unreadable (%s)" % (name, e))


def _series_end(payloads):
    """Gia tri cuoi cua chuoi tich luy cua the Impressions: duong bieu do, hoac simpleMetric trong body."""
    card = _find(payloads, ID_IMPRESSIONS_CARD)
    if card is None:
        return None
    last = None
    for n in _nodes(card):
        lc = n.get("lineChartViewModel")
        if isinstance(lc, dict):
            for s in lc.get("series") or []:
                for dp in s.get("dataPoints") or []:
                    if isinstance(dp, dict) and isinstance(dp.get("measure"), (int, float)):
                        last = dp["measure"]
    if last is not None:
        return last
    for n in _nodes(card):
        if n.get("accessibilityId") == ID_IMPRESSIONS:
            continue
        vm = n.get("simpleMetricViewModel")
        if isinstance(vm, dict):
            v = (vm.get("value") or {}).get("content")
            try:
                last = parse_number(v)
            except ParseError:
                pass
    return last


def _impressions(payloads):
    text = _metric_text(payloads, ID_IMPRESSIONS, "impressions")
    try:
        head = parse_number(text)
    except ParseError as e:
        raise ParseError("metric impressions unreadable (%s)" % e)
    if not is_abbreviated(text):
        return int(round(head)), False
    end = _series_end(payloads)
    if end is not None and float(end) == int(end) and head > 0 and abs(end - head) <= 0.10 * head:
        return int(end), False
    return int(round(head)), True


_BIDI = re.compile("[‎‏‪-‮⁦-⁩]")


def _norm(text):
    """Chuoi de so nhan: bo ky tu bidi, gop khoang trang, NFC, chu thuong."""
    return unicodedata.normalize("NFC", _clean(_BIDI.sub("", str(text)))).lower()


def _content(obj, *path):
    """obj[path...]['content'] hoac None, khong bao gio nem loi."""
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    if isinstance(obj, dict):
        obj = obj.get("content")
    return obj if isinstance(obj, str) else None


def _percent(text):
    """'65,4%' -> 0.654; chuoi khong phai phan tram hoac khong doc duoc -> None."""
    if not isinstance(text, str) or not _clean(text).endswith("%"):
        return None
    try:
        return parse_number(text)
    except ParseError:
        return None


# --- retention: the video_audience_retention_highlights_card (tab Engagement) ---
_APV_LABELS = ("tỷ lệ phần trăm đã xem trung bình", "average percentage viewed")
_RET30_LABEL = re.compile(r"giây thứ 30\b|\b30 seconds?\b")


def _retention(payloads):
    """(apv_pct, ret_30s); thieu the hoac khong doc duoc thi None. ret_60s khong duoc Studio tra."""
    apv = ret30 = None
    for aid in (ID_RET_METRICS, ID_RET_TAB):
        for p in payloads:
            for n in _nodes(p):
                if n.get("accessibilityId") != aid:
                    continue
                for m in _nodes(n):
                    vm = m.get("simpleMetricViewModel")
                    if not isinstance(vm, dict):
                        continue
                    label = _norm(_content(vm, "subtitle") or "")
                    value = _percent(_content(vm, "value"))
                    if value is None:
                        continue
                    if apv is None and any(k in label for k in _APV_LABELS):
                        apv = value
                    elif ret30 is None and _RET30_LABEL.search(label):
                        ret30 = value
    return apv, ret30


# --- cac the basic_table_card (tab Reach), nhan dien theo tieu de ---
_TOTAL_LABELS = ("tổng tỷ lệ lưu lượng truy cập", "total traffic share")
_SRC_HEADLINES = {
    "src_search": ("cụm từ tìm kiếm trên youtube", "youtube search"),
    "src_suggested": ("nội dung giúp người xem tìm thấy", "suggested videos"),
    "src_other": ("trang web hoặc ứng dụng bên ngoài", "external websites"),
}
_AB_HEADLINES = ("thử nghiệm a/b", "a/b test", "test & compare", "test and compare")
_AB_DONE = ("đã hoàn tất", "completed", "đã dừng", "stopped")


def _table_cards(payloads):
    """(headline da chuan hoa, node) cua moi the basic_table_card."""
    for p in payloads:
        for n in _nodes(p):
            if n.get("accessibilityId") == ID_TABLE:
                head = _content(n, "topContent", "sectionHeaderViewModel", "headline")
                if head:
                    yield _norm(head), n


def _card_total(node):
    """Phan tram cua dong 'Tong ty le luu luong truy cap'; the 'Khong du du lieu' hoac khong co dong nay -> None."""
    for n in _nodes(node):
        item = n.get("listItemViewModel")
        if isinstance(item, dict) and _norm(_content(item, "title") or "") in _TOTAL_LABELS:
            return _percent(_content(item, "trailingLabel"))
    return None


_SINCE_PUBLISH = ("từ khi xuất bản", "since published")


def _traffic_shares(payloads):
    """Chi nhan the dung tieu de (bat dau bang) va co ky 'Tu khi xuat ban': ky khac thi None."""
    out = dict.fromkeys(_SRC_HEADLINES)
    for head, node in _table_cards(payloads):
        sub = _norm(_content(node, "topContent", "sectionHeaderViewModel", "sublabel") or "")
        if not any(s in sub for s in _SINCE_PUBLISH):
            continue
        for col, keys in _SRC_HEADLINES.items():
            if out[col] is None and head.startswith(keys):
                out[col] = _card_total(node)
    return out


def _test_running(payloads):
    """True neu co the 'Thu nghiem A/B' ma trang thai khong phai da hoan tat/da dung.
    Trang thai la hoac thieu dong trang thai tinh la dang chay (an toan: chay thi chan doi anh)."""
    for head, node in _table_cards(payloads):
        if head.startswith(_AB_HEADLINES):
            sub = _content(node, "topContent", "sectionHeaderViewModel", "sublabel") or ""
            parts = [_norm(s) for s in sub.split("•")]
            if not any(s in _AB_DONE for s in parts):
                return True
    return False


_PERIOD_LABELS = {"since_publish": "since_published"}


def _route_period(node):
    for n in _nodes(node):
        if n.get("key") == "time_period" and isinstance(n.get("value"), str):
            return n["value"]
    return None


def _payload_period(payloads):
    """time_period trong routeParams: the Impressions truoc, roi bat ky cho nao trong payload."""
    raw = None
    card = _find(payloads, ID_IMPRESSIONS_CARD)
    if card is not None:
        raw = _route_period(card)
    if raw is None:
        raw = _route_period(payloads)
    if raw is None:
        return None
    raw = raw.strip().lower()
    return _PERIOD_LABELS.get(raw, raw)


def parse_snapshot(payloads, video_id, period=None):
    """payloads: cac body JSON da decode cua ca hai tab, thu tu nao cung duoc.

    period: tuy chon; neu co thi phai khop time_period trong payload (since_publish ->
    "since_published"), neu khong khop thi ParseError. Khong co ca hai thi ParseError.
    captured_at_utc, age_hours, thumb_hash do noi goi dien. Cac cot khong co trong
    ban chup de None (khong bao gio la 0)."""
    if not payloads:
        raise ParseError("no payloads")
    found = _payload_period(payloads)
    if period is not None and found is not None and period != found:
        raise ParseError("period %r disagrees with payload time_period %r" % (period, found))
    period = found if found is not None else period
    if period is None:
        raise ParseError("period unknown: no time_period in payload and none given")
    row = dict.fromkeys(SNAPSHOT_COLS)
    row["video_id"] = video_id
    row["period"] = period
    row["impressions"], row["impr_rounded"] = _impressions(payloads)
    row["ctr"] = _metric(payloads, ID_CTR, "ctr")
    row["views"] = int(round(_metric(payloads, ID_VIEWS, "views")))
    row["unique_viewers"] = int(round(_metric(payloads, ID_UNIQUE, "unique_viewers")))
    row["avd_s"] = _metric(payloads, ID_AVD, "avd_s")
    if row["ctr"] > 1.0:
        raise ParseError("metric ctr out of range: %r" % row["ctr"])
    if row["avd_s"] < 0:
        raise ParseError("metric avd_s out of range: %r" % row["avd_s"])
    # cac cot sau tuy chon: thieu thi None, khong bao gio ParseError va khong bao gio tinh bu
    row["apv_pct"], row["ret_30s"] = _retention(payloads)
    row.update(_traffic_shares(payloads))  # src_browse khong co trong Studio: de None
    row["test_running"] = _test_running(payloads)
    return row
