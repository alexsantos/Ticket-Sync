from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    hr_api_base_url: str
    hr_api_key: str
    ops_api_base_url: str
    ops_api_key: str

    search_config_path: str = "config/search.yaml"
    create_config_path: str = "config/create.yaml"

    sync_cron: str = "*/15 * * * *"
    state_db_path: str = "ticket_sync.db"

    http_timeout: float = 30.0
    log_level: str = "INFO"


@dataclass
class SearchConfig:
    status: str
    dept_id: Optional[int] = None
    topic_id: Optional[int] = None
    custom_fields: dict[str, str] = field(default_factory=dict)
    updated_after_lookback_days: int = 30
    page_size: int = 50


@dataclass
class CreateConfig:
    user_id: int
    subject_template: str
    message_template: str
    base_url_env: str = "OPS_API_BASE_URL"
    dept_id: Optional[int] = None
    topic_id: Optional[int] = None


def load_search_config(path: str) -> SearchConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    search = data.get("search") if data else None
    if not search:
        raise ValueError(f"No 'search' section defined in {path}")
    if not search.get("status"):
        raise ValueError(f"'search.status' is required in {path}")

    return SearchConfig(
        status=search["status"],
        dept_id=search.get("dept_id"),
        topic_id=search.get("topic_id"),
        custom_fields=search.get("custom_fields") or {},
        updated_after_lookback_days=search.get("updated_after_lookback_days", 30),
        page_size=search.get("page_size", 50),
    )


def load_create_config(path: str) -> CreateConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    create = data.get("create") if data else None
    if not create:
        raise ValueError(f"No 'create' section defined in {path}")
    if create.get("user_id") is None:
        raise ValueError(f"'create.user_id' is required in {path}")
    if not create.get("subject_template"):
        raise ValueError(f"'create.subject_template' is required in {path}")
    if not create.get("message_template"):
        raise ValueError(f"'create.message_template' is required in {path}")

    return CreateConfig(
        user_id=create["user_id"],
        subject_template=create["subject_template"],
        message_template=create["message_template"],
        base_url_env=create.get("base_url_env", "OPS_API_BASE_URL"),
        dept_id=create.get("dept_id"),
        topic_id=create.get("topic_id"),
    )


@lru_cache
def get_settings() -> Settings:
    """Constructed lazily (not at import time) so importing this module - e.g.
    from tests that only need load_search_config/load_create_config - doesn't
    require HR/Ops credentials to be present in the environment."""
    return Settings()  # type: ignore[call-arg]
