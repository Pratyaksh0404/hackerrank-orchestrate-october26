"""JSON evidence (snapshot bundles, service exports, ledgers) -> Obs + Coverage."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import Coverage, Obs, split_resource
from . import records as R


def parse_json(path: Path, evidence_id: str, manifest_ts: str) -> tuple[list[Obs], list[Coverage]]:
    d = json.loads(path.read_text(errors="ignore"))
    obs: list[Obs] = []
    cov: list[Coverage] = []

    if "snapshots" in d:                                   # customer_data_snapshot_bundle
        for snap in d["snapshots"]:
            ts = snap.get("captured_at") or d.get("exported_at") or manifest_ts
            for rec in snap.get("records", []):
                obs.extend(R.collection_record(evidence_id, ts, snap["data_store"], snap["collection"], rec,
                                               detail=snap["collection"]))
            cov.append(Coverage(evidence_id, ts, snap["data_store"], snap["collection"]))

    elif "service_exports" in d:                           # service_resource_export
        file_ts = d.get("exported_at") or manifest_ts
        for svc, sections in d["service_exports"].items():
            for section, recs in sections.items():
                for rec in recs:
                    obs.extend(R.service_export_record(evidence_id, file_ts, svc, section, rec,
                                                       detail=f"{svc}.{section}"))
            cov.append(Coverage(evidence_id, file_ts, svc, "service_export"))

    elif "records" in d and d["records"] and "record_type" in d["records"][0]:   # ledger-style exports
        file_ts = d.get("exported_at") or manifest_ts
        systems: set[str] = set()
        for rec in d["records"]:
            obs.extend(R.history_row(evidence_id, file_ts,
                                     {**rec, "lookup_value": rec.get("subject_reference")},
                                     detail=d.get("origin", ""), kind="ledger"))
            systems.add(rec.get("system", ""))
        cov.extend(Coverage(evidence_id, file_ts, s, "ledger") for s in systems if s)

    elif "collection" in d:                                # analytics report / export jobs
        ts = d.get("captured_at") or manifest_ts
        systems = set()
        for rec in d.get("records", []):
            es = R.collection_record(evidence_id, ts, "", d["collection"], rec, detail=d["collection"])
            obs.extend(es)
            systems.update(o.system_id for o in es)
        cov.extend(Coverage(evidence_id, ts, s, d["collection"]) for s in systems if s)
    return obs, cov
