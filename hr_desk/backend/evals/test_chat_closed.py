"""A saved reply stays readable, and follow-ups close when its stamp no longer matches."""

from app.main import exchange_closed

SCOPE = "hr:policy-roles-v1:4"
TODAY = "2026-11-27"


def test_matching_pair_stays_open():
    answer = {"scope": SCOPE, "expiresOn": "2026-12-01"}
    assert not exchange_closed(answer, SCOPE, TODAY)


def test_missing_expiry_stays_open():
    answer = {"scope": SCOPE}
    assert not exchange_closed(answer, SCOPE, TODAY)


def test_different_scope_closes_the_pair():
    answer = {"scope": "hr:policy-roles-v1:3", "expiresOn": "2026-12-01"}
    assert exchange_closed(answer, SCOPE, TODAY)


def test_past_expiry_closes_the_pair():
    answer = {"scope": SCOPE, "expiresOn": "2026-11-26"}
    assert exchange_closed(answer, SCOPE, TODAY)


def test_saved_question_time_fills_a_message_that_has_none():
    from app.main import stamp_message_times
    messages = [
        {"role": "user", "text": "What is the code of conduct?"},
        {"role": "assistant", "text": "A short answer.", "scope": SCOPE},
    ]
    stamped = stamp_message_times(messages, [("What is the code of conduct?", "2026-11-26T10:28:00+00:00")], None)
    assert stamped[0]["at"] == "2026-11-26T10:28:00+00:00"
    assert stamped[1]["at"] == stamped[0]["at"]


def test_stored_message_time_is_kept():
    from app.main import stamp_message_times
    messages = [
        {"role": "user", "text": "Hello", "at": "2026-11-27T06:00:00+00:00"},
        {"role": "assistant", "text": "Hi", "at": "2026-11-27T06:00:00+00:00"},
    ]
    stamped = stamp_message_times(messages, [("Hello", "2026-11-01T00:00:00+00:00")], "2026-11-27T08:00:00+00:00")
    assert stamped[0]["at"] == "2026-11-27T06:00:00+00:00"
