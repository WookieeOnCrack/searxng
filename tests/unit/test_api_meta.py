# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the /api/v1/meta endpoint."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)

#: ``/meta`` owns instance-wide metadata only. The engine, category, locale
#: and theme catalogues live in ``/engines`` and ``/preferences``.
CATALOGUE_FIELDS = {"engines", "categories", "locales", "styles", "default_theme"}


def test_meta_returns_exactly_the_instance_metadata():
    res = client.get("/api/v1/meta")
    assert res.status_code == 200
    assert set(res.json()) == {"brand", "version", "formats"}


def test_meta_does_not_duplicate_the_catalogues():
    data = client.get("/api/v1/meta").json()
    for field in CATALOGUE_FIELDS:
        assert field not in data


def test_meta_reports_the_instance_version_and_formats():
    data = client.get("/api/v1/meta").json()
    assert data["version"]
    assert isinstance(data["formats"], list) and len(data["formats"]) > 0
    assert isinstance(data["brand"], dict)
