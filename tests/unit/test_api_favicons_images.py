# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/favicons and /api/v1/images endpoints."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_favicon_returns_image():
    res = client.get("/api/v1/favicons", params={"authority": "example.com"})
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("image/")


def test_favicon_requires_authority():
    res = client.get("/api/v1/favicons")
    assert res.status_code == 422


def test_image_proxy_returns_image():
    # Mock the kernel's network layer so the test is network-independent.
    # The endpoint binds it as `from searx.network import stream as http_stream`,
    # so patch the local name in `backend.api.images`.
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {
        "Content-Type": "image/png",
        "Content-Length": "8",
    }
    png_bytes = b"\x89PNG\r\n\x1a\n"
    mock_stream = iter([png_bytes])

    with patch("backend.api.images.http_stream", return_value=(mock_resp, mock_stream)):
        res = client.get("/api/v1/images", params={"url": "https://example.com/img.png"})
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("image/")
