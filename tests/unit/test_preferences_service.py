# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the client preference document service."""

# pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

# Importing backend.app runs create_app(), which calls init_kernel().
# build_preferences() needs the initialized kernel: searx.favicons.proxy.CFG
# is None until then and constructing Preferences raises AttributeError.
import base64
import json
import zlib

import msgspec

import backend.app  # noqa: F401  pylint: disable=unused-import

from searx.engines import categories as engine_categories
from searx.preferences import MAP_STR2BOOL, EnumStringSetting

from backend.services.preferences import (
    MAX_PAYLOAD_BYTES,
    PreferencesError,
    build_preferences,
    compute_epoch,
    decode_token,
    filter_document,
    mint_token,
    normalize_document,
    sign_payload,
    verify_token,
)


def _other_epoch() -> str:
    """Return a 16-char hex epoch that cannot collide with a real one."""
    return "0" * 16


def _skeleton() -> bytes:
    """Return the JSON body of a token whose document is a single empty padding key."""
    return json.dumps({"epoch": _other_epoch(), "doc": {"p": ""}}, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _padding_body(size: int) -> bytes:
    """Return a token body that decompresses to exactly ``size`` bytes."""
    padding = size - len(_skeleton())
    body = json.dumps(
        {"epoch": _other_epoch(), "doc": {"p": "a" * padding}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(body) == size
    return body


def _restamp(token: str, epoch: str) -> str:
    """Re-encode a token with a different epoch and re-sign it."""
    payload, _, _ = token.partition(".")
    body = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload)))
    body["epoch"] = epoch
    fresh = base64.urlsafe_b64encode(
        zlib.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    ).decode("ascii")
    return fresh + "." + sign_payload(fresh)


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


def test_token_roundtrip():
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    verified, skipped = verify_token(token, prefs)
    assert verified["categories"] == ["general"]
    assert not skipped


def test_bad_signature_rejected():
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    payload, _, _ = token.partition(".")
    try:
        verify_token(payload + "." + ("0" * 64), prefs)
    except PreferencesError as exc:
        assert exc.status_code == 403
        assert exc.code == "preferences_invalid_signature"
    else:
        raise AssertionError("bad signature was accepted")


def test_malformed_token_rejected():
    prefs = build_preferences()
    try:
        verify_token("not-a-token", prefs)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_invalid"
    else:
        raise AssertionError("malformed token was accepted")


def test_undecodable_token_rejected():
    """A token that fails to decode is a 400, not an uncaught exception."""
    prefs = build_preferences()
    # "abc" is not valid padded base64 -> binascii.Error if uncaught.
    # "eyJ9" is valid base64 but not zlib -> zlib.error.
    # "eJzLyy9RyCrOzwMADiYDLA==" is valid zlib but not JSON -> JSONDecodeError.
    # "a.b" -> binascii.Error; "!!!" -> decodes to empty -> JSONDecodeError.
    for bad in ("abc", "eyJ9", "eJzLyy9RyCrOzwMADiYDLA==", "a.b", "!!!"):
        try:
            verify_token(bad, prefs)
        except PreferencesError as exc:
            assert exc.status_code == 400, f"{bad!r} gave {exc.status_code}"
            assert exc.code == "preferences_invalid", f"{bad!r} gave {exc.code}"
        else:
            raise AssertionError(f"malformed token {bad!r} was accepted")


def test_stale_token_rejected():
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    stale = _restamp(token, _other_epoch())
    try:
        verify_token(stale, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 409
        assert exc.code == "preferences_stale"
    else:
        raise AssertionError("stale token was accepted")


def test_epoch_tracks_default():
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    first = compute_epoch(doc, prefs)
    assert first == compute_epoch(doc, prefs)
    assert len(first) == 16


def test_vanished_pair_is_stale():
    """A named pair the instance no longer has makes the token stale."""
    prefs = build_preferences()
    pair = sorted(prefs.engines.choices)[0]
    doc, _ = filter_document({"disabled_engines": [pair]}, prefs)
    token = mint_token(doc, prefs)
    # Simulate the engine being removed after the token was minted:
    # the pair's default state becomes unavailable (None).
    vanished = dict(prefs.engines.default_choices)
    vanished.pop(pair, None)
    try:
        prefs.engines.default_choices = vanished
        verify_token(token, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 409
        assert exc.code == "preferences_stale"
    else:
        raise AssertionError("a token naming a vanished engine was accepted")


def test_dropped_setting_is_skipped():
    """A key_value_settings value the instance dropped is skipped, not stale.

    The epoch covers engine pairs, plugins and locks -- not the
    key_value_settings defaults -- so losing a setting value does not
    invalidate the token. The key is simply no longer applicable, and
    verify_token must report it so the client learns its preference
    stopped applying.
    """
    prefs = build_preferences()
    theme_setting = prefs.key_value_settings["theme"]
    assert isinstance(theme_setting, EnumStringSetting)
    theme = theme_setting.get_value()
    doc, _ = filter_document({"theme": theme}, prefs)
    token = mint_token(doc, prefs)
    # Remove the theme value from the instance's choices.
    theme_setting.choices = [c for c in theme_setting.choices if c != theme]
    verified, skipped = verify_token(token, prefs)
    assert not verified
    assert any("theme" in note for note in skipped)


def test_unsigned_token_is_accepted():
    """An unsigned token carries no verifiable epoch, so only that check is skipped."""
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    signed = mint_token(doc, prefs)
    # An unsigned token with a stale epoch is still accepted: it carries no
    # verifiable epoch, so there is nothing to compare against.
    unsigned = _restamp(signed, _other_epoch()).partition(".")[0]
    verified, skipped = verify_token(unsigned, prefs)
    assert verified["categories"] == ["general"]
    assert not skipped


def test_oversized_payload_rejected():
    """A payload that decompresses beyond the limit is a 400, not a memory blow-up."""
    prefs = build_preferences()
    body = json.dumps(
        {"epoch": _other_epoch(), "doc": {"padding": "a" * (MAX_PAYLOAD_BYTES + 1000)}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = base64.urlsafe_b64encode(zlib.compress(body)).decode("ascii")
    try:
        verify_token(payload + "." + sign_payload(payload), prefs)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_invalid"
    else:
        raise AssertionError("an oversized token payload was accepted")


def test_non_ascii_signature_rejected():
    """A signature that cannot be compared is a 403, not an uncaught TypeError."""
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    payload, _, _ = token.partition(".")
    # hmac.compare_digest raises TypeError on a non-ASCII string.
    try:
        verify_token(payload + "." + ("0" * 63 + "é"), prefs)
    except PreferencesError as exc:
        assert exc.status_code == 403
        assert exc.code == "preferences_invalid_signature"
    else:
        raise AssertionError("a non-ASCII signature was accepted")


def test_oversized_token_string_rejected():
    """A token string far larger than any legal one is refused before decoding."""
    prefs = build_preferences()
    huge = "A" * (4 * MAX_PAYLOAD_BYTES + 1)
    try:
        verify_token(huge, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_invalid"
    else:
        raise AssertionError("an oversized token string was accepted")


def test_truncated_zlib_stream_rejected():
    """A payload truncated after its data blocks is malformed, not valid JSON."""
    prefs = build_preferences()
    body = json.dumps({"epoch": _other_epoch(), "doc": {}}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(body)
    # Drop the adler32 checksum: the data blocks are complete, so the
    # decompressor emits the full JSON -- but the stream never ends.
    truncated = base64.urlsafe_b64encode(compressed[:-4]).decode("ascii")
    try:
        verify_token(truncated + "." + sign_payload(truncated), prefs)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_invalid"
    else:
        raise AssertionError("a truncated zlib stream was accepted")


def test_trailing_garbage_after_stream_rejected():
    """A second member appended after a valid zlib stream is malformed."""
    prefs = build_preferences()
    body = json.dumps({"epoch": _other_epoch(), "doc": {}}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    padded = base64.urlsafe_b64encode(zlib.compress(body) + b"garbage").decode("ascii")
    try:
        verify_token(padded + "." + sign_payload(padded), prefs)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_invalid"
    else:
        raise AssertionError("a token with trailing garbage was accepted")


def test_payload_exactly_at_limit_is_accepted():
    """A payload that decompresses to exactly MAX_PAYLOAD_BYTES is legal."""
    body = _padding_body(MAX_PAYLOAD_BYTES)
    assert len(body) == MAX_PAYLOAD_BYTES
    payload = base64.urlsafe_b64encode(zlib.compress(body)).decode("ascii")
    doc, epoch, signed = decode_token(payload)
    assert signed is False
    assert epoch == _other_epoch()
    assert doc == {"p": "a" * (MAX_PAYLOAD_BYTES - len(_skeleton()))}


def test_payload_one_byte_over_limit_rejected():
    """A payload one byte over the limit is a 400."""
    body = _padding_body(MAX_PAYLOAD_BYTES + 1)
    assert len(body) == MAX_PAYLOAD_BYTES + 1
    payload = base64.urlsafe_b64encode(zlib.compress(body)).decode("ascii")
    try:
        decode_token(payload)
    except PreferencesError as exc:
        assert exc.status_code == 400
        assert exc.code == "preferences_invalid"
    else:
        raise AssertionError("a payload one byte over the limit was accepted")


def test_flipped_engine_default_is_stale():
    """Flipping the default state of a named engine pair makes the token stale."""
    prefs = build_preferences()
    pair = sorted(prefs.engines.choices)[0]
    doc, _ = filter_document({"disabled_engines": [pair]}, prefs)
    token = mint_token(doc, prefs)
    flipped = dict(prefs.engines.default_choices)
    flipped[pair] = not flipped[pair]
    try:
        prefs.engines.default_choices = flipped
        verify_token(token, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 409
        assert exc.code == "preferences_stale"
    else:
        raise AssertionError("a token naming a re-defaulted engine was accepted")


def test_flipped_plugin_default_is_stale():
    """Flipping the default state of a named plugin makes the token stale."""
    prefs = build_preferences()
    plugin = sorted(prefs.plugins.choices)[0]
    doc, _ = filter_document({"disabled_plugins": [plugin]}, prefs)
    token = mint_token(doc, prefs)
    flipped = dict(prefs.plugins.default_choices)
    flipped[plugin] = not flipped[plugin]
    try:
        prefs.plugins.default_choices = flipped
        verify_token(token, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 409
        assert exc.code == "preferences_stale"
    else:
        raise AssertionError("a token naming a re-defaulted plugin was accepted")


def test_changed_lock_set_is_stale():
    """The epoch covers the lock set, so a new lock makes the token stale."""
    prefs = build_preferences()
    doc, _ = filter_document({"categories": ["general"]}, prefs)
    token = mint_token(doc, prefs)
    original = prefs.cfg
    try:
        prefs.cfg = msgspec.structs.replace(original, lock=set(original.lock) | {"theme"})
        verify_token(token, prefs)
    except PreferencesError as exc:
        assert exc.status_code == 409
        assert exc.code == "preferences_stale"
    else:
        raise AssertionError("a token minted before a lock change was accepted")
    finally:
        prefs.cfg = original
