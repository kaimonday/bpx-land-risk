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


def test_rrc_lease_named_in_the_land_description_ranks_first():
    # EDG-0014: "The F. C. Hinds oil and gas lease, more fully described in Exhibits A, B and C"
    lease = {"lessor": "Dorothy Hinds; Frank Hinds", "lessee": "Frank W Cole Engineering", "effective_date": "2004-12-14",
             "land": "The F. C. Hinds oil and gas lease, more fully described in Exhibits A, B and C, attached hereto"}
    named = {"lease_name": 'HINDS, F.C. "C"', "operator_name": "NORTH AMERICAN DRILLING", "first_prod_ym": "199301",
             "last_prod_ym": "201908", "lease_no": "06366"}
    other = {"lease_name": "HINDS, ADDIE", "operator_name": "TEXIAN OPERATING", "first_prod_ym": "201209",
             "last_prod_ym": "202606", "lease_no": "16192"}
    assert score_candidate(lease, named)[0] > score_candidate(lease, other)[0]


def test_rrc_lease_number_cited_in_the_lease_is_a_near_certain_match():
    # EDG-0015: "The DILLER B lease, Railroad Commission Of Texas District 7B no. 18751"
    lease = {"lessor": "David Palmer, John Scott Palmer", "lessee": "Energytec Inc.", "effective_date": None,
             "land": "The DILLER B lease, Railroad Commission Of Texas District 7B no. 18751 and the Diller A lease"}
    cited = {"lease_name": "DILLER", "operator_name": "SOMEONE", "first_prod_ym": "199301", "lease_no": "18751"}
    score, why = score_candidate(lease, cited)
    assert score >= 0.95
    assert "18751" in why


def test_abstract_with_en_dash():
    from landrisk.link_rrc import land_description
    assert land_description("following described land: Being 229 acres out of the William Allen Survey, A–72 Milam County")["abstracts"] == ["A-72"]


def test_initials_must_match_when_the_rrc_name_has_them():
    from landrisk.link_rrc import named_in_land
    land = "The F. C. Hinds oil and gas lease, more fully described in Exhibits A, B and C, attached hereto"
    assert named_in_land("HINDS, F. C.", land)
    assert named_in_land("HINDS, F.C. -H-", land)       # unit suffix ignored
    assert named_in_land('HINDS, F.C. "C"', land)
    assert not named_in_land("HINDS, F. L. -A-", land)
    assert not named_in_land("HINDS, B.T.", land)
    assert not named_in_land("HINDS, ADDIE", land)
    assert named_in_land("DILLER A", "The DILLER B lease ... and the Diller A lease")
