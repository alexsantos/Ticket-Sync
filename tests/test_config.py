import pytest

from src.config import CreateConfig, SearchConfig, get_settings, load_create_config, load_search_config


def write_yaml(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


# --- load_search_config ---

def test_load_search_config_full(tmp_path):
    path = write_yaml(tmp_path, "search.yaml", """
search:
  status: closed
  dept_id: 3
  topic_id: 17
  custom_fields:
    route_to: "Ops"
  updated_after_lookback_days: 10
  page_size: 25
""")
    cfg = load_search_config(path)
    assert cfg == SearchConfig(
        status="closed",
        dept_id=3,
        topic_id=17,
        custom_fields={"route_to": "Ops"},
        updated_after_lookback_days=10,
        page_size=25,
    )


def test_load_search_config_defaults(tmp_path):
    path = write_yaml(tmp_path, "search.yaml", """
search:
  status: closed
""")
    cfg = load_search_config(path)
    assert cfg.dept_id is None
    assert cfg.topic_id is None
    assert cfg.custom_fields == {}
    assert cfg.updated_after_lookback_days == 30
    assert cfg.page_size == 50


def test_load_search_config_missing_section_raises(tmp_path):
    path = write_yaml(tmp_path, "search.yaml", "not_search: {}\n")
    with pytest.raises(ValueError, match="search"):
        load_search_config(path)


def test_load_search_config_missing_status_raises(tmp_path):
    path = write_yaml(tmp_path, "search.yaml", "search:\n  dept_id: 3\n")
    with pytest.raises(ValueError, match="status"):
        load_search_config(path)


# --- load_create_config ---

def test_load_create_config_full(tmp_path):
    path = write_yaml(tmp_path, "create.yaml", """
create:
  base_url_env: OPS_API_BASE_URL
  dept_id: 8
  topic_id: 4
  user_id: 91
  subject_template: "[From HR #{hr_number}] {hr_subject}"
  message_template: "Forwarded: {hr_message}"
""")
    cfg = load_create_config(path)
    assert cfg == CreateConfig(
        user_id=91,
        subject_template="[From HR #{hr_number}] {hr_subject}",
        message_template="Forwarded: {hr_message}",
        base_url_env="OPS_API_BASE_URL",
        dept_id=8,
        topic_id=4,
    )


def test_load_create_config_defaults(tmp_path):
    path = write_yaml(tmp_path, "create.yaml", """
create:
  user_id: 91
  subject_template: "s"
  message_template: "m"
""")
    cfg = load_create_config(path)
    assert cfg.base_url_env == "OPS_API_BASE_URL"
    assert cfg.dept_id is None
    assert cfg.topic_id is None


@pytest.mark.parametrize("missing_field", ["user_id", "subject_template", "message_template"])
def test_load_create_config_missing_required_field_raises(tmp_path, missing_field):
    fields = {"user_id": 91, "subject_template": "s", "message_template": "m"}
    del fields[missing_field]
    body = "create:\n" + "".join(f"  {k}: {v!r}\n" for k, v in fields.items())
    path = write_yaml(tmp_path, "create.yaml", body)
    with pytest.raises(ValueError, match=missing_field):
        load_create_config(path)


def test_load_create_config_missing_section_raises(tmp_path):
    path = write_yaml(tmp_path, "create.yaml", "not_create: {}\n")
    with pytest.raises(ValueError, match="create"):
        load_create_config(path)


# --- get_settings ---

@pytest.fixture
def clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_settings_reads_env(monkeypatch, clean_settings_cache):
    monkeypatch.setenv("HR_API_BASE_URL", "https://hr.example.com")
    monkeypatch.setenv("HR_API_KEY", "hr-key")
    monkeypatch.setenv("OPS_API_BASE_URL", "https://ops.example.com")
    monkeypatch.setenv("OPS_API_KEY", "ops-key")

    settings = get_settings()
    assert settings.hr_api_base_url == "https://hr.example.com"
    assert settings.ops_api_key == "ops-key"
    assert settings.sync_cron == "*/15 * * * *"


def test_get_settings_is_cached(monkeypatch, clean_settings_cache):
    monkeypatch.setenv("HR_API_BASE_URL", "https://hr.example.com")
    monkeypatch.setenv("HR_API_KEY", "hr-key")
    monkeypatch.setenv("OPS_API_BASE_URL", "https://ops.example.com")
    monkeypatch.setenv("OPS_API_KEY", "ops-key")

    assert get_settings() is get_settings()


def test_get_settings_missing_required_raises(tmp_path, monkeypatch, clean_settings_cache):
    monkeypatch.delenv("HR_API_BASE_URL", raising=False)
    monkeypatch.delenv("HR_API_KEY", raising=False)
    monkeypatch.delenv("OPS_API_BASE_URL", raising=False)
    monkeypatch.delenv("OPS_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here to accidentally satisfy the required fields

    with pytest.raises(Exception):
        get_settings()
