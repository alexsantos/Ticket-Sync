import pytest

from src.state import StateStore


@pytest.fixture
def store(tmp_path):
    with StateStore(str(tmp_path / "state.db")) as s:
        yield s


def test_has_not_been_forwarded_initially(store):
    assert store.has_been_forwarded(123) is False


def test_record_and_check_forwarded(store):
    store.record_forwarded(123, ops_ticket_id=456, hr_ticket_number="HR-1", ops_ticket_number="OPS-1")
    assert store.has_been_forwarded(123) is True

    forwarded = store.get_forwarded(123)
    assert forwarded.hr_ticket_id == 123
    assert forwarded.hr_ticket_number == "HR-1"
    assert forwarded.ops_ticket_id == 456
    assert forwarded.ops_ticket_number == "OPS-1"
    assert forwarded.synced_at


def test_get_forwarded_returns_none_when_missing(store):
    assert store.get_forwarded(999) is None


def test_record_forwarded_is_idempotent_upsert(store):
    store.record_forwarded(123, ops_ticket_id=456, ops_ticket_number="OPS-1")
    store.record_forwarded(123, ops_ticket_id=456, ops_ticket_number="OPS-1")
    assert store.has_been_forwarded(123) is True


def test_record_failure_tracks_attempt_count(store):
    assert store.record_failure(123, "boom") == 1
    assert store.record_failure(123, "boom again") == 2
    assert store.record_failure(123, "boom a third time") == 3


def test_record_failure_is_per_ticket(store):
    store.record_failure(1, "err")
    assert store.record_failure(2, "err") == 1


def test_successful_forward_clears_prior_failures(store):
    store.record_failure(123, "transient error")
    store.record_failure(123, "transient error again")
    store.record_forwarded(123, ops_ticket_id=456)

    # a fresh failure after a successful forward should restart the count at 1,
    # proving the earlier failure history was cleared, not just superseded
    assert store.record_failure(123, "new error") == 1


def test_state_persists_across_store_instances(tmp_path):
    db_path = str(tmp_path / "state.db")
    with StateStore(db_path) as s1:
        s1.record_forwarded(123, ops_ticket_id=456)

    with StateStore(db_path) as s2:
        assert s2.has_been_forwarded(123) is True
