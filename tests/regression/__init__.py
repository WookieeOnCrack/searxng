# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

import os
from pathlib import Path

# By default, the user settings from regression/settings/test_settings.yml are used.

os.environ['SEARXNG_SETTINGS_PATH'] = str(Path(__file__).parent / "settings" / "test_settings.yml")
