# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the kernel initialization."""
from backend.services.kernel import init_kernel


def test_init_kernel_is_idempotent():
    init_kernel(check_network=False)
    init_kernel(check_network=False)  # should not raise


def test_init_kernel_loads_settings():
    from searx import settings
    init_kernel(check_network=False)
    assert "engines" in settings
    assert "search" in settings


def test_init_kernel_initializes_engines():
    from searx.engines import engines
    init_kernel(check_network=False)
    assert len(engines) > 0
