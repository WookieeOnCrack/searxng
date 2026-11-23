# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests that blocking API work runs outside the event-loop thread."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import searx.favicons
from backend.api.autocomplete import autocomplete
from backend.api.favicons import favicon
from backend.api.images import image_proxy
from backend.services.kernel import init_kernel


def setup_module():
    searx.favicons.init()
    init_kernel(check_network=False)


def test_autocomplete_worker():
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def fake_search_autocomplete(_backend_name, _query, _locale):
        worker_threads.append(threading.get_ident())
        return ["suggestion"]

    with patch("backend.api.autocomplete.answerers_storage.ask", return_value=[]):
        with patch("backend.api.autocomplete.search_autocomplete", side_effect=fake_search_autocomplete):
            response = asyncio.run(autocomplete(q="probe", lang="en", method="POST", count=5))

    assert response.suggestions == ["suggestion"]
    assert worker_threads
    assert worker_threads[0] != event_loop_thread


def test_favicon_worker():
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def fake_search_favicon(_resolver, _authority):
        worker_threads.append(threading.get_ident())
        return b"icon", "image/png"

    with patch.dict(
        "backend.api.favicons.settings",
        {"server": {"secret_key": "test-secret"}, "search": {"favicon_resolver": "test-resolver"}},
    ):
        with patch("backend.api.favicons.search_favicon", side_effect=fake_search_favicon):
            response = asyncio.run(favicon(authority="example.com", h="", format="png", size=64))

    assert response.media_type == "image/png"
    assert worker_threads
    assert worker_threads[0] != event_loop_thread


def test_image_proxy_worker():
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {"Content-Type": "image/png", "Content-Length": "8"}

    def fake_http_stream(**_kwargs):
        worker_threads.append(threading.get_ident())
        return upstream, iter([b"image-bytes"])

    with patch("backend.api.images.http_stream", side_effect=fake_http_stream):
        response = asyncio.run(image_proxy(url="https://example.com/image.png", h=""))

    assert response.media_type == "image/png"
    assert worker_threads
    assert worker_threads[0] != event_loop_thread
