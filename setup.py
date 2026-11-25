# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installer for SearXNG package.

All metadata is declared in 'pyproject.toml'.  The version is derived from the
git repository (see 'searx/version.py').
"""

import pathlib
import sys

from setuptools import setup

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from searx.version import VERSION_TAG  # pylint: disable=wrong-import-position

setup(version=VERSION_TAG)
