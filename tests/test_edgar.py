import csv

from landrisk import edgar

EXECUTED = """PAID UP OIL AND GAS LEASE THIS LEASE AGREEMENT is made as of the 20th day of June, 2011,
between NOACK FARMS, LLC, as Lessor, and ARDENT 1, LLC., as Lessee. Lessor hereby grants, leases and lets
exclusively to Lessee the following described land: Being 623.29 acres in Milam County, Texas.
""" + "Lessee shall pay royalty. " * 400

BLANK_FORM = """FORM OF OIL AND GAS LEASE. THIS LEASE is made this ____ day of __________, 20__, between
______________, as Lessor, and ______________, as Lessee.""" + " Lessor grants to Lessee. " * 400


def test_html_to_text_strips_tags_and_entities():
    assert edgar.html_to_text("<p>Lessor&nbsp;&amp; <b>Lessee</b></p><br>x") == "Lessor & Lessee x"


def test_executed_lease_is_accepted():
    ok, reason = edgar.looks_like_executed_lease(EXECUTED)
    assert ok, reason


def test_blank_form_is_rejected():
    ok, reason = edgar.looks_like_executed_lease(BLANK_FORM)
    assert not ok
    assert "blank" in reason


def test_non_lease_is_rejected():
    ok, _ = edgar.looks_like_executed_lease("Purchase and sale agreement. " * 500)
    assert not ok


def test_label_rows_blank_means_absent_and_dash_means_skip(tmp_path):
    path = tmp_path / "labels.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["document_id", "lessor", "tract_id", "depth_limit_ft"])
        w.writeheader()
        w.writerow({"document_id": "EDG-0001", "lessor": "Noack Farms, LLC", "tract_id": "-", "depth_limit_ft": ""})
    rows = edgar.label_rows(path)
    assert ("EDG-0001", "lessor", "Noack Farms, LLC") in rows
    assert ("EDG-0001", "depth_limit_ft", None) in rows
    assert not any(r[1] == "tract_id" for r in rows)


def test_label_rows_skips_unlabeled_documents(tmp_path):
    path = tmp_path / "labels.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["document_id", "lessor", "county"])
        w.writeheader()
        w.writerow({"document_id": "EDG-0002", "lessor": "", "county": ""})
    assert edgar.label_rows(path) == []


def test_other_agreement_that_mentions_a_lease_is_rejected():
    text = ("PURCHASE AND SALE AGREEMENT dated the 18th day of December, 2003, between PNP Petroleum as Seller. "
            "Seller conveys each oil and gas lease listed, and Lessor and Lessee consents. ") + "Terms. " * 900
    ok, reason = edgar.looks_like_executed_lease(text)
    assert not ok
    assert "not titled" in reason


def test_untitled_lease_agreement_is_rejected():
    # e.g. a frac-sand surface lease: a lease, a lessor and a lessee, but not oil and gas
    text = ("LEASE AGREEMENT dated this 14th day of April 2011 between Sand Hill Land and Cattle, LLC (the Lessor) "
            "and Lonestar Prospects (the Lessee). Nothing here is an oil and gas lease. ") + "Sand. " * 1200
    assert not edgar.looks_like_executed_lease(text)[0]


def test_bundle_of_several_leases_is_rejected():
    one = EXECUTED[:600]
    ok, reason = edgar.looks_like_executed_lease(one + " " + one + " Royalty. " * 800)
    assert not ok
    assert "more than one" in reason


def test_near_duplicates_detected_despite_formatting_noise():
    body = " ".join(f"clause {i} lessee shall pay lessor" for i in range(400))
    a = "EX-10.10 6 s1-filing.htm OIL AND GAS LEASE " + body
    b = "EX-10.10 7 s4-filing.htm OIL AND GAS LEASE , " + body
    assert edgar.near_duplicate(a, b)
    assert not edgar.near_duplicate(a, " ".join(f"item {i} buyer pays seller" for i in range(400)))


def test_assignment_of_leases_is_rejected():
    text = ("ASSIGNMENT AND BILL OF SALE OF OIL AND GAS LEASES dated the 1st day of May, 2005. Grantor, as Lessee "
            "under the leases, assigns to Grantee; each Lessor consents. ") + "Leases listed. " * 700
    assert not edgar.looks_like_executed_lease(text)[0]


def test_purchase_of_lease_acreage_is_rejected():
    text = ("Agreement for the purchase of 3,400 oil and gas lease acreage. PURCHASE AND SALE AGREEMENT effective "
            "the 1st day of October, 2006. Lessor and Lessee terms apply. ") + "Terms. " * 900
    assert not edgar.looks_like_executed_lease(text)[0]
