# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models defining the /api/v1 JSON contract.

These are the *single source of truth* for the API shape. Both the
FastAPI backend (serializes into these) and the frontend (deserializes
from these) depend on them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from searx.enginelib import Engine  # noqa: I001
    from types import ModuleType


class EngineMeta(BaseModel):
    name: str
    url: str
    description: str
    category: str
    shortcut: str = ""

    @classmethod
    def from_engine(cls, engine: Engine | ModuleType) -> Self:
        return cls(
            name=engine.name,
            url=engine.about.website or "",
            description=engine.about.description or "",
            category=(engine.categories or ["general"])[0],
            shortcut=engine.shortcut or "",
        )


class CategoryMeta(BaseModel):
    name: str
    title: str
    engines: list[str] = Field(default_factory=list)


class LocaleMeta(BaseModel):
    tag: str
    title: str
    rtl: bool = False


class SearchQuery(BaseModel):
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
    title: str
    url: str = ""
    content: str = ""
    engine: str = ""
    is_infopage: bool = False


class SearchResponse(BaseModel):
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


class PreferencesResponse(BaseModel):
    categories: list[CategoryMeta] = Field(default_factory=list)
    engines: list[EngineMeta] = Field(default_factory=list)
    locales: list[LocaleMeta] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    default: dict[str, Any] = Field(default_factory=dict)
    current: dict[str, Any] = Field(default_factory=dict)
    locked: list[str] = Field(default_factory=list)


class AutocompleteResponse(BaseModel):
    suggestions: list[str] = Field(default_factory=list)


class EngineStat(BaseModel):
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
    engines: list[EngineStat] = Field(default_factory=list)
    total: dict[str, Any] = Field(default_factory=dict)


class FaviconResponse(BaseModel):
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
    """Static metadata served to the SPA at boot (engines, categories, locales)."""

    engines: list[EngineMeta] = Field(default_factory=list)
    categories: list[dict[str, Any]] = Field(default_factory=list)
    locales: list[dict[str, Any]] = Field(default_factory=list)
    brand: dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    default_theme: str = "simple"
    formats: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
