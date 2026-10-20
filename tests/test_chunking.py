from landrisk.extract import chunk_text, merge_chunks


def _paras(n, size=400):
    return "\n\n".join(f"para{i} " + "x" * size for i in range(n))


def test_short_text_is_one_chunk():
    assert chunk_text("short lease", max_chars=1000) == ["short lease"]


def test_long_text_splits_on_paragraphs_within_budget_and_covers_everything():
    text = _paras(40)
    chunks = chunk_text(text, max_chars=3000, overlap=200)
    assert len(chunks) > 1
    assert all(len(c) <= 3000 for c in chunks)
    for i in range(40):
        assert any(f"para{i} " in c for c in chunks)
    assert all(c.lstrip().startswith("para") or c.startswith("x") for c in chunks)


def test_giant_paragraph_is_hard_split():
    chunks = chunk_text("y" * 10_000, max_chars=3000, overlap=100)
    assert all(len(c) <= 3000 for c in chunks)
    assert sum(len(c) for c in chunks) >= 10_000


def f(value, conf, evidence="ev"):
    return {"value": value, "confidence": conf, "evidence": evidence}


def test_merge_takes_most_confident_non_null():
    out = merge_chunks([{"lessor": f(None, 0.9)}, {"lessor": f("Noack Farms", 0.95)}])
    assert out["lessor"]["value"] == "Noack Farms"
    assert out["lessor"]["confidence"] == 0.95


def test_merge_later_amendment_overrides_earlier_term():
    out = merge_chunks([
        {"royalty_rate": f("1/8", 0.97, "Lessor shall receive one-eighth (1/8) of all oil")},
        {"royalty_rate": f("3/16", 0.9, "EXHIBIT A. Notwithstanding Paragraph 3, the royalty shall be 3/16")},
    ])
    assert out["royalty_rate"]["value"] == "3/16"


def test_merge_disagreement_is_capped_for_review():
    out = merge_chunks([{"gross_acres": f("623.29", 0.95)}, {"gross_acres": f("640", 0.92)}])
    assert out["gross_acres"]["value"] == "623.29"
    assert out["gross_acres"]["confidence"] <= 0.6
    assert "disagree" in out["gross_acres"]["merge_note"]


def test_merge_same_value_in_different_words_is_not_a_disagreement():
    out = merge_chunks([{"royalty_rate": f("1/8", 0.95)}, {"royalty_rate": f("0.125", 0.9)}],
                       kinds={"royalty_rate": "decimal"})
    assert out["royalty_rate"]["confidence"] == 0.95
    assert not out["royalty_rate"].get("merge_note")


def test_merge_all_null_keeps_lowest_confidence():
    out = merge_chunks([{"depth_limit_ft": f(None, 0.9, None)}, {"depth_limit_ft": f(None, 0.7, None)}])
    assert out["depth_limit_ft"]["value"] is None
    assert out["depth_limit_ft"]["confidence"] == 0.7


def test_merge_keeps_every_conflicting_value_with_its_quote_and_part():
    out = merge_chunks([{"county": f("Milam", 0.9, "in the County of MILAM, State of Texas")},
                        {"county": f("Travis", 0.8, "recorded in Travis County")},
                        {"county": f("Milam", 0.95, "Milam County, Texas")}])
    alts = out["county"]["alternatives"]
    assert [a["value"] for a in alts] == ["Milam", "Travis"]
    assert alts[0] == {"value": "Milam", "evidence": "Milam County, Texas", "confidence": 0.95, "part": 3}
    assert alts[1]["part"] == 2


def test_merge_agreement_has_no_alternatives():
    out = merge_chunks([{"county": f("Milam", 0.9)}, {"county": f("milam", 0.95)}])
    assert "alternatives" not in out["county"]
