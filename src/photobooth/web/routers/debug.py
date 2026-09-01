"""Debug endpoints — the tool that answers "where did the 10 seconds go"
(telemetry/spans.py's docstring, IMPLEMENTATION_PLAN.md §4.2). Reads the
spans SQLite table the capture flow already writes to; records nothing
itself.
"""

from __future__ import annotations

import sqlite3
import statistics
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

router = APIRouter(prefix="/debug")


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/traces")
def get_traces(db: DbDep, limit: int = 20) -> list[dict[str, Any]]:
    capture_ids = [
        row[0]
        for row in db.execute(
            "SELECT DISTINCT capture_id FROM spans ORDER BY rowid DESC LIMIT ?", (limit,)
        )
    ]
    traces = []
    for capture_id in capture_ids:
        rows = db.execute(
            "SELECT name, t_start, t_end, meta_json FROM spans "
            "WHERE capture_id = ? ORDER BY t_start",
            (capture_id,),
        ).fetchall()
        traces.append(
            {
                "capture_id": capture_id,
                "spans": [
                    {
                        "name": name,
                        "t_start": t_start,
                        "t_end": t_end,
                        "duration_ms": None if t_end is None else (t_end - t_start) * 1000,
                        "meta": meta_json,
                    }
                    for name, t_start, t_end, meta_json in rows
                ],
            }
        )
    return traces


@router.get("/timings")
def get_timings(db: DbDep, limit_per_name: int = 200) -> dict[str, dict[str, float | int]]:
    names = [row[0] for row in db.execute("SELECT DISTINCT name FROM spans")]
    result: dict[str, dict[str, float | int]] = {}
    for name in names:
        durations_ms = [
            (t_end - t_start) * 1000
            for (t_start, t_end) in db.execute(
                "SELECT t_start, t_end FROM spans WHERE name = ? AND t_end IS NOT NULL "
                "ORDER BY rowid DESC LIMIT ?",
                (name, limit_per_name),
            )
        ]
        if not durations_ms:
            continue
        sorted_ms = sorted(durations_ms)
        result[name] = {
            "count": len(sorted_ms),
            "p50": _percentile(sorted_ms, 0.50),
            "p95": _percentile(sorted_ms, 0.95),
            "p99": _percentile(sorted_ms, 0.99),
            "max": sorted_ms[-1],
        }
    return result


def _percentile(sorted_values: list[float], p: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    return statistics.quantiles(sorted_values, n=100, method="inclusive")[int(p * 100) - 1]
