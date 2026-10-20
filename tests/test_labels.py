from datetime import datetime

from landrisk.edgar import labels_from_reviews

T0, T1 = datetime(2026, 10, 20, 9), datetime(2026, 10, 20, 10)
FIELDS = ["lessor", "royalty_rate", "tract_id", "effective_date"]


def test_complete_document_takes_accepted_and_corrected_values():
    extracted = [("EDG-1", "lessor", "Noack Farms"), ("EDG-1", "royalty_rate", "0.125"),
                 ("EDG-1", "tract_id", None), ("EDG-1", "effective_date", None)]
    reviews = [("EDG-1", "lessor", "accept", None, T0),
               ("EDG-1", "royalty_rate", "correct", "0.1666666667", T0),
               ("EDG-1", "effective_date", "correct", None, T0)]  # "not in lease"
    done, progress = labels_from_reviews(extracted, reviews, FIELDS, skip={"tract_id"})
    assert done == {"EDG-1": {"lessor": "Noack Farms", "royalty_rate": "0.1666666667", "effective_date": None}}
    assert progress == {"EDG-1": (3, 3)}


def test_incomplete_document_is_held_back():
    extracted = [("EDG-2", "lessor", "A"), ("EDG-2", "royalty_rate", "0.2")]
    reviews = [("EDG-2", "lessor", "accept", None, T0)]
    done, progress = labels_from_reviews(extracted, reviews, ["lessor", "royalty_rate"], skip=set())
    assert done == {}
    assert progress == {"EDG-2": (1, 2)}


def test_latest_review_wins():
    extracted = [("EDG-3", "lessor", "A")]
    reviews = [("EDG-3", "lessor", "correct", "B", T1), ("EDG-3", "lessor", "accept", None, T0)]
    done, _ = labels_from_reviews(extracted, reviews, ["lessor"], skip=set())
    assert done["EDG-3"]["lessor"] == "B"


def test_accept_uses_the_value_seen_at_review_time_not_a_later_extraction():
    extracted = [("EDG-4", "lessor", "Re-extracted Name")]
    reviews = [("EDG-4", "lessor", "accept", "Name I Accepted", T0)]
    done, _ = labels_from_reviews(extracted, reviews, ["lessor"], skip=set())
    assert done["EDG-4"]["lessor"] == "Name I Accepted"
