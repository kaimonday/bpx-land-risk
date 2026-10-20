from pathlib import Path

from landrisk.ingest_rrc import Source, leases_in_counties
from landrisk.link_rrc import name_score, score_candidate


def _write(dir_: Path, table: str, header: list[str], rows: list[list[str]]) -> None:
    lines = ["}".join(header)] + ["}".join(r) for r in rows]
    (dir_ / f"{table}_DATA_TABLE.dsv").write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_leases_in_counties_matches_padded_county_names(tmp_path):
    _write(tmp_path, "OG_WELL_COMPLETION", ["OIL_GAS_CODE", "DISTRICT_NO", "LEASE_NO", "WELL_NO", "COUNTY_NAME"],
           [["O", "03", "012345", "1", "MILAM     "], ["G", "03", "222222", "1", "CALDWELL"],
            ["O", "08", "999999", "1", "LOVING"]])
    keys = leases_in_counties(Source(dir_path=tmp_path), ["Milam", "caldwell"])
    assert keys == {("O", "03", "012345"), ("G", "03", "222222")}


def test_name_score_ignores_entity_words_and_rewards_surnames():
    assert name_score("NOACK FARMS, LLC", "NOACK FARMS") == 1.0
    assert name_score("Henry J. Janssen Jr.", "JANSSEN UNIT") > 0.4
    assert name_score("The Smith Family Trust", "JONES ESTATE") == 0.0


def test_candidate_with_matching_names_and_later_production_outranks_one_without():
    lease = {"lessor": "Noack Farms, LLC", "lessee": "Ardent 1, LLC", "effective_date": "2011-06-20"}
    good = {"lease_name": "NOACK FARMS", "operator_name": "ARDENT 1 LLC", "first_prod_ym": "201203"}
    before = {"lease_name": "NOACK FARMS", "operator_name": "OTHER CO", "first_prod_ym": "199501"}
    unrelated = {"lease_name": "SMITH", "operator_name": "OTHER CO", "first_prod_ym": "201203"}
    s_good, s_before, s_unrelated = (score_candidate(lease, c)[0] for c in (good, before, unrelated))
    assert s_good > s_before > s_unrelated


def test_person_lessors_match_on_surname_not_first_name():
    # Real false positives from the Caldwell, Shackelford and Milam candidates.
    assert name_score("Dorothy Hinds; Frank Hinds", "HINDS, F. C.") == 1.0
    assert name_score("Dorothy Hinds; Frank Hinds", "FRANK") == 0.0
    assert name_score("Carolyn Travis", "COOKE, TRAVIS C. ET AL") == 0.0
    assert name_score("Judy K. Rouse", "MEISTER, JUDY C.") == 0.0
    assert name_score("Cynthia Williams Price", "WILLIAMS, J. T.") == 0.0
    assert name_score("A. A. McVOY Jr.", "MCVOY, A. A.") == 1.0
    assert name_score("James V. Garcia and Eddie B. Garcia", "GARCIA, JAMES") == 1.0


def test_company_lessors_still_match_on_distinctive_words():
    assert name_score("Texas Lime Company", "U.S. LIME D") > 0
    assert name_score("NOACK FARMS, LLC", "NOACK FARMS") == 1.0
