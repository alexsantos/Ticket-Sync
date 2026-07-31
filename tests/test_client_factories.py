from types import SimpleNamespace

from src.hr_client import build_hr_client
from src.ops_client import build_ops_client


def test_build_hr_client_uses_hr_settings():
    settings = SimpleNamespace(
        hr_api_base_url="https://hr.example.com/",
        hr_api_key="hr-secret",
        http_timeout=12.0,
    )
    client = build_hr_client(settings)
    assert str(client._client.base_url) == "https://hr.example.com"
    assert client._client.headers["x-api-key"] == "hr-secret"
    assert client._client.timeout.read == 12.0


def test_build_ops_client_uses_ops_settings():
    settings = SimpleNamespace(
        ops_api_base_url="https://ops.example.com/",
        ops_api_key="ops-secret",
        http_timeout=8.0,
    )
    client = build_ops_client(settings)
    assert str(client._client.base_url) == "https://ops.example.com"
    assert client._client.headers["x-api-key"] == "ops-secret"
    assert client._client.timeout.read == 8.0
