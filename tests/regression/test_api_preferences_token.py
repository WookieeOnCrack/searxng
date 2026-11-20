# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the client preference token envelope."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

import base64
import json
import zlib

from fastapi.testclient import TestClient

from backend.app import app
from backend.services.preferences import (
    build_preferences,
    filter_document,
    mint_token,
    sign_payload,
)

client = TestClient(app)


def test_token_envelope_shape():
    res = client.post("/api/v1/preferences", json={"categories": ["general"]})
    assert res.status_code == 200
    token = res.json()["token"]
    payload, _, signature = token.partition(".")
    assert len(signature) == 64
    raw = zlib.decompress(base64.urlsafe_b64decode(payload))
    body = json.loads(raw)
    assert set(body) == {"epoch", "doc"}
    assert len(body["epoch"]) == 16
    assert body["doc"]["categories"] == ["general"]


def test_error_codes_are_stable():
    res = client.post("/api/v1/preferences", json={"tokens": {"e": "s"}})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("preferences_tokens_not_allowed")


def _restamp(token: str, epoch: str) -> str:
    """Re-encode a token with a different epoch and re-sign it."""
    payload, _, _ = token.partition(".")
    body = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload)))
    body["epoch"] = epoch
    fresh = base64.urlsafe_b64encode(
        zlib.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    ).decode("ascii")
    return fresh + "." + sign_payload(fresh)


def test_bad_signature_is_403():
    """A token whose signature does not verify is a 403, not a 400."""
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    payload, _, _ = token.partition(".")
    res = client.get(
        "/api/v1/preferences",
        headers={"X-SearXNG-Preferences": payload + "." + ("0" * 64)},
    )
    assert res.status_code == 403
    assert res.json()["detail"].startswith("preferences_invalid_signature")


def test_stale_token_is_409():
    """A validly signed token whose epoch no longer matches is a 409."""
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    stale = _restamp(mint_token(doc, prefs), "0" * 16)
    res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": stale})
    assert res.status_code == 409
    assert res.json()["detail"].startswith("preferences_stale")


def test_bad_token_is_400_not_500():
    """A garbage token must be a 400, never an unhandled 500."""
    for bad in ("abc", "eyJ9", "eJzLyy9RyCrOzwMADiYDLA==", "not-a-token"):
        res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": bad})
        assert res.status_code == 400, f"{bad!r} gave {res.status_code}"
        assert res.json()["detail"].startswith("preferences_invalid")
