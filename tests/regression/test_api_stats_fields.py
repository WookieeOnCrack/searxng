# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression Test for the /api/v1/stats endpoint returned fields."""

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_stats_engines_fields():
    '''Required fields for engine stats'''
    res = client.get("/api/v1/stats")
    data = res.json()
    for engine in data["engines"]:
        assert "name" in engine
        assert "url" in engine
        assert "category" in engine
        assert "title" in engine
        assert "result_count" in engine
        assert "error_count" in engine
        assert "score_per_result" in engine
        assert "time" in engine
