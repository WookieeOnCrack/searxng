# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the API model factory methods and their ordering."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

from backend.models.api import CategoryMeta, EngineMeta, LocaleMeta, PluginMeta


def test_plugin_meta_sorts_with_null_section():
    """A plugin without a preference section must still be sortable.

    ``PluginInfo.preference_section`` is nullable, so two catalogue
    entries that share an id can differ in it. Comparing ``None`` with a
    string raises, which would turn a sort into a 500.
    """
    without = PluginMeta(id="tracker_url_remover", name="Tracker URL Remover", preference_section=None)
    with_section = PluginMeta(id="tracker_url_remover", name="Tracker URL Remover", preference_section="privacy")
    ordered = sorted([with_section, without])
    assert [plugin.preference_section for plugin in ordered] == [None, "privacy"]


def test_engine_meta_sorts_by_name():
    """Engines are ordered by name, then category, then shortcut."""
    first = EngineMeta(name="alpha", url="", description="", category="general")
    second = EngineMeta(name="beta", url="", description="", category="general")
    assert [engine.name for engine in sorted([second, first])] == ["alpha", "beta"]


def test_category_meta_sorts_by_name():
    """Categories are ordered by name."""
    first = CategoryMeta(name="general", title="General")
    second = CategoryMeta(name="images", title="Images")
    assert [category.name for category in sorted([second, first])] == ["general", "images"]


def test_locale_meta_sorts_by_tag():
    """Locales are ordered by tag."""
    first = LocaleMeta(tag="de", title="Deutsch")
    second = LocaleMeta(tag="en", title="English")
    assert [locale.tag for locale in sorted([second, first])] == ["de", "en"]
