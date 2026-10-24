"""Plain-text handover records:  - ts | system | resource_id | RECORD_TYPE | RESULT | subject"""
from __future__ import annotations

from pathlib import Path

from ..models import Coverage, Obs
from . import records as R


def parse_text(path: Path, evidence_id: str, manifest_ts: str) -> tuple[list[Obs], list[Coverage]]:
    obs: list[Obs] = []
    systems: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip().lstrip("-").strip()
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 6 or not parts[0][:4].isdigit():
            continue
        ts, system, rid, rtype, result, subject = parts
        subject = "" if subject.lower().startswith("no direct") else subject
        obs.extend(R.history_row(evidence_id, ts,
                                 {"recorded_at": ts, "system": system, "resource_id": rid,
                                  "record_type": rtype, "result": result, "lookup_value": subject},
                                 detail=path.name, kind="handover"))
        systems[system] = ts
    cov = [Coverage(evidence_id, ts, s, "handover") for s, ts in systems.items()]
    return obs, cov
