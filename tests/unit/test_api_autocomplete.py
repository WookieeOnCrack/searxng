# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/autocomplete endpoint."""

from fastapi.testclient import TestClient

from backend.app import app
from backend.services.kernel import init_kernel

client = TestClient(app)


def setup_module():
    init_kernel(check_network=False)


def test_autocomplete_returns_list():
    res = client.get("/api/v1/autocomplete", params={"q": "sea", "lang": "en", "count": 5})
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["suggestions"], list)


def test_autocomplete_empty_query():
    res = client.get("/api/v1/autocomplete", params={"q": "", "lang": "en"})
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["suggestions"], list)
