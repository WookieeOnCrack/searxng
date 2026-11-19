# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the client preference document service."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

# Importing backend.app runs create_app(), which calls init_kernel().
# build_preferences() needs the initialized kernel: searx.favicons.proxy.CFG
# is None until then and constructing Preferences raises AttributeError.
import backend.app  # noqa: F401  pylint: disable=unused-import

from searx.engines import categories as engine_categories
from searx.preferences import MAP_STR2BOOL

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


def test_locked_setting_is_skipped():
    """A locked setting is not applied upstream, so it must not be reported as applied."""
    prefs = build_preferences()
    prefs.key_value_settings["safesearch"].locked = True
    doc, skipped = filter_document({"safesearch": 2}, prefs)
    assert "safesearch" not in doc
    assert any("safesearch" in note for note in skipped)


def test_safesearch_accepts_int_and_str():
    """The map setting accepts its integer values and their string keys."""
    prefs = build_preferences()
    doc, skipped = filter_document({"safesearch": 2}, prefs)
    assert doc["safesearch"] == 2
    assert not skipped
    doc, skipped = filter_document({"safesearch": "1"}, prefs)
    assert doc["safesearch"] == "1"
    assert not skipped


def test_safesearch_rejects_bool():
    """``True`` is an int in Python, but it is not a map value."""
    prefs = build_preferences()
    doc, skipped = filter_document({"safesearch": True}, prefs)
    assert "safesearch" not in doc
    assert any("safesearch" in note for note in skipped)


def test_safesearch_rejects_out_of_range():
    """A value outside the map is skipped, not clamped."""
    prefs = build_preferences()
    doc, skipped = filter_document({"safesearch": 5}, prefs)
    assert "safesearch" not in doc
    assert any("safesearch" in note for note in skipped)


def test_boolean_accepts_every_map_str2bool_key():
    """Every string upstream's MAP_STR2BOOL knows must be accepted."""
    prefs = build_preferences()
    for text in MAP_STR2BOOL:
        doc, skipped = filter_document({"image_proxy": text}, prefs)
        assert doc["image_proxy"] == text, text
        assert not skipped, text


def test_categories_all_or_nothing():
    """One unknown category skips the whole list -- elements are never dropped."""
    prefs = build_preferences()
    doc, skipped = filter_document({"categories": ["general", "no such category"]}, prefs)
    assert "categories" not in doc
    assert any("categories" in note for note in skipped)


def test_categories_none_sentinel_accepted():
    """Upstream's own "none" sentinel is part of the categories contract."""
    prefs = build_preferences()
    doc, skipped = filter_document({"categories": ["none"]}, prefs)
    assert doc["categories"] == ["none"]
    assert not skipped


def test_categories_wrong_shape_skipped():
    """A categories value that is not a list of strings is skipped."""
    prefs = build_preferences()
    doc, skipped = filter_document({"categories": "general"}, prefs)
    assert "categories" not in doc
    assert any("categories" in note for note in skipped)


def test_default_category_none_rejected():
    """The fork-only key takes the real categories, not the "none" sentinel."""
    assert "none" not in engine_categories
    prefs = build_preferences()
    doc, skipped = filter_document({"default_category": "none"}, prefs)
    assert "default_category" not in doc
    assert any("default_category" in note for note in skipped)


def test_normalize_emits_all_engine_and_plugin_keys():
    """An empty document still yields all four engine/plugin keys."""
    prefs = build_preferences()
    normalized = normalize_document({}, prefs)
    for key in ("disabled_engines", "enabled_engines", "disabled_plugins", "enabled_plugins"):
        assert normalized[key] == ""


def test_normalize_drops_the_fork_only_key():
    """``default_category`` is not an upstream key, so it is not normalized."""
    prefs = build_preferences()
    doc, _ = filter_document({"default_category": "general"}, prefs)
    assert "default_category" not in normalize_document(doc, prefs)


def test_normalize_rejects_an_unfiltered_value():
    """An unfiltered document raises instead of normalizing to a wrong value."""
    prefs = build_preferences()
    try:
        normalize_document({"image_proxy": "yes"}, prefs)
    except ValueError:
        pass
    else:
        raise AssertionError("an invalid boolean value was normalized")


def test_good_keys_survive_bad_ones():
    """A document with several bad keys still applies every good key."""
    prefs = build_preferences()
    doc, skipped = filter_document(
        {
            "nope": 1,
            "image_proxy": "yes",
            "language": "xx-INVALID",
            "disabled_engines": ["no such engine__general"],
            "categories": ["general"],
        },
        prefs,
    )
    assert doc == {"categories": ["general"]}
    assert len(skipped) == 4


def test_tokens_error_carries_status_and_message():
    """The error carries the status code and the machine-readable code."""
    prefs = build_preferences()
    try:
        filter_document({"tokens": {}}, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_tokens_not_allowed"
        assert exc.message
        assert str(exc) == exc.message
    else:
        raise AssertionError("tokens was accepted")
