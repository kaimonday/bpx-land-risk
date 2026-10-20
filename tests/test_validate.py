from landrisk.extract import validate

LEASE = ("Lessee shall pay Lessor a royalty of one-eighth (1/8) of all oil produced. "
         "EXHIBIT A. Notwithstanding the foregoing, the royalty shall be three-sixteenths (3/16).")


def test_two_royalty_rates_are_listed_as_alternatives_with_their_sentences():
    raw = {"royalty_rate": {"value": "3/16", "confidence": 0.95, "evidence": "the royalty shall be three-sixteenths (3/16)"}}
    row = next(r for r in validate(raw, LEASE) if r["field_name"] == "royalty_rate")
    assert row["confidence"] <= 0.6
    by_value = {a["value"]: a for a in row["alternatives"]}
    assert set(by_value) == {"0.125", "0.1875"}
    assert "one-eighth (1/8)" in by_value["0.125"]["evidence"]
    assert "(3/16)" in by_value["0.1875"]["evidence"]


def test_each_rate_is_quoted_from_the_sentence_that_states_it_alone():
    lease = ("4. Royalties. Lessee shall deliver to the credit of Lessor one-eighth (1/8) of all oil produced. "
             "EXHIBIT A. Notwithstanding Paragraph 4, the royalty shall be one-fifth (1/5) rather than one-eighth (1/8).")
    raw = {"royalty_rate": {"value": "1/5", "confidence": 0.95, "evidence": "the royalty shall be one-fifth (1/5)"}}
    row = next(r for r in validate(raw, lease) if r["field_name"] == "royalty_rate")
    by_value = {a["value"]: a["evidence"] for a in row["alternatives"]}
    assert "Lessee shall deliver" in by_value["0.125"]
    assert "one-fifth (1/5)" in by_value["0.2"]
