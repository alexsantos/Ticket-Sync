import json

import httpx
import pytest

from src.osticket_client import OsTicketApiError, OsTicketClient, Ticket


def make_client(handler) -> OsTicketClient:
    return OsTicketClient(
        base_url="https://osticket.example.com",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )


def ticket_payload(ticket_id: int, **overrides) -> dict:
    payload = {
        "ticket_id": ticket_id,
        "number": f"T-{ticket_id}",
        "created": "2026-01-01T00:00:00",
        "status_id": 3,
        "status_name": "Closed",
        "topic_id": 17,
        "topic_name": "Escalate to Ops",
        "dept_id": 3,
        "dept_name": "HR",
        "user_id": 42,
        "user_name": "Dr. Jane Doe",
        "user_email": "jane@example.com",
        "custom_fields": {"route_to": "Ops"},
    }
    payload.update(overrides)
    return payload


def test_sends_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=[])

    client = make_client(handler)
    client.list_statuses()
    assert seen["api_key"] == "test-key"


def test_search_tickets_builds_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total": 1, "limit": 50, "offset": 0, "items": [ticket_payload(1)]})

    client = make_client(handler)
    page = client.search_tickets(
        status_id=[2, 3],
        topic_id=17,
        dept_id=3,
        custom_fields={"route_to": "Ops"},
        updated_after="2026-01-01T00:00:00",
    )

    assert seen["params"] == {
        "limit": "50",
        "offset": "0",
        "status_id": "2,3",
        "topic_id": "17",
        "dept_id": "3",
        "updated_after": "2026-01-01T00:00:00",
        "route_to": "Ops",
    }
    assert page.total == 1
    assert page.items[0].ticket_id == 1
    assert page.items[0].custom_fields == {"route_to": "Ops"}


def test_search_tickets_omits_unset_filters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total": 0, "limit": 50, "offset": 0, "items": []})

    make_client(handler).search_tickets()
    assert seen["params"] == {"limit": "50", "offset": "0"}


def test_iter_all_tickets_pages_until_exhausted():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params)["offset"])
        requests.append(offset)
        all_ids = [1, 2, 3, 4, 5]
        page_ids = all_ids[offset:offset + 2]
        return httpx.Response(200, json={
            "total": len(all_ids), "limit": 2, "offset": offset,
            "items": [ticket_payload(i) for i in page_ids],
        })

    client = make_client(handler)
    tickets = list(client.iter_all_tickets(limit=2))

    assert [t.ticket_id for t in tickets] == [1, 2, 3, 4, 5]
    assert requests == [0, 2, 4]


def test_iter_all_tickets_empty_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "limit": 50, "offset": 0, "items": []})

    assert list(make_client(handler).iter_all_tickets()) == []


def test_get_ticket():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tickets/123"
        return httpx.Response(200, json=ticket_payload(123))

    ticket = make_client(handler).get_ticket(123)
    assert ticket.ticket_id == 123
    assert ticket.user_email == "jane@example.com"


def test_get_ticket_not_found_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Ticket not found"})

    with pytest.raises(OsTicketApiError) as exc_info:
        make_client(handler).get_ticket(999)
    assert exc_info.value.status_code == 404
    assert "Ticket not found" in str(exc_info.value)


def test_create_ticket_sends_full_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket_id": 55, "number": "OPS-55"})

    result = make_client(handler).create_ticket(
        user_id=91, subject="Subj", message="Msg", topic_id=4, dept_id=8,
    )
    assert seen["json"] == {"user_id": 91, "subject": "Subj", "message": "Msg", "topic_id": 4, "dept_id": 8}
    assert result.ticket_id == 55
    assert result.number == "OPS-55"


def test_create_ticket_omits_optional_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket_id": 1, "number": "OPS-1"})

    make_client(handler).create_ticket(user_id=91, subject="s", message="m")
    assert seen["json"] == {"user_id": 91, "subject": "s", "message": "m"}


def test_create_ticket_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "User with id 91 does not exist."})

    with pytest.raises(OsTicketApiError) as exc_info:
        make_client(handler).create_ticket(user_id=91, subject="s", message="m")
    assert exc_info.value.status_code == 400


def test_add_note_full_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tickets/123/note"
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"entry_id": 77})

    entry_id = make_client(handler).add_note(123, body="Forwarded as #456", title="Sync", poster="Ticket-Sync")
    assert seen["json"] == {"body": "Forwarded as #456", "title": "Sync", "poster": "Ticket-Sync"}
    assert entry_id == 77


def test_add_note_minimal_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"entry_id": 1})

    make_client(handler).add_note(123, body="Note body")
    assert seen["json"] == {"body": "Note body"}


def test_add_note_ticket_not_found_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Ticket not found."})

    with pytest.raises(OsTicketApiError):
        make_client(handler).add_note(999, body="x")


def test_resolve_status_ids_filters_by_state():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"id": 1, "name": "Open", "state": "open"},
            {"id": 2, "name": "Closed", "state": "closed"},
            {"id": 3, "name": "Resolved", "state": "closed"},
        ])

    ids = make_client(handler).resolve_status_ids("closed")
    assert ids == [2, 3]


def test_resolve_status_ids_no_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "name": "Open", "state": "open"}])

    assert make_client(handler).resolve_status_ids("closed") == []


def test_paginated_tickets_has_more():
    from src.osticket_client import PaginatedTickets

    one_item = [Ticket(**ticket_payload(1))]
    assert PaginatedTickets(total=5, limit=2, offset=0, items=one_item).has_more is True
    assert PaginatedTickets(total=1, limit=2, offset=0, items=one_item).has_more is False


def test_client_usable_as_context_manager():
    closed = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = make_client(handler)
    original_close = client.close

    def spy_close():
        closed["value"] = True
        original_close()

    client.close = spy_close
    with client as ctx_client:
        assert ctx_client is client
        ctx_client.list_statuses()
    assert closed["value"] is True


def test_transport_level_error_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(OsTicketApiError) as exc_info:
        make_client(handler).list_statuses()
    assert exc_info.value.status_code is None
    assert "connection refused" in str(exc_info.value)
