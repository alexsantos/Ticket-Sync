import signal

import pytest

import src.main as main_module
from src.config import get_settings


class FakeScheduler:
    def __init__(self):
        self.jobs = []
        self.started = False
        self.shutdown_called_with = None

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.shutdown_called_with = wait


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    search_path = tmp_path / "search.yaml"
    search_path.write_text("search:\n  state: closed\n")
    create_path = tmp_path / "create.yaml"
    create_path.write_text(
        "create:\n  user_id: 91\n  subject_template: s\n  message_template: m\n"
    )

    monkeypatch.setenv("HR_API_BASE_URL", "https://hr.example.com")
    monkeypatch.setenv("HR_API_KEY", "hr-key")
    monkeypatch.setenv("OPS_API_BASE_URL", "https://ops.example.com")
    monkeypatch.setenv("OPS_API_KEY", "ops-key")
    monkeypatch.setenv("SEARCH_CONFIG_PATH", str(search_path))
    monkeypatch.setenv("CREATE_CONFIG_PATH", str(create_path))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("SYNC_CRON", "*/5 * * * *")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_scheduler(monkeypatch):
    scheduler = FakeScheduler()
    monkeypatch.setattr(main_module, "BlockingScheduler", lambda: scheduler)
    return scheduler


def test_main_runs_initial_cycle_then_schedules_job(base_env, fake_scheduler, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "run_sync_cycle", lambda: calls.append("ran") or {})

    main_module.main()

    assert calls == ["ran"]
    assert fake_scheduler.started is True
    assert len(fake_scheduler.jobs) == 1
    job = fake_scheduler.jobs[0]
    assert job["func"] is main_module.run_sync_cycle
    assert job["id"] == "ticket_sync"
    assert job["max_instances"] == 1
    assert job["coalesce"] is True


def test_main_continues_scheduling_when_initial_cycle_fails(base_env, fake_scheduler, monkeypatch):
    def failing_cycle():
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "run_sync_cycle", failing_cycle)

    main_module.main()  # must not raise

    assert fake_scheduler.started is True
    assert len(fake_scheduler.jobs) == 1


def test_main_registers_sigterm_handler_that_shuts_down_scheduler(base_env, fake_scheduler, monkeypatch):
    monkeypatch.setattr(main_module, "run_sync_cycle", lambda: {})
    original_handler = signal.getsignal(signal.SIGTERM)

    try:
        main_module.main()
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is not original_handler

        handler(signal.SIGTERM, None)
        assert fake_scheduler.shutdown_called_with is False
    finally:
        signal.signal(signal.SIGTERM, original_handler)


def test_main_builds_cron_trigger_from_settings(base_env, fake_scheduler, monkeypatch):
    from apscheduler.triggers.cron import CronTrigger

    monkeypatch.setattr(main_module, "run_sync_cycle", lambda: {})
    main_module.main()

    trigger = fake_scheduler.jobs[0]["trigger"]
    assert isinstance(trigger, CronTrigger)


def test_main_shuts_down_gracefully_on_keyboard_interrupt(base_env, fake_scheduler, monkeypatch):
    monkeypatch.setattr(main_module, "run_sync_cycle", lambda: {})

    def raise_keyboard_interrupt():
        raise KeyboardInterrupt

    fake_scheduler.start = raise_keyboard_interrupt

    main_module.main()  # must not propagate the KeyboardInterrupt
