# Ticket-Sync — HR → Ops ticket forwarding job

## 1. Goal

Doctors open tickets on the **HR** osTicket instance. Some of those need
Ops to actually act on them. Today: HR closes the ticket and emails Ops
manually. Going forward: HR closes the ticket and marks it (via topic
and/or a custom field) as "needs Ops". A scheduled job — **Ticket-Sync** —
periodically finds those tickets and creates a corresponding ticket on the
**Ops** osTicket instance, via the `osticket-api` service
(`~/PycharmProjects/osticket-api`, one deployed instance per osTicket
server: `HR_API_*` and `OPS_API_*`).

This is a standalone scheduler process, not a web service — same shape as
`codesystem-poller`: a small script that wakes up on a cron schedule, does
one pass of work, and goes back to sleep. No inbound HTTP surface of its
own.

## 2. Decisions made

- **Ops ticket owner**: a fixed service account ("HR-Ops Bridge") created
  once in the Ops osTicket, referenced by `user_id` in config. The
  original requester's name/email is embedded in the forwarded ticket's
  message body, not as the ticket owner. Rejected: creating/matching a
  real doctor account in Ops — Ops agents don't need it and it would
  require new user-provisioning endpoints in the API for no real benefit.
- **Idempotency & audit trail**: a local SQLite state DB
  (`hr_ticket_id → ops_ticket_id`) is the source of truth for "already
  forwarded" — so a ticket is never double-created even across restarts
  or partial failures. In addition, on success the job posts an **internal
  note** back onto the HR ticket ("Forwarded to Ops as #12345") purely for
  human visibility to HR agents. The state DB is authoritative; the note
  is best-effort (a note failure doesn't cause a re-forward, it's just
  logged).

## 3. What already exists in `osticket-api` (reused as-is)

Confirmed by reading `~/PycharmProjects/osticket-api/main.py`:

| Need | Endpoint |
|---|---|
| List topics/departments (to populate config by name) | `GET /topics`, `GET /departments` |
| Search HR tickets by status/topic/dept + arbitrary custom field | `GET /tickets?status_id=&topic_id=&dept_id=&<custom_field>=` (paginated via `limit`/`offset`) |
| Fetch full ticket incl. custom fields | `GET /tickets/{id}` |
| Create the Ops ticket | `POST /tickets` (`user_id`, `subject`, `message`, `topic_id`, `dept_id`) |
| Close (not needed here — HR already closes manually) | `PUT /tickets/{id}/close` |

Auth is `X-API-Key` (osTicket API key, optionally IP-whitelisted at the
osTicket end) — one key per instance (HR and Ops).

## 4. Gaps to add to `osticket-api` (you own this repo, so extend it)

1. **`POST /tickets/{ticket_id}/note`** — add an internal (staff-only,
   not customer-visible) note to a ticket. New. Needed on the **HR**
   instance for the write-back ("Forwarded to Ops as #X"). Mirrors the
   existing `close_ticket` handler's shape (`ost_thread` insert with the
   internal "note" entry type instead of a status change) — straightforward
   given `main.py` already has ticket/thread lookups in that style.
2. **Optional — custom fields on ticket creation.** `TicketCreate` today
   only takes `user_id/subject/message/topic_id/dept_id`. If the Ops
   help-topic's dynamic form requires fields to be filled (osTicket often
   does), ticket creation will fail without them. **Needs verification
   against the real Ops topic config before implementation** — if
   required, extend `TicketCreate` with an optional `custom_fields: dict`
   and reuse the existing custom-field-parsing code already in the
   `GET /tickets` path.
3. **Deferred (not in v1) — attachment carry-over.** The HR ticket may
   have attachments a doctor uploaded. `osticket-api` has
   `POST /tickets/{id}/attach` (upload) but no download/list endpoint, so
   attachments can't currently be read back off a ticket to re-upload
   elsewhere. If this turns out to matter, add
   `GET /tickets/{id}/attachments` (list) + `GET /attachments/{file_id}`
   (download) to the HR instance. Left out of v1 scope to keep the first
   version small; flagged here so it isn't forgotten.

## 5. Scheduler pattern (mirrors `codesystem-poller`)

Same idiom as `~/PycharmProjects/codesystem-poller`: `pydantic-settings`
for secrets/env (`.env`), a YAML file for the domain-specific list/config,
`APScheduler` `BlockingScheduler` + `CronTrigger` built from a cron string
in settings, run an immediate cycle on startup then hand off to the
scheduler, `SIGTERM` → graceful shutdown, structured logging to stdout,
packaged as a Docker container. Unlike `codesystem-poller`, no
Postgres/RabbitMQ — state lives in a single SQLite file, so there's no
external infra dependency at all.

```
Ticket-Sync/
├── src/
│   ├── config.py       # Settings (env) + load_search_config() + load_create_config()
│   ├── hr_client.py     # thin httpx wrapper over the HR osticket-api instance
│   ├── ops_client.py    # thin httpx wrapper over the Ops osticket-api instance
│   ├── state.py         # SQLite: has_been_forwarded(), record_forwarded(), record_failure()
│   ├── sync.py           # run_sync_cycle(): search → filter by state → create → note-back → record
│   └── main.py           # logging setup, initial run, BlockingScheduler+CronTrigger, SIGTERM handling
├── config/
│   ├── search.yaml       # what counts as "needs Ops" on the HR side
│   └── create.yaml       # how to build the Ops ticket
├── tests/
├── .env.example
├── Dockerfile
└── pyproject.toml
```

### `config/search.yaml` (HR side — what to look for)

```yaml
search:
  status: closed                # resolved via GET /statuses at startup, cached
  dept_id: 3                    # HR department id in HR osTicket
  topic_id: 17                  # e.g. "Escalate to Ops" help topic
  # optional extra filter, only if HR uses a custom field instead of/alongside topic:
  custom_fields:
    route_to: "Ops"
  updated_after_lookback_days: 30   # bound the search window; state DB handles true idempotency
  page_size: 50
```

### `config/create.yaml` (Ops side — what to create)

```yaml
create:
  base_url_env: OPS_API_BASE_URL     # which client to use (supports >1 target in the future)
  dept_id: 8                          # target department in Ops osTicket
  topic_id: 4                         # target help topic in Ops osTicket
  user_id: 91                         # fixed "HR-Ops Bridge" service account in Ops osTicket
  subject_template: "[From HR #{hr_number}] {hr_subject}"
  message_template: |
    Forwarded automatically from HR ticket #{hr_number}.
    Original requester: {hr_requester_name} <{hr_requester_email}>
    Closed by HR on: {hr_closed_at}

    --- Original message ---
    {hr_message}
```

### `.env` (secrets + schedule — pydantic-settings, not committed)

```
HR_API_BASE_URL=https://hr-osticket-api.internal
HR_API_KEY=...
OPS_API_BASE_URL=https://ops-osticket-api.internal
OPS_API_KEY=...
SYNC_CRON=*/15 * * * *
STATE_DB_PATH=/data/ticket_sync.db
LOG_LEVEL=INFO
```

## 6. Sync cycle logic (`sync.py: run_sync_cycle`)

1. Load `search.yaml` and `create.yaml`.
2. `GET /tickets` on the HR client with the configured status/topic/dept/
   custom-field filters, paginating via `limit`/`offset` until exhausted.
3. For each matching ticket:
   a. `state.has_been_forwarded(hr_ticket_id)` → if yes, skip (already
      synced — covers the "topic stays the marker forever" case since HR
      won't/shouldn't flip it back).
   b. Render `subject_template`/`message_template` from the ticket's
      fields (`GET /tickets/{id}` for full detail incl. requester email).
   c. `POST /tickets` on the Ops client with the rendered payload.
   d. On success: `state.record_forwarded(hr_ticket_id, ops_ticket_id, ops_number)`,
      then best-effort `POST /tickets/{hr_ticket_id}/note` on the HR client
      ("Forwarded to Ops as #{ops_number}"). Note failure is logged, not
      retried, and does not block recording success in state.
   e. On failure (Ops API error): log with full context, **do not** write
      to state, so it's retried next cycle. Track consecutive failures per
      ticket so a permanently-broken payload (e.g. missing required Ops
      custom field) doesn't retry forever silently — surface via logs/metric
      after N failed attempts.
4. Log a per-cycle summary: matched / already-forwarded / newly-forwarded / failed.

## 7. Deployment

- `Dockerfile` + single container, `STATE_DB_PATH` on a mounted volume so
  the SQLite file survives restarts/redeploys.
- No docker-compose needed for infra (no DB/broker), unlike
  `codesystem-poller` — this is the whole appeal of SQLite here.
- Runs as one instance only (SQLite has no safe concurrent-writer story
  across processes) — fine, since this is a single scheduled job.

## 8. Testing

Mirror `osticket-api`'s own test style: `pytest`, `httpx` calls mocked
(e.g. `respx` or a fixture that monkeypatches the client), a couple of
integration-style tests against the local SQLite state file. Cover: happy
path (new ticket forwarded + note posted), already-forwarded ticket
skipped, Ops API failure leaves state untouched (retried next cycle), note
POST failure doesn't affect recorded state, pagination across >1 page of
search results.

## 9. Open items to confirm before/while building

- **Ops dynamic form fields**: does the target Ops help topic require
  custom fields on creation? Determines whether gap #2 above is in scope
  for v1.
- **Exact HR marker**: confirm whether "needs Ops" will be a dedicated
  help topic, a custom field, or both — determines whether `search.yaml`
  needs the `custom_fields` block at all for v1.
- **Service account creation**: the "HR-Ops Bridge" user needs to be
  created once, manually, in the Ops osTicket admin UI, and its `user_id`
  put into `create.yaml`.
- **Attachment carry-over**: deferred per §4.3 — confirm this is
  acceptable for v1 or needs pulling forward.

## 10. Build order

1. `osticket-api`: add `POST /tickets/{id}/note` (HR instance gap).
2. `osticket-api`: verify/add custom-fields-on-create support if Ops
   topic requires it.
3. `Ticket-Sync`: `config.py` + `state.py` (SQLite) + tests.
4. `Ticket-Sync`: `hr_client.py` / `ops_client.py` thin wrappers + tests.
5. `Ticket-Sync`: `sync.py` cycle logic + tests (mocked clients).
6. `Ticket-Sync`: `main.py` scheduler wiring, Dockerfile.
7. Manual end-to-end dry run against real HR/Ops staging instances with
   `SYNC_CRON` set to run once on demand before enabling the schedule.