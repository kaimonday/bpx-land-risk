import duckdb
import pytest

from landrisk import db
from landrisk.link_rrc import link_detail, link_overview, save_links, search_rrc


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.duckdb")
    db.init_raw()
    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    fields = {"lessor": "Noack Farms, LLC", "lessee": "Ardent 1, LLC", "county": "Milam", "effective_date": "2011-06-20"}
    c.executemany("""insert into raw.lease_extraction_fields (extraction_id, document_id, provider, model, field_name,
                     value, llm_confidence, confidence, evidence, validation_notes, extracted_at)
                     values ('x1', 'EDG-0001', 'groq', 'm', ?, ?, 0.9, 0.9, null, null, now())""", list(fields.items()))
    rrc = [("O", "01", "16355", "NOACK FARMS", "VON GONTEN", "MILAM"),
           ("O", "01", "08239", "NOACK -A-", "RUSK ENERGY", "MILAM"),
           ("O", "01", "99999", "SMITH", "OTHER", "MILAM"),
           ("O", "08", "18751", "DILLER", "OTHER", "SHACKELFORD")]
    for og, dist, no, name, op, county in rrc:
        c.execute("insert into raw.rrc_well_completion (oil_gas_code, district_no, lease_no, well_no, county_name) "
                  "values (?, ?, ?, '1', ?)", [og, dist, no, county])
        for ym in ("201208", "201301"):
            c.execute("""insert into raw.rrc_lease_cycle (oil_gas_code, district_no, lease_no, cycle_year_month,
                         operator_name, lease_name, lease_oil_prod_vol) values (?, ?, ?, ?, ?, ?, '100')""",
                      [og, dist, no, ym, op, name])
    yield c
    c.close()


def test_shortlist_is_scored_within_the_lease_county(con):
    d = link_detail(con, "EDG-0001")
    keys = [c["key"] for c in d["candidates"]]
    assert keys[0] == "O-01-16355"
    assert "O-08-18751" not in keys  # another county
    assert d["candidates"][0]["annual_boe"] == [(2012, 100.0), (2013, 100.0)]


def test_save_links_then_no_match_then_relink(con):
    save_links(con, "EDG-0001", ["O-01-16355", "O-01-08239"])
    assert link_detail(con, "EDG-0001")["linked"] == ["O-01-08239", "O-01-16355"]
    assert link_overview(con)[0]["status"] == "linked"

    save_links(con, "EDG-0001", [], no_match=True)
    assert link_detail(con, "EDG-0001")["linked"] == []
    assert link_overview(con)[0]["status"] == "no_match"

    save_links(con, "EDG-0001", ["O-01-16355"])
    assert link_overview(con)[0] == {**link_overview(con)[0], "status": "linked", "linked_count": 1}


def test_a_linked_lease_from_another_county_still_shows(con):
    save_links(con, "EDG-0001", ["O-08-18751"])
    d = link_detail(con, "EDG-0001")
    assert any(c["key"] == "O-08-18751" and c["linked"] for c in d["candidates"])


def test_search_by_name_or_number(con):
    assert [c["key"] for c in search_rrc(con, "smith")] == ["O-01-99999"]
    assert [c["key"] for c in search_rrc(con, "18751")] == ["O-08-18751"]
    assert search_rrc(con, "s") == []


def test_bad_key_is_rejected(con):
    with pytest.raises(ValueError):
        save_links(con, "EDG-0001", ["not-a-key"])


def test_suggest_keeps_confirmations_including_hand_added_rows(con, tmp_path, monkeypatch):
    import csv
    from landrisk import link_rrc
    monkeypatch.setattr(link_rrc, "_land", lambda doc: link_rrc.land_description(""))
    out = tmp_path / "cands.csv"
    link_rrc.suggest(top=1, out=out)
    rows = list(csv.DictReader(out.open()))
    rows[0]["confirm"] = "y"  # the top suggestion
    rows.append({**rows[0], "rank": "", "rrc_lease": "O-08-18751", "rrc_lease_name": "DILLER", "confirm": "y"})  # by hand
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, rows[0].keys()); w.writeheader(); w.writerows(rows)

    link_rrc.suggest(top=1, out=out)
    confirmed = {r["rrc_lease"] for r in csv.DictReader(out.open()) if r["confirm"] == "y"}
    assert confirmed == {"O-01-16355", "O-08-18751"}


def test_an_rrc_lease_already_linked_elsewhere_is_refused(con):
    # EDG-0004 (a 2013 top lease) and EDG-0001 (the 2011 lease) cover the same land;
    # counting NOACK FARMS' production for both broke the build.
    con.executemany("""insert into raw.lease_extraction_fields (extraction_id, document_id, provider, model, field_name,
                       value, llm_confidence, confidence, evidence, validation_notes, extracted_at)
                       values ('x2', 'EDG-0004', 'groq', 'm', ?, ?, 0.9, 0.9, null, null, now())""",
                    [("lessor", "Noack Farms, LLC"), ("county", "Milam")])
    save_links(con, "EDG-0001", ["O-01-16355"])
    with pytest.raises(ValueError, match="EDG-0001"):
        save_links(con, "EDG-0004", ["O-01-16355"])
    assert link_detail(con, "EDG-0004")["linked"] == []
    shared = next(c for c in link_detail(con, "EDG-0004")["candidates"] if c["key"] == "O-01-16355")
    assert shared["linked_to"] == ["EDG-0001"]


def test_relinking_the_same_lease_is_not_a_conflict(con):
    save_links(con, "EDG-0001", ["O-01-16355"])
    save_links(con, "EDG-0001", ["O-01-16355", "O-01-08239"])
    assert link_detail(con, "EDG-0001")["linked"] == ["O-01-08239", "O-01-16355"]
