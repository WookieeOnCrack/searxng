# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/preferences — the writable preference contract as JSON.

The endpoint owns everything a client needs to understand and change
preferences: one descriptor per setting (its JSON type, instance default,
effective value, legal choices and lock state), the effective state of
every engine/category pair a document may name, and the metadata, instance
default and effective state of every installed plugin.  The instance-wide
engine catalogue lives in ``/api/v1/engines`` and is not repeated here.

The GET route is client-aware -- it reads the client's signed preference
token from the request, applies the document it carries and reports that
document back.  The POST route applies a replacement document and mints a
new token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, Response

from backend.models.api import (
    PluginState,
    PreferenceChoice,
    PreferenceSetting,
    PreferencesResponse,
)
from backend.services.preferences import (
    DEFAULT_CATEGORY_KEY,
    PreferencesError,
    build_preferences,
    filter_document,
    mint_token,
    normalize_document,
    read_client_token,
    verify_token,
)
from searx.engines import categories as engine_categories
from searx.locales import LOCALE_NAMES, RTL_LOCALES
from searx.plugins import STORAGE as plugins_storage
from searx.preferences import (
    COOKIE_MAX_AGE,
    BooleanSetting,
    EnumStringSetting,
    MapSetting,
    MultipleChoiceSetting,
    Preferences,
    Setting,
    ValidationException,
)

router = APIRouter(tags=["preferences"])

#: The instance default of the fork-only ``default_category`` setting.
DEFAULT_CATEGORY = "general"

#: Display labels of the safesearch map setting, keyed by its stored value.
SAFESEARCH_TITLES: dict[Any, str] = {0: "Off", 1: "Moderate", 2: "Strict"}


def _title(value: Any) -> str:
    """Return the deterministic English display title of a choice value."""
    text = str(value)
    if text == "":
        return "Default"
    return text.replace("_", " ").title()


def _json_type(value: Any) -> str:
    """Return the JSON type name of a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "string"


def _setting_type(setting: Setting) -> str:
    """Return the JSON type of the value a setting holds."""
    if isinstance(setting, MultipleChoiceSetting):
        return "array"
    if isinstance(setting, BooleanSetting):
        return "boolean"
    if isinstance(setting, MapSetting):
        return _json_type(setting.get_value())
    return "string"


def _enum_choices(name: str, setting: EnumStringSetting) -> list[PreferenceChoice]:
    """Return the finite choices of an enum setting, in declared order.

    Locale choices carry the upstream display name and text direction;
    every other choice is labelled from its value.
    """
    if name == "locale":
        return [
            PreferenceChoice(
                value=tag,
                title=LOCALE_NAMES.get(tag) or _title(tag),
                rtl=tag in RTL_LOCALES,
            )
            for tag in setting.choices
        ]
    return [PreferenceChoice(value=value, title=_title(value)) for value in setting.choices]


def _setting_choices(name: str, setting: Setting) -> list[PreferenceChoice] | None:
    """Return the finite choices of a setting, or None when it has none.

    The list is returned (not stored as an empty list) so a free-form
    setting omits the ``choices`` key from the response entirely.
    """
    if isinstance(setting, MultipleChoiceSetting):
        return [PreferenceChoice(value=value, title=_title(value)) for value in setting.choices]
    if isinstance(setting, EnumStringSetting):
        return _enum_choices(name, setting)
    if isinstance(setting, MapSetting):
        titles = SAFESEARCH_TITLES if name == "safesearch" else {}
        return [PreferenceChoice(value=value, title=titles.get(value, _title(value))) for value in setting.map.values()]
    return None


def _category_choices() -> list[PreferenceChoice]:
    """Return the real engine categories as choices, without ``none``."""
    return [PreferenceChoice(value=value, title=_title(value)) for value in engine_categories]


def _setting_catalogue(
    default: dict[str, Any],
    current: dict[str, Any],
    prefs: Preferences,
    client_doc: dict[str, object],
) -> dict[str, PreferenceSetting]:
    """Build one descriptor per writable setting.

    ``default`` is the snapshot taken before the client document was
    applied and ``current`` the one taken afterwards.  ``default_category``
    is not an upstream ``Preferences`` setting: its effective value comes
    from the applicable client document, falling back to the instance
    default.
    """
    catalogue: dict[str, PreferenceSetting] = {}

    for name, setting in prefs.key_value_settings.items():
        catalogue[name] = PreferenceSetting(
            type=_setting_type(setting),
            default=default[name],
            current=current[name],
            choices=_setting_choices(name, setting),
            locked=setting.locked,
        )

    catalogue[DEFAULT_CATEGORY_KEY] = PreferenceSetting(
        type="string",
        default=DEFAULT_CATEGORY,
        current=client_doc.get(DEFAULT_CATEGORY_KEY, DEFAULT_CATEGORY),
        choices=_category_choices(),
        locked=False,
    )

    return catalogue


def _engine_states(prefs: Preferences) -> dict[str, bool]:
    """Return the effective state of every expressible engine/category pair.

    The keys are exactly ``prefs.engines.choices`` -- the pairs a document
    may legally name -- and the values are their effective booleans after
    the client document has been applied.
    """
    return {pair: bool(state) for pair, state in prefs.engines.choices.items()}


def _plugin_states(prefs: Preferences) -> dict[str, PluginState]:
    """Return metadata and effective state for every installed plugin.

    ``PluginStorage.info`` yields one entry per registered plugin object,
    and the same id can be registered more than once.  The state map is
    keyed by id, so the last registration wins -- the same rule
    ``PluginsSetting`` applies when it builds its ``choices`` dict from the
    same storage.
    """
    infos = {info.id: info for info in plugins_storage.info}
    states: dict[str, PluginState] = {}

    for plugin_id, enabled in prefs.plugins.choices.items():
        info = infos.get(plugin_id)
        states[plugin_id] = PluginState(
            name=info.name if info else "",
            description=info.description if info else "",
            preference_section=info.preference_section if info else None,
            default_enabled=prefs.plugins.default_choices[plugin_id],
            enabled=enabled,
        )

    return states


@router.get(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Get the preference contract",
    description=(
        "Return the writable preference contract of this instance. "
        "`settings` holds one descriptor per writable setting, with its "
        "JSON type, instance default, effective value, legal choices (when "
        "the setting has a finite set) and lock state. `engine_states` "
        "reports the effective boolean state of every engine/category pair "
        "a document may name; the full engine catalogue is served by "
        "`/api/v1/engines`. `plugin_states` reports the metadata, instance "
        "default and effective state of every installed plugin.\n\n"
        "The endpoint is client-aware: it reads the client's preference "
        "token from the `preferences` query parameter, the "
        "`X-SearXNG-Preferences` header or the `sxng_preferences` cookie "
        "(in that order of precedence), applies the document it carries and "
        "reports it in the `client` field together with the `token` that "
        "carried it. The keys of that document this instance could not "
        "apply are listed in `skipped`."
    ),
    operation_id="get_preferences",
    openapi_extra={
        "parameters": [
            {
                "name": "preferences",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": "The client's signed preference token.",
            },
            {
                "name": "X-SearXNG-Preferences",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "description": "The client's signed preference token.",
            },
        ]
    },
)
def get_preferences(request: Request) -> PreferencesResponse:
    prefs = build_preferences()

    # Snapshot the defaults *before* the client document is applied:
    # parse_dict() mutates the setting objects in place.
    default = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}

    doc: dict[str, object] = {}
    skipped: list[str] = []

    token = read_client_token(request)
    if token:
        try:
            doc, skipped = verify_token(token, prefs)
        except PreferencesError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc

    if doc:
        try:
            prefs.parse_dict(normalize_document(doc, prefs))
        except ValidationException as exc:
            raise HTTPException(status_code=400, detail=f"preferences_invalid: {exc}") from exc

    current = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}

    return PreferencesResponse(
        settings=_setting_catalogue(default, current, prefs, doc),
        engine_states=_engine_states(prefs),
        plugin_states=_plugin_states(prefs),
        client=doc,
        skipped=skipped,
        locked=sorted(prefs.cfg.lock),
        token=token or "",
    )


@router.post(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Apply a client preference document",
    description=(
        "Apply a preference document and return the effective state for this "
        "client, together with a signed token that carries the document. "
        "`settings` describes every writable setting and its effective value, "
        "`engine_states` the effective state of every engine/category pair "
        "and `plugin_states` the metadata, instance default and effective "
        "state of every installed plugin.\n\n"
        "The posted document *replaces* the client's document; nothing is "
        "merged from a previously presented token -- a token sent with this "
        "request is discarded. The token is returned in the response body and "
        "set as the `sxng_preferences` cookie.\n\n"
        "The document is applied leniently: a key this instance cannot apply "
        "(an unknown key, a value it rejects, an engine or plugin it does not "
        "have) is skipped rather than failing the request, so a template "
        "document can be reused across instances. The skipped keys are listed "
        "in the `skipped` field of the response.\n\n"
        "Two cases are hard errors rather than skips: a document carrying "
        "`tokens` is rejected with `400 preferences_tokens_not_allowed`, and a "
        "body that is not a JSON object is rejected with `400 "
        "preferences_invalid`.\n\n"
        "To reset to the instance defaults, POST an empty document `{}`; the "
        "response carries a signed token of the empty document. There is no "
        "`DELETE` route: an API client resets by simply stopping to send its "
        "token, and a browser client resets by clearing the cookie."
    ),
    operation_id="post_preferences",
)
def post_preferences(
    request: Request,
    response: Response,
    body: Any = Body(...),
) -> PreferencesResponse:
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="preferences_invalid: the preference document must be a JSON object",
        )

    prefs = build_preferences()

    # Snapshot the defaults *before* the client document is applied:
    # parse_dict() mutates the setting objects in place.
    default = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}

    try:
        doc, skipped = filter_document(body, prefs)
    except PreferencesError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc

    try:
        prefs.parse_dict(normalize_document(doc, prefs))
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=f"preferences_invalid: {exc}") from exc

    current = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}

    # The token carries the *applicable* document, so the epoch is computed
    # over exactly the pairs that will be interpreted against this instance.
    token = mint_token(doc, prefs)

    response.set_cookie(
        "sxng_preferences",
        token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )

    return PreferencesResponse(
        settings=_setting_catalogue(default, current, prefs, doc),
        engine_states=_engine_states(prefs),
        plugin_states=_plugin_states(prefs),
        client=doc,
        skipped=skipped,
        locked=sorted(prefs.cfg.lock),
        token=token,
    )
