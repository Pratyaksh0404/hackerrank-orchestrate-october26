"""SQLite-compatible SQL snapshot exports -> Obs + Coverage."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

from ..models import Coverage, Obs
from . import records as R

INVENTORY_TABLES = {
    "customer_profiles", "customer_search_documents", "customer_analytics_profiles",
    "support_records", "customer_exports", "connector_subscriptions",
    "backup_restore_entries", "security_audit_records",
}


def _captured_at(text: str, fallback: str) -> str:
    m = re.search(r"Captured at:\s*(\S+)", text)
    return m.group(1) if m else fallback


def _rows(db: sqlite3.Connection, table: str):
    cur = db.execute(f"SELECT * FROM {table}")
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        yield dict(zip(cols, row))


def parse_sql(path: Path, evidence_id: str, manifest_ts: str) -> tuple[list[Obs], list[Coverage]]:
    text = path.read_text(errors="ignore")
    captured = _captured_at(text, manifest_ts)
    db = sqlite3.connect(":memory:")
    db.executescript(text)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    obs: list[Obs] = []
    cov: list[Coverage] = []
    for t in tables:
        is_primary = "primary" in t or t in INVENTORY_TABLES
        if t == "snapshot_scope":
            for r in _rows(db, t):
                cov.append(Coverage(evidence_id, r["captured_at"], r["origin_system"], "snapshot_scope"))
        elif t in INVENTORY_TABLES:
            for r in _rows(db, t):
                obs.extend(R.inventory_row(evidence_id, t, r, detail=t))
        elif t.startswith("customer_profiles_"):          # account profile primary / failover replica
            for r in _rows(db, t):
                obs.extend(R.profile_row(evidence_id, r, detail=t))
            cov.append(Coverage(evidence_id, captured, "account_service", t, is_primary))
        elif t == "search_documents_primary":
            for r in _rows(db, t):
                obs.extend(R.search_doc_row(evidence_id, r, detail=t))
            cov.append(Coverage(evidence_id, captured, "search_service", t, is_primary))
        else:                                              # generic record_json tables (primary + replicas)
            systems: set[str] = set()
            for r in _rows(db, t):
                try:
                    rec = json.loads(r["record_json"])
                except (KeyError, json.JSONDecodeError):
                    continue
                systems.add(r["origin_system"])
                obs.extend(R.collection_record(evidence_id, r.get("updated_at") or captured,
                                               r["origin_system"], r["collection_name"], rec, detail=t))
            for s in systems:
                cov.append(Coverage(evidence_id, captured, s, t, is_primary))
    db.close()
    # state is observed when the snapshot was captured, not when a row last changed
    scope_ts = {c.system_id: c.captured_at for c in cov if c.detail == "snapshot_scope"}
    obs = [replace(o, observed_at=scope_ts.get(o.system_id, captured)) if o.kind.startswith(("inventory", "sql:"))
           else replace(o, observed_at=captured) for o in obs]
    return obs, cov
