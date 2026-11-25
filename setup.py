# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installer for SearXNG package."""

from setuptools import find_packages, setup

from searx.version import VERSION_TAG

setup(
    name='searxng',
    version=VERSION_TAG,
    packages=find_packages(
        include=[
            'searx',
            'searx.*',
            'searx.*.*',
            'searx.*.*.*',
            'backend',
            'backend.*',
        ]
    ),
    package_data={
        'searx': [
            'settings.yml',
            '*.toml',
            '*.msg',
            'data/*.json',
            'data/*.txt',
            'data/*.ftz',
            'favicons/*.toml',
            'infopage/**',
            'static/**',
            'templates/**',
            'translations/**',
        ],
    }
)
