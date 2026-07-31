from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Optional

import httpx


class OsTicketApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class StatusInfo:
    id: int
    name: str
    state: str


@dataclass
class Ticket:
    ticket_id: int
    number: str
    created: str
    status_id: int
    status_name: str
    user_id: int
    user_name: str
    user_email: str
    topic_id: Optional[int] = None
    topic_name: Optional[str] = None
    dept_id: Optional[int] = None
    dept_name: Optional[str] = None
    subject: Optional[str] = None
    message: Optional[str] = None
    closed: Optional[str] = None
    custom_fields: dict = field(default_factory=dict)


@dataclass
class PaginatedTickets:
    total: int
    limit: int
    offset: int
    items: list[Ticket]

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass
class CreatedTicket:
    ticket_id: int
    number: str


class OsTicketClient:
    """Thin wrapper over one osticket-api deployment. HR and Ops are two
    separate instances of the identical API, so both use this same class -
    see hr_client.py / ops_client.py for the per-instance factory functions."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0, transport: Optional[httpx.BaseTransport] = None):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OsTicketClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OsTicketApiError(
                f"{method} {path} failed: {exc.response.status_code} {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise OsTicketApiError(f"{method} {path} failed: {exc}") from exc
        return response.json()

    def list_statuses(self) -> list[StatusInfo]:
        return [StatusInfo(**item) for item in self._request("GET", "/statuses")]

    def resolve_status_ids(self, state: str) -> list[int]:
        """Translates a status *state* (e.g. 'closed', as used in search.yaml)
        into the concrete status_id(s) configured for it in this osTicket
        instance - there can be more than one (e.g. both "Closed" and
        "Resolved" statuses sharing the 'closed' state)."""
        return [s.id for s in self.list_statuses() if s.state == state]

    def search_tickets(
        self,
        status_id: Optional[list[int]] = None,
        topic_id: Optional[int] = None,
        dept_id: Optional[int] = None,
        custom_fields: Optional[dict[str, str]] = None,
        updated_after: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedTickets:
        params: dict = {"limit": limit, "offset": offset}
        if status_id:
            params["status_id"] = ",".join(str(i) for i in status_id)
        if topic_id is not None:
            params["topic_id"] = topic_id
        if dept_id is not None:
            params["dept_id"] = dept_id
        if updated_after:
            params["updated_after"] = updated_after
        for name, value in (custom_fields or {}).items():
            params[name] = value

        data = self._request("GET", "/tickets", params=params)
        items = [Ticket(**item) for item in data["items"]]
        return PaginatedTickets(total=data["total"], limit=data["limit"], offset=data["offset"], items=items)

    def iter_all_tickets(self, **search_kwargs) -> Iterator[Ticket]:
        """Pages through search_tickets() until every match has been yielded."""
        offset = search_kwargs.pop("offset", 0)
        while True:
            page = self.search_tickets(offset=offset, **search_kwargs)
            yield from page.items
            if not page.items:
                break
            offset += len(page.items)
            if offset >= page.total:
                break

    def get_ticket(self, ticket_id: int) -> Ticket:
        return Ticket(**self._request("GET", f"/tickets/{ticket_id}"))

    def create_ticket(
        self,
        user_id: int,
        subject: str,
        message: str,
        topic_id: Optional[int] = None,
        dept_id: Optional[int] = None,
    ) -> CreatedTicket:
        payload: dict = {"user_id": user_id, "subject": subject, "message": message}
        if topic_id is not None:
            payload["topic_id"] = topic_id
        if dept_id is not None:
            payload["dept_id"] = dept_id
        return CreatedTicket(**self._request("POST", "/tickets", json=payload))

    def add_note(self, ticket_id: int, body: str, title: Optional[str] = None, poster: Optional[str] = None) -> int:
        payload: dict = {"body": body}
        if title is not None:
            payload["title"] = title
        if poster is not None:
            payload["poster"] = poster
        return self._request("POST", f"/tickets/{ticket_id}/note", json=payload)["entry_id"]
