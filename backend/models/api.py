# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models defining the /api/v1 JSON contract.

These are the *single source of truth* for the API shape. Both the
FastAPI backend (serializes into these) and the frontend (deserializes
from these) depend on them.
"""

from __future__ import annotations

from functools import total_ordering
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from searx.enginelib import Engine  # noqa: I001
    from types import ModuleType
    from searx.plugins import PluginInfo


class EngineCapabilities(BaseModel):
    """Upstream feature flags a client needs before sending a query to an engine."""

    safesearch: bool
    time_range_support: bool
    paging: bool


@total_ordering
class EngineMeta(BaseModel):
    """An engine as shown in the instance's canonical engine catalogue."""

    name: str
    url: str
    description: str
    shortcut: str = ""
    categories: list[str] = Field(default_factory=list)
    default_enabled: bool = True
    capabilities: EngineCapabilities

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, EngineMeta):
            return NotImplemented
        return (self.name, self.categories, self.shortcut) < (other.name, other.categories, other.shortcut)

    @classmethod
    def from_engine(cls, engine: Engine | ModuleType) -> Self:
        return cls(
            name=engine.name,
            url=engine.about.website or "",
            description=engine.about.description or "",
            shortcut=engine.shortcut or "",
            categories=list(engine.categories or ["general"]),
            default_enabled=not engine.disabled,
            capabilities=EngineCapabilities(
                safesearch=bool(engine.safesearch),
                time_range_support=bool(engine.time_range_support),
                paging=bool(engine.paging),
            ),
        )


class SearchQuery(BaseModel):
    """A search request: the query text and the parameters that shape it."""

    query: str
    pageno: int = 1
    language: str = "all"
    safesearch: int = 0
    time_range: str | None = None
    timeout_limit: float | None = None
    categories: list[str] = Field(default_factory=list)


class ResultItem(BaseModel):
    """A single search result.

    ``author``, ``pubdate``, ``metadata`` and ``length`` are carried in the
    standard response because API consumers (AI agents in particular) need
    them for ranking and citation. They are populated by the engine layer
    itself -- see ``searx.result_types.Result`` -- so no extra engine
    round-trips are needed.
    """

    url: str
    title: str
    block: str = ""
    img_thumbnail: str | None = None
    width: int | None = None
    height: int | None = None
    engine: str = ""
    category: str = ""
    score: float = 1.0
    urls: list[str] = Field(default_factory=list)
    children: list[dict[str, Any]] = Field(default_factory=list)
    author: str = ""
    pubdate: str = ""
    metadata: str = ""
    length: str = ""


class AnswerItem(BaseModel):
    """A direct answer produced by an engine or answerer (e.g. a calculator or infobox)."""

    title: str
    url: str = ""
    content: str = ""
    engine: str = ""
    is_infopage: bool = False


class SearchResponse(BaseModel):
    """The current JSON contract of the search endpoint.

    Carries the aggregated results together with the query echo, the
    suggestions/corrections/answers, and the per-engine diagnostics
    (captcha, deactivated, incompatible, unresponsive) so clients can
    surface partial failures.
    """

    query: str
    pageno: int
    languages: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    start_info: dict[str, Any] = Field(default_factory=dict)
    results: list[ResultItem] = Field(default_factory=list)
    results_per_category: dict[str, int] = Field(default_factory=dict)
    engines_captcha: list[str] = Field(default_factory=list)
    engines_deactivated: list[str] = Field(default_factory=list)
    engines_incompatible: list[str] = Field(default_factory=list)
    answers: list[AnswerItem] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    infoboxes: list[dict[str, Any]] = Field(default_factory=list)
    unresponsive_engines: list[list[str]] = Field(default_factory=list)


class CompatSearchResponse(BaseModel):
    """The legacy ``?format=json`` contract of the Flask webapp.

    Kept byte-compatible with ``searx.webutils.get_json_response()`` so that
    existing API consumers (scripts, AI agents, integrations) keep working
    while they migrate to the richer :class:`SearchResponse` contract.

    Top-level keys: query, results, answers, corrections, infoboxes,
    suggestions, unresponsive_engines.
    """

    query: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    answers: list[dict[str, Any]] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    infoboxes: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    unresponsive_engines: list[list[str]] = Field(default_factory=list)


@total_ordering
class PluginMeta(BaseModel):
    """An installed plugin, as offered to clients in the preference catalogue."""

    id: str
    name: str = ""
    description: str = ""
    preference_section: str | None = "general"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, PluginMeta):
            return NotImplemented
        # `preference_section` is nullable, so it cannot be compared with a
        # string directly. A plugin without a section sorts first.
        return (self.id, self.name, self.preference_section or "") < (
            other.id,
            other.name,
            other.preference_section or "",
        )

    @classmethod
    def from_plugininfo(cls, info: PluginInfo) -> Self:
        return cls(
            id=info.id,
            name=info.name,
            description=info.description,
            preference_section=info.preference_section,
        )


class PreferenceChoice(BaseModel):
    """One legal value of a preference setting.

    ``value`` is what a document submits. ``rtl`` is present only for
    locale choices, where it tells the client the text direction; every
    other choice omits the key rather than emitting ``null``.
    """

    value: Any
    title: str
    rtl: bool | None = Field(default=None, exclude_if=lambda value: value is None)


class PreferenceSetting(BaseModel):
    """A writable preference setting: its type, instance default and effective value.

    ``choices`` is present only when the setting has a finite set of legal
    values; it is omitted (not ``null`` or ``[]``) for free-form settings.
    """

    type: str
    default: Any
    current: Any
    choices: list[PreferenceChoice] | None = Field(default=None, exclude_if=lambda value: value is None)
    locked: bool = False


class PluginState(BaseModel):
    """A plugin's metadata together with its instance default and effective state.

    The plugin id is the key of the ``plugin_states`` map, so it is not
    repeated inside the value.
    """

    name: str = ""
    description: str = ""
    preference_section: str | None = "general"
    default_enabled: bool
    enabled: bool


class PreferencesResponse(BaseModel):
    """The writable preference contract: settings, effective states and client state.

    ``settings`` holds one descriptor per writable preference key, with
    its JSON type, instance default, effective value, legal choices and
    lock state. ``engine_states`` reports the effective boolean state of
    every engine/category pair a document may name, and ``plugin_states``
    the metadata, instance default and effective state of every installed
    plugin. The instance-wide engine catalogue lives in ``/api/v1/engines``
    and is not repeated here.

    The response also carries the *client* state: the preference document
    this client presented (``client``), the keys of that document this
    instance could not apply (``skipped``), the keys the operator locked
    (``locked``) and the token that carried the document (``token``).
    """

    settings: dict[str, PreferenceSetting] = Field(default_factory=dict)
    engine_states: dict[str, bool] = Field(default_factory=dict)
    plugin_states: dict[str, PluginState] = Field(default_factory=dict)
    client: dict[str, Any] = Field(default_factory=dict)
    skipped: list[str] = Field(default_factory=list)
    locked: list[str] = Field(default_factory=list)
    token: str = ""


class AutocompleteResponse(BaseModel):
    """Completion suggestions for a partially typed query."""

    suggestions: list[str] = Field(default_factory=list)


class EngineStat(BaseModel):
    """Runtime metrics for a single engine: result/error counts, score per result and response time."""

    name: str
    url: str
    category: str
    title: str
    result_count: int = 0
    error_count: int = 0
    score_per_result: float = 0.0
    time: float = 0.0
    cached_results: int = 0
    suspended: bool = False


class StatsResponse(BaseModel):
    """Aggregated runtime metrics: per-engine stats plus the instance-wide totals."""

    engines: list[EngineStat] = Field(default_factory=list)
    total: dict[str, Any] = Field(default_factory=dict)


class FaviconResponse(BaseModel):
    """A favicon served by the favicon proxy: the image URL, format and size in pixels."""

    url: str
    format: str = "png"
    size: int = 64


class ErrorResponse(BaseModel):
    """The error body returned by an endpoint that rejects the request.

    Mirrors what FastAPI's ``HTTPException`` renders: a ``detail`` key
    holding the human-readable reason.
    """

    detail: str


class MetaResponse(BaseModel):
    """Instance-wide metadata served at boot: brand, version and output formats.

    The engine catalogue lives in ``/api/v1/engines`` and the preference
    catalogue in ``/api/v1/preferences``; neither is duplicated here.
    """

    brand: dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    formats: list[str] = Field(default_factory=list)
