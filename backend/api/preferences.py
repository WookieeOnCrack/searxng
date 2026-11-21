# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/preferences — return the preference catalogue as JSON.

The catalogue reports what this instance can be asked to prefer: the
selectable categories, engines, plugins and locales, the engine pairs a
document may name, and the default and current value of every setting.
The GET route is client-aware -- it reads the client's preference token
from the request, applies the document it carries and reports that
document back.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, Response

from backend.models.api import (
    CategoryMeta,
    EngineMeta,
    LocaleMeta,
    PluginMeta,
    PreferencesResponse,
)
from backend.services.preferences import (
    PreferencesError,
    build_preferences,
    filter_document,
    mint_token,
    normalize_document,
    read_client_token,
    verify_token,
)
from searx.engines import categories, engines
from searx.locales import LOCALE_NAMES
from searx.plugins import STORAGE as plugins_storage
from searx.preferences import COOKIE_MAX_AGE, Preferences, ValidationException
from searx.settings_defaults import OUTPUT_FORMATS

router = APIRouter(tags=["preferences"])


def _snapshot(prefs: Preferences) -> dict[str, Any]:
    """Return the value of every key/value setting of the preferences."""
    return {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}


def _catalogue(prefs: Preferences) -> dict[str, Any]:
    """Return the instance catalogue: what a client may name in a document.

    ``engine_pairs`` is the *expressible* set of engine/category pairs --
    exactly the keys ``prefs.engines.choices`` holds -- not the raw
    per-category membership ``categories[]`` implies. The two differ: an
    engine registered in a category that is not a tab is not expressible,
    so advertising it would name a pair the server then refuses.
    """
    return {
        "categories": sorted([CategoryMeta.from_category(c) for c in categories]),
        "engines": sorted([EngineMeta.from_engine(e) for e in engines.values()]),
        "plugins": _plugin_catalogue(),
        "engine_pairs": sorted(prefs.engines.choices),
        "locales": [LocaleMeta.from_tag(tag) for tag in LOCALE_NAMES],
        "formats": OUTPUT_FORMATS,
        "styles": ["auto", "light", "dark", "black"],
    }


def _plugin_catalogue() -> list[PluginMeta]:
    """Return the installed plugins, one entry per plugin id.

    ``PluginStorage.info`` yields one entry per registered plugin
    *object*, and the same plugin id can be registered more than once --
    the storage's collision check compares a plugin object against id
    strings and therefore never fires, and the test harness re-initializes
    the storage on every test setup. The catalogue must publish each id
    once, because ``prefs.plugins.choices`` (the set a document may name)
    is keyed by id and holds each of them once.

    When an id *is* registered more than once, the last registration wins
    -- the same rule ``PluginsSetting`` applies when it builds its
    ``choices`` dict from the same storage. The catalogue therefore
    describes the same registration whose state the preference is
    computed from.
    """
    catalogue: dict[str, PluginMeta] = {info.id: PluginMeta.from_plugininfo(info) for info in plugins_storage.info}
    return [catalogue[plugin_id] for plugin_id in sorted(catalogue)]


@router.get(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Get the preference catalogue",
    description=(
        "Return the preference catalogue of this instance: the selectable "
        "categories, engines, plugins and locales, the engine/category "
        "pairs a document may name, the available output formats and "
        "styles, the default and current values of every preference, and "
        "the list of preferences the operator has locked.\n\n"
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
    default = _snapshot(prefs)

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

    current = _snapshot(prefs)

    return PreferencesResponse(
        **_catalogue(prefs),
        default=default,
        current=current,
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
        "client, together with a signed token that carries the document.\n\n"
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
    default = _snapshot(prefs)

    try:
        doc, skipped = filter_document(body, prefs)
    except PreferencesError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc

    try:
        prefs.parse_dict(normalize_document(doc, prefs))
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=f"preferences_invalid: {exc}") from exc

    current = _snapshot(prefs)

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
        **_catalogue(prefs),
        default=default,
        current=current,
        client=doc,
        skipped=skipped,
        locked=sorted(prefs.cfg.lock),
        token=token,
    )
