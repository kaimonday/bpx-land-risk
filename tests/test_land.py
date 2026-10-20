from landrisk.link_rrc import land_description

EDG1 = ("Lessor hereby grants, leases and lets exclusively to Lessee the following described land, hereinafter called "
        "leased premises: (use Exhibit “A” for long description): Being 623.29 acres, more or less, out of the "
        "James Reese League, A-303, in Milam County, Texas. Being the same lands described in an Assignment/Deed.")
EDG6 = ("the following described land, hereinafter called leased premises. All that certain real property out of the "
        "E.T.R.R.Co. Survey No. 43, Abstract No. 306 described in that Warranty Deed from the Veterans Land Board.")
EDG13 = ("situated in the County of Milam, State of Texas, and is described as follows: All those certain tracts or "
         "parcels of land, out of the Jose Leal Survey, lying and being situated in Milam County, Texas and being more "
         "particularly described in EXHIBIT “A” attached hereto.")
EDG9 = ("The land that is covered by and subject to this Lease is situated in Karnes County, Texas, and is described as "
        "follows and referred to in this Lease as the \"land\": 138 acres, more or less, out of the Charles A. Smith Survey.")


def test_newer_form_description_with_abstract_and_acres():
    d = land_description(EDG1)
    assert d["text"].startswith("Being 623.29 acres")
    assert d["abstracts"] == ["A-303"]
    assert d["acres"] == "623.29"
    assert "James Reese League" in d["surveys"]


def test_abstract_number_written_out():
    d = land_description(EDG6)
    assert d["abstracts"] == ["A-306"]
    assert any("Survey No. 43" in s or "E.T.R.R.Co." in s for s in d["surveys"])


def test_older_form_described_as_follows():
    d = land_description(EDG13)
    assert d["text"].startswith("All those certain tracts")
    assert "Jose Leal Survey" in d["surveys"]


def test_described_as_follows_with_trailing_words():
    d = land_description(EDG9)
    assert "138 acres" in d["text"]
    assert d["acres"] == "138"


def test_nothing_found():
    assert land_description("This agreement has no land description at all.")["text"] is None
