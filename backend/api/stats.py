# SPDX-License-Identifier: AGPL-3.0-or-later
"""/api/v1/stats — engine statistics, backed by the kernel's metrics module."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.models.api import EngineStat, StatsResponse
from searx.engines import engines
from searx.metrics import get_engine_errors, get_engines_stats, get_reliabilities

router = APIRouter(tags=["stats"])


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get engine statistics",
    description=(
        "Return the runtime metrics of the search engines: per engine the "
        "number of results returned, the number of errors, the score per "
        "result and the total response time, plus the aggregated totals.\n\n"
        "The numbers come from the instance's own metrics and reset when the "
        "instance restarts."
    ),
    operation_id="get_stats",
)
def stats() -> StatsResponse:
    filtered_engines = list(engines.keys())
    engine_stats: dict[str, Any] = get_engines_stats(filtered_engines)
    engine_reliabilities: dict[str, Any] = get_reliabilities(filtered_engines)

    stats_engines = []
    for stat in engine_stats["time"]:
        name = stat["name"]
        eng = engines.get(name)
        stats_engines.append(
            EngineStat(
                name=name,
                url=eng.about.website if eng else "",
                category=(eng.categories or ["general"])[0] if eng else "general",
                title=name,
                result_count=int(stat.get("result_count") or 0),
                error_count=len(engine_reliabilities.get(name, {}).get("errors") or []),
                score_per_result=stat.get("score_per_result") or 0.0,
                time=stat.get("total") or 0.0,
                cached_results=0,
                suspended=False,
            )
        )

    return StatsResponse(
        engines=stats_engines,
        total={
            "engines": len(stats_engines),
            "max_time": engine_stats.get("max_time", 0),
            "max_result_count": engine_stats.get("max_result_count", 0),
        },
    )


@router.get(
    "/stats/errors",
    summary="Get engine errors",
    description=(
        "Return the errors recorded for each engine, keyed by engine name.\n\n"
        "Useful to diagnose why an engine returns no results or fails on a "
        "specific query."
    ),
    operation_id="get_stats_errors",
)
def stats_errors() -> dict[str, Any]:
    return get_engine_errors(list(engines.keys()))
