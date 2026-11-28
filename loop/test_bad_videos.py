from loop import bad_videos as bv


def row(vid="v", ctr="0.02", impr="3000", age="30", verdict="WATCH", slug="s"):
    return {"video_id": vid, "ctr_latest": ctr, "impr_latest": impr, "age_h_latest": age, "verdict": verdict, "slug": slug}


def test_candidates_pick_bad_young_judged_videos_only():
    rows = [row("bad"), row("keep", verdict="KEEP"), row("good", ctr="0.08"), row("old", age="200"),
            row("young_few", age="2", impr="900"), row("young_many", age="2", impr="1600"), row("blank", ctr="")]
    got = [r["video_id"] for r in bv.candidates(rows, 0.07, 168, "")]
    assert got == ["bad", "young_many"]


def test_locked_title_and_alts(tmp_path):
    p = tmp_path / "UPLOAD.md"
    p.write_text("## Title (locked - x)\n\n```\nMain title\n```\n\n## Title alt 1\n\nnote\n\n```\nAlt one\n```\n\n## Description\n",
                 encoding="utf-8")
    assert bv.locked_title_and_alts(p) == ("Main title", ["Alt one"])
    assert bv.locked_title_and_alts(tmp_path / "none.md") == (None, [])


def test_winners_prefers_same_packaging_then_ctr():
    rows = [dict(row("a", ctr="0.062", impr="9000"), packaging="lineup", lane="2", live_title="A"),
            dict(row("b", ctr="0.080", impr="9000"), packaging="gia", lane="2", live_title="B"),
            dict(row("c", ctr="0.058", impr="9000"), packaging="lineup", lane="1", live_title="C")]
    assert [w["video_id"] for w in bv.winners(rows, "2", "lineup")] == ["a", "c", "b"]
