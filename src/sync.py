from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from src.config import CreateConfig, SearchConfig, get_settings, load_create_config, load_search_config
from src.hr_client import build_hr_client
from src.ops_client import build_ops_client
from src.osticket_client import OsTicketClient, Ticket
from src.state import StateStore

logger = logging.getLogger(__name__)

NOTE_POSTER = "Ticket-Sync"
MAX_FAILURE_ATTEMPTS_BEFORE_ALERT = 3


class ForwardOutcome(str, Enum):
    ALREADY_FORWARDED = "already_forwarded"
    FORWARDED = "forwarded"
    FAILED = "failed"


def render_ops_payload(ticket: Ticket, create_cfg: CreateConfig) -> tuple[str, str]:
    context = {
        "hr_number": ticket.number,
        "hr_subject": ticket.subject or "(no subject)",
        "hr_message": ticket.message or "",
        "hr_requester_name": ticket.user_name,
        "hr_requester_email": ticket.user_email,
        "hr_closed_at": ticket.closed or "",
    }
    subject = create_cfg.subject_template.format(**context)
    message = create_cfg.message_template.format(**context)
    return subject, message


def _forward_one(
    hr: OsTicketClient,
    ops: OsTicketClient,
    state: StateStore,
    create_cfg: CreateConfig,
    ticket_summary: Ticket,
) -> ForwardOutcome:
    hr_ticket_id = ticket_summary.ticket_id
    if state.has_been_forwarded(hr_ticket_id):
        return ForwardOutcome.ALREADY_FORWARDED

    try:
        ticket = hr.get_ticket(hr_ticket_id)
        subject, message = render_ops_payload(ticket, create_cfg)
        created = ops.create_ticket(
            user_id=create_cfg.user_id,
            subject=subject,
            message=message,
            topic_id=create_cfg.topic_id,
            dept_id=create_cfg.dept_id,
        )
    except Exception as exc:
        attempts = state.record_failure(hr_ticket_id, str(exc))
        log = logger.error if attempts >= MAX_FAILURE_ATTEMPTS_BEFORE_ALERT else logger.warning
        log("Failed to forward HR ticket %s (%s), attempt %d: %s", hr_ticket_id, ticket_summary.number, attempts, exc)
        return ForwardOutcome.FAILED

    state.record_forwarded(
        hr_ticket_id,
        ops_ticket_id=created.ticket_id,
        hr_ticket_number=ticket.number,
        ops_ticket_number=created.number,
    )
    logger.info("Forwarded HR ticket %s to Ops as %s", ticket.number, created.number)

    try:
        hr.add_note(hr_ticket_id, body=f"Forwarded to Ops as ticket #{created.number}.", poster=NOTE_POSTER)
    except Exception as exc:
        # best-effort write-back; the state DB above is already the source of truth
        logger.warning(
            "Forwarded HR ticket %s to Ops as %s, but failed to leave the write-back note: %s",
            ticket.number, created.number, exc,
        )

    return ForwardOutcome.FORWARDED


def run_sync_cycle_with(
    hr: OsTicketClient,
    ops: OsTicketClient,
    state: StateStore,
    search_cfg: SearchConfig,
    create_cfg: CreateConfig,
) -> dict[str, int]:
    stats = {outcome.value: 0 for outcome in ForwardOutcome}
    stats["matched"] = 0

    status_ids = hr.resolve_status_ids(search_cfg.status)
    if not status_ids:
        logger.error("No statuses with state '%s' found on the HR instance; nothing to search for", search_cfg.status)
        return stats

    updated_after = None
    if search_cfg.updated_after_lookback_days:
        updated_after = (
            datetime.now(timezone.utc) - timedelta(days=search_cfg.updated_after_lookback_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

    for ticket_summary in hr.iter_all_tickets(
        status_id=status_ids,
        topic_id=search_cfg.topic_id,
        dept_id=search_cfg.dept_id,
        custom_fields=search_cfg.custom_fields,
        updated_after=updated_after,
        limit=search_cfg.page_size,
    ):
        stats["matched"] += 1
        outcome = _forward_one(hr, ops, state, create_cfg, ticket_summary)
        stats[outcome.value] += 1

    logger.info("Sync cycle complete: %s", stats)
    return stats


def run_sync_cycle() -> dict[str, int]:
    settings = get_settings()
    search_cfg = load_search_config(settings.search_config_path)
    create_cfg = load_create_config(settings.create_config_path)

    with build_hr_client(settings) as hr, build_ops_client(settings) as ops, StateStore(settings.state_db_path) as state:
        return run_sync_cycle_with(hr, ops, state, search_cfg, create_cfg)
