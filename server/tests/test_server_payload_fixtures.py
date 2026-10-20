"""Payload mẫu server -> điện thoại có đúng hình dạng mà app Android đọc."""

import json
from pathlib import Path

import pytest

from app.actions import Action

FIXTURES = Path(__file__).parent / "fixtures" / "server_payloads"
EXPECTED_KEYS = {
    "request_id",
    "user_id",
    "device_id",
    "action",
    "params_json",
    "issued_at",
    "deadline_at",
    "expires_at",
    "requested_priority",
}
# Khoá bổ sung (additive) theo action: navigation_start mang maps_url cho app mở Google Maps.
EXTRA_KEYS = {"navigation_start": {"maps_url"}}


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_fixture_matches_command_payload_shape(path: Path) -> None:
    """Mỗi fixture có đủ khoá, giá trị chuỗi, params_json là JSON object."""
    data = json.loads(path.read_text(encoding="utf-8"))

    assert set(data) == EXPECTED_KEYS | EXTRA_KEYS.get(path.stem, set())
    assert all(isinstance(value, str) for value in data.values())
    assert data["action"] == path.stem
    assert isinstance(json.loads(data["params_json"]), dict)


def test_fixtures_cover_nine_actions() -> None:
    """Đủ 9 action, mỗi tên là một Action hợp lệ của server."""
    names = {p.stem for p in FIXTURES.glob("*.json")}

    assert len(names) == 9
    assert names <= {a.value for a in Action}
