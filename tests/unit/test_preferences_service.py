# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the client preference document service."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

# Importing backend.app runs create_app(), which calls init_kernel().
# build_preferences() needs the initialized kernel: searx.favicons.proxy.CFG
# is None until then and constructing Preferences raises AttributeError.
import backend.app  # noqa: F401  pylint: disable=unused-import

from backend.services.preferences import (
    PreferencesError,
    build_preferences,
    filter_document,
    normalize_document,
)


def test_unknown_key_skipped():
    prefs = build_preferences()
    doc, skipped = filter_document({"nope": 1, "categories": ["general"]}, prefs)
    assert "nope" not in doc
    assert doc["categories"] == ["general"]
    assert any("nope" in note for note in skipped)


def test_tokens_key_rejected():
    prefs = build_preferences()
    try:
        filter_document({"tokens": {"engine": "secret"}}, prefs)
    except PreferencesError as exc:
        assert exc.code == "preferences_tokens_not_allowed"
    else:
        raise AssertionError("tokens was accepted")


def test_bad_bool_skipped():
    prefs = build_preferences()
    doc, skipped = filter_document({"image_proxy": "yes"}, prefs)
    assert "image_proxy" not in doc
    assert any("image_proxy" in note for note in skipped)


def test_bad_language_skipped():
    prefs = build_preferences()
    doc, skipped = filter_document({"language": "xx-INVALID"}, prefs)
    assert "language" not in doc
    assert any("language" in note for note in skipped)


def test_unknown_engine_skipped():
    prefs = build_preferences()
    doc, skipped = filter_document({"disabled_engines": ["no such engine__general"]}, prefs)
    assert "disabled_engines" not in doc
    assert any("disabled_engines" in note for note in skipped)


def test_unknown_plugin_skipped():
    prefs = build_preferences()
    doc, skipped = filter_document({"disabled_plugins": ["no_such_plugin"]}, prefs)
    assert "disabled_plugins" not in doc
    assert any("disabled_plugins" in note for note in skipped)


def test_unknown_default_category():
    prefs = build_preferences()
    doc, skipped = filter_document({"default_category": "no such category"}, prefs)
    assert "default_category" not in doc
    assert any("default_category" in note for note in skipped)


def test_known_default_category():
    prefs = build_preferences()
    doc, skipped = filter_document({"default_category": "general"}, prefs)
    assert doc["default_category"] == "general"
    # Equivalent to the plan's `skipped == []`, but the repo's pylint config
    # enables `use-implicit-booleaness-not-comparison` (C1803), which rejects
    # the comparison against an empty list.
    assert not skipped


def test_enabled_only_applies():
    prefs = build_preferences()
    # Take a real pair from the live registry -- the unit-test registry
    # has only the two dummy engines, so a hardcoded name would be skipped.
    pair = sorted(prefs.engines.choices)[0]
    doc, _ = filter_document({"enabled_engines": [pair]}, prefs)
    normalized = normalize_document(doc, prefs)
    # Upstream dispatches the engine branch on the presence of the
    # disabled_engines key, so both keys must always be present.
    assert normalized["disabled_engines"] == ""
    assert normalized["enabled_engines"] == pair


def test_boolean_normalization():
    """The boolean string forms must normalize by lookup, not truthiness."""
    prefs = build_preferences()
    doc, _ = filter_document({"image_proxy": "none"}, prefs)
    # "none" is in MAP_STR2BOOL and maps to False -> "0", not "1".
    assert normalize_document(doc, prefs)["image_proxy"] == "0"
    doc, _ = filter_document({"image_proxy": "on"}, prefs)
    assert normalize_document(doc, prefs)["image_proxy"] == "1"
    doc, _ = filter_document({"image_proxy": True}, prefs)
    assert normalize_document(doc, prefs)["image_proxy"] == "1"
    doc, _ = filter_document({"image_proxy": False}, prefs)
    assert normalize_document(doc, prefs)["image_proxy"] == "0"
