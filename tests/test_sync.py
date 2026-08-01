import json

import httpx
import pytest

from src.config import CreateConfig, SearchConfig, get_settings
from src.osticket_client import OsTicketClient, Ticket
from src.state import StateStore
from src.sync import render_ops_payload, run_sync_cycle, run_sync_cycle_with

STATUSES = [
    {"id": 1, "name": "Open", "state": "open"},
    {"id": 2, "name": "Closed", "state": "closed"},
]


def ticket_payload(ticket_id: int, **overrides) -> dict:
    payload = {
        "ticket_id": ticket_id,
        "number": f"HR-{ticket_id}",
        "created": "2026-01-01T00:00:00",
        "status_id": 2,
        "status_name": "Closed",
        "topic_id": 17,
        "topic_name": "Escalate to Ops",
        "dept_id": 3,
        "dept_name": "HR",
        "user_id": 42,
        "user_name": "Dr. Jane Doe",
        "user_email": "jane@example.com",
        "subject": "Broken laptop",
        "message": "My laptop won't boot.",
        "closed": "2026-01-02T00:00:00",
        "custom_fields": {"route_to": "Ops"},
    }
    payload.update(overrides)
    return payload


class FakeApi:
    """A minimal in-memory stand-in for one osticket-api deployment, driven
    through the same HTTP surface OsTicketClient talks to, so tests exercise
    the real client code instead of a hand-rolled substitute."""

    def __init__(self, statuses=None, tickets=None):
        self.statuses = statuses if statuses is not None else STATUSES
        self.tickets: dict[int, dict] = {t["ticket_id"]: t for t in (tickets or [])}
        self.created_tickets: list[dict] = []
        self.notes: list[dict] = []
        self._next_created_id = 1000

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)

        if path == "/statuses":
            return httpx.Response(200, json=self.statuses)

        if path == "/tickets" and request.method == "GET":
            status_ids = {int(i) for i in params.get("status_id", "").split(",") if i}
            matching = [
                t for t in self.tickets.values()
                if not status_ids or t["status_id"] in status_ids
            ]
            matching.sort(key=lambda t: t["ticket_id"])
            limit = int(params.get("limit", 50))
            offset = int(params.get("offset", 0))
            page = matching[offset:offset + limit]
            return httpx.Response(200, json={
                "total": len(matching), "limit": limit, "offset": offset, "items": page,
            })

        if path.startswith("/tickets/") and path.endswith("/note") and request.method == "POST":
            ticket_id = int(path.split("/")[2])
            if ticket_id not in self.tickets:
                return httpx.Response(404, json={"detail": "Ticket not found."})
            body = json.loads(request.content)
            self.notes.append({"ticket_id": ticket_id, **body})
            return httpx.Response(200, json={"entry_id": len(self.notes)})

        if path.startswith("/tickets/") and request.method == "GET":
            ticket_id = int(path.split("/")[2])
            if ticket_id not in self.tickets:
                return httpx.Response(404, json={"detail": "Ticket not found"})
            return httpx.Response(200, json=self.tickets[ticket_id])

        if path == "/tickets" and request.method == "POST":
            body = json.loads(request.content)
            self._next_created_id += 1
            created = {"ticket_id": self._next_created_id, "number": f"OPS-{self._next_created_id}"}
            self.created_tickets.append({**body, **created})
            return httpx.Response(200, json=created)

        raise AssertionError(f"unexpected request: {request.method} {path}")

    def client(self) -> OsTicketClient:
        return OsTicketClient("https://example.com", "key", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def state(tmp_path):
    with StateStore(str(tmp_path / "state.db")) as s:
        yield s


@pytest.fixture
def search_cfg():
    return SearchConfig(state="closed", dept_id=3, topic_id=17, custom_fields={}, page_size=50)


@pytest.fixture
def create_cfg():
    return CreateConfig(
        user_id=91,
        subject_template="[From HR #{hr_number}] {hr_subject}",
        message_template="Requester: {hr_requester_name} <{hr_requester_email}>\n\n{hr_message}",
        dept_id=8,
        topic_id=4,
    )


# --- render_ops_payload ---

def test_render_ops_payload():
    ticket = Ticket(**ticket_payload(1))
    create_cfg = CreateConfig(
        user_id=91,
        subject_template="[From HR #{hr_number}] {hr_subject}",
        message_template="From {hr_requester_name} <{hr_requester_email}>: {hr_message} (closed {hr_closed_at})",
    )
    subject, message = render_ops_payload(ticket, create_cfg)
    assert subject == "[From HR #HR-1] Broken laptop"
    assert message == "From Dr. Jane Doe <jane@example.com>: My laptop won't boot. (closed 2026-01-02T00:00:00)"


def test_render_ops_payload_handles_missing_content():
    ticket = Ticket(**ticket_payload(1, subject=None, message=None, closed=None))
    create_cfg = CreateConfig(
        user_id=91,
        subject_template="{hr_subject}",
        message_template="{hr_message}|{hr_closed_at}",
    )
    subject, message = render_ops_payload(ticket, create_cfg)
    assert subject == "(no subject)"
    assert message == "|"


# --- run_sync_cycle_with ---

def test_forwards_a_new_matching_ticket(state, search_cfg, create_cfg):
    hr_api = FakeApi(tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 1, "already_forwarded": 0, "forwarded": 1, "would_forward": 0, "failed": 0}
    assert len(ops_api.created_tickets) == 1
    created = ops_api.created_tickets[0]
    assert created["user_id"] == 91
    assert created["topic_id"] == 4
    assert created["dept_id"] == 8
    assert created["subject"] == "[From HR #HR-1] Broken laptop"
    assert "My laptop won't boot." in created["message"]

    assert state.has_been_forwarded(1)
    forwarded = state.get_forwarded(1)
    assert forwarded.ops_ticket_number == created["number"]

    assert len(hr_api.notes) == 1
    assert hr_api.notes[0]["ticket_id"] == 1
    assert created["number"] in hr_api.notes[0]["body"]
    assert hr_api.notes[0]["poster"] == "Ticket-Sync"


def test_skips_already_forwarded_ticket(state, search_cfg, create_cfg):
    state.record_forwarded(1, ops_ticket_id=999, ops_ticket_number="OPS-999")
    hr_api = FakeApi(tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 1, "already_forwarded": 1, "forwarded": 0, "would_forward": 0, "failed": 0}
    assert ops_api.created_tickets == []
    assert hr_api.notes == []


def test_ops_create_failure_is_recorded_and_not_marked_forwarded(state, search_cfg, create_cfg):
    hr_api = FakeApi(tickets=[ticket_payload(1)])

    def failing_ops_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "User with id 91 does not exist."})

    with hr_api.client() as hr, OsTicketClient("https://ops.example.com", "key", transport=httpx.MockTransport(failing_ops_handler)) as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 1, "already_forwarded": 0, "forwarded": 0, "would_forward": 0, "failed": 1}
    assert state.has_been_forwarded(1) is False
    assert hr_api.notes == []


def test_note_failure_does_not_prevent_marking_forwarded(state, search_cfg, create_cfg):
    hr_api = FakeApi(tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    original_handler = hr_api.handler

    def flaky_note_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/note"):
            return httpx.Response(500, json={"detail": "boom"})
        return original_handler(request)

    hr_client = OsTicketClient("https://hr.example.com", "key", transport=httpx.MockTransport(flaky_note_handler))

    with hr_client as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 1, "already_forwarded": 0, "forwarded": 1, "would_forward": 0, "failed": 0}
    assert state.has_been_forwarded(1) is True
    assert len(ops_api.created_tickets) == 1


def test_paginates_across_multiple_tickets(state, search_cfg, create_cfg):
    hr_api = FakeApi(tickets=[ticket_payload(i) for i in range(1, 6)])
    ops_api = FakeApi()
    search_cfg.page_size = 2

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 5, "already_forwarded": 0, "forwarded": 5, "would_forward": 0, "failed": 0}
    assert len(ops_api.created_tickets) == 5
    assert {state.has_been_forwarded(i) for i in range(1, 6)} == {True}


def test_no_matching_status_returns_early(state, search_cfg, create_cfg):
    hr_api = FakeApi(statuses=[{"id": 1, "name": "Open", "state": "open"}], tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 0, "already_forwarded": 0, "forwarded": 0, "would_forward": 0, "failed": 0}
    assert ops_api.created_tickets == []


def test_mixed_outcomes_across_multiple_tickets(state, search_cfg, create_cfg):
    state.record_forwarded(1, ops_ticket_id=999, ops_ticket_number="OPS-999")
    hr_api = FakeApi(tickets=[ticket_payload(1), ticket_payload(2)])
    ops_api = FakeApi()

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)

    assert stats == {"matched": 2, "already_forwarded": 1, "forwarded": 1, "would_forward": 0, "failed": 0}
    assert len(ops_api.created_tickets) == 1
    assert ops_api.created_tickets[0]["subject"].startswith("[From HR #HR-2]")


def test_dry_run_does_not_create_ticket_or_mutate_state(state, search_cfg, create_cfg):
    hr_api = FakeApi(tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg, dry_run=True)

    assert stats == {"matched": 1, "already_forwarded": 0, "forwarded": 0, "would_forward": 1, "failed": 0}
    assert ops_api.created_tickets == []
    assert hr_api.notes == []
    assert state.has_been_forwarded(1) is False


def test_dry_run_still_reports_already_forwarded_tickets(state, search_cfg, create_cfg):
    state.record_forwarded(1, ops_ticket_id=999, ops_ticket_number="OPS-999")
    hr_api = FakeApi(tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    with hr_api.client() as hr, ops_api.client() as ops:
        stats = run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg, dry_run=True)

    assert stats == {"matched": 1, "already_forwarded": 1, "forwarded": 0, "would_forward": 0, "failed": 0}
    assert ops_api.created_tickets == []


# --- run_sync_cycle (settings-driven entrypoint) ---

def test_run_sync_cycle_wires_everything_from_settings(tmp_path, monkeypatch):
    search_path = tmp_path / "search.yaml"
    search_path.write_text("search:\n  state: closed\n  dept_id: 3\n  topic_id: 17\n")
    create_path = tmp_path / "create.yaml"
    create_path.write_text(
        "create:\n"
        "  user_id: 91\n"
        "  dept_id: 8\n"
        "  topic_id: 4\n"
        "  subject_template: \"[From HR #{hr_number}] {hr_subject}\"\n"
        "  message_template: \"{hr_message}\"\n"
    )

    monkeypatch.setenv("HR_API_BASE_URL", "https://hr.example.com")
    monkeypatch.setenv("HR_API_KEY", "hr-key")
    monkeypatch.setenv("OPS_API_BASE_URL", "https://ops.example.com")
    monkeypatch.setenv("OPS_API_KEY", "ops-key")
    monkeypatch.setenv("SEARCH_CONFIG_PATH", str(search_path))
    monkeypatch.setenv("CREATE_CONFIG_PATH", str(create_path))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()

    hr_api = FakeApi(tickets=[ticket_payload(1)])
    ops_api = FakeApi()

    import src.sync as sync_module
    monkeypatch.setattr(sync_module, "build_hr_client", lambda settings: hr_api.client())
    monkeypatch.setattr(sync_module, "build_ops_client", lambda settings: ops_api.client())

    try:
        stats = run_sync_cycle()
    finally:
        get_settings.cache_clear()

    assert stats == {"matched": 1, "already_forwarded": 0, "forwarded": 1, "would_forward": 0, "failed": 0}
    assert len(ops_api.created_tickets) == 1

    with StateStore(str(tmp_path / "state.db")) as reopened_state:
        assert reopened_state.has_been_forwarded(1) is True
