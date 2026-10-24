"""Shared mapping from raw record dicts (any evidence format) to normalized Obs.

Every parser (SQL, JSON, text, logs, OCR) funnels through the functions here so
state semantics are defined exactly once:

  DELETE categories   : record present -> PRESENT, missing -> ABSENT
  REDACT / ANONYMIZE  : direct identifiers present -> IDENTIFIABLE, else REDACTED/ANONYMIZED
  DATA_FLOW           : enabled -> ACTIVE, disabled -> DISABLED
  BACKUP_ARCHIVE      : restore allowed -> ACTIVE, blocked -> BLOCKED
"""
from __future__ import annotations

import re

from ..models import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, IDENTIFIABLE,
                      PRESENT, REDACTED, Obs, norm, split_resource)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def is_null(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none"))


def ids_of(*vals) -> frozenset[str]:
    """Normalized identifier set; emails found inside free text are included."""
    out: set[str] = set()
    for v in vals:
        if is_null(v):
            continue
        s = norm(str(v))
        out.add(s)
        out.update(norm(e) for e in EMAIL_RE.findall(s))
    return frozenset(out)


def any_present(*vals) -> bool:
    return any(not is_null(v) for v in vals)


def _sys(resource_id: str, fallback: str) -> str:
    r = split_resource(resource_id)
    return r[0] if r else fallback


def make(evidence_id, ts, system, resource_id, state, ids=frozenset(), kind="", detail="", category=""):
    return Obs(evidence_id, ts, _sys(resource_id, system), resource_id, state, ids, kind, detail, category)


# ---------------------------------------------------------------------------
# generic "operational history" rows: system / record_type / result / lookup key
# ---------------------------------------------------------------------------
_RESULT = {
    "NOT_FOUND": ABSENT, "ABSENT": ABSENT, "FOUND": PRESENT, "PRESENT": PRESENT,
    "IDENTIFIABLE": IDENTIFIABLE, "REDACTED": REDACTED, "ANONYMIZED": ANONYMIZED,
    "ACTIVE": ACTIVE, "ENABLED": ACTIVE, "DISABLED": DISABLED, "BLOCKED": BLOCKED,
}


def history_row(evidence_id, ts, rec: dict, detail="", kind="history") -> list[Obs]:
    state = _RESULT.get(str(rec.get("result", "")).strip().upper())
    rid = rec.get("resource_id")
    if not rid or state is None:
        return []
    key = rec.get("lookup_value") or rec.get("subject_reference")
    return [make(evidence_id, rec.get("recorded_at") or ts, rec.get("system", ""), rid, state,
                 ids_of(key), kind, detail, category=rec.get("record_type", ""))]


# ---------------------------------------------------------------------------
# collection records (SQL generic tables and JSON snapshot bundles)
# ---------------------------------------------------------------------------
def collection_record(evidence_id, ts, origin: str, collection: str, rec: dict, detail="") -> list[Obs]:
    rid = rec.get("resource_id", "")
    k = f"{origin}:{collection}"
    mk = lambda state, ids, cat="": [make(evidence_id, ts, origin, rid, state, ids, k, detail, cat)]

    if collection == "operational_record_history":
        return history_row(evidence_id, ts, rec, detail, kind=k)
    if not rid:
        return []

    if collection == "customer_profiles":
        return mk(PRESENT, ids_of(rec.get("customer_reference"), rec.get("email"), rec.get("phone"),
                                  rec.get("email_hash")))
    if collection == "customer_analytics_reports":
        direct = (rec.get("user_id"), rec.get("email"), rec.get("phone"), rec.get("email_hash"))
        return mk(IDENTIFIABLE if any_present(*direct) else ANONYMIZED, ids_of(*direct))
    if collection == "customer_search_documents":
        doc = rec.get("document") or {}
        return mk(PRESENT, ids_of(doc.get("email"), doc.get("customer_reference"), doc.get("phone"),
                                  doc.get("email_hash")))
    if collection == "security_audit_events":
        return mk(PRESENT, ids_of(rec.get("subject_reference")))
    if collection == "support_conversations":
        text = rec.get("message_text") or ""
        has_pii = bool(EMAIL_RE.findall(text))
        return mk(IDENTIFIABLE if has_pii else REDACTED, ids_of(rec.get("subject_reference"), text))
    if collection == "ticket_attachments":
        return mk(PRESENT, ids_of(rec.get("uploaded_by")), "SUPPORT_ATTACHMENT")
    if collection == "customer_export_jobs":
        return mk(PRESENT, ids_of(rec.get("owner_email")))
    if collection == "connector_configurations":
        return mk(ACTIVE if rec.get("enabled") else DISABLED, ids_of(rec.get("subject_selector")))
    if collection == "backup_catalog":
        return mk(ACTIVE if rec.get("restore_allowed") else BLOCKED, ids_of(rec.get("subject_reference")))
    if collection == "deleted_customer_references":
        direct = (rec.get("name"), rec.get("email"), rec.get("phone"), rec.get("email_hash"))
        return mk(IDENTIFIABLE if any_present(*direct) else REDACTED,
                  ids_of(rec.get("subject_reference"), *direct))
    if collection == "retained_order_records":
        return mk(PRESENT, ids_of(rec.get("subject_reference")))
    return []


# ---------------------------------------------------------------------------
# service_resource_export.json sections
# ---------------------------------------------------------------------------
def service_export_record(evidence_id, file_ts, svc: str, section: str, rec: dict, detail="") -> list[Obs]:
    ts = rec.get("observed_at") or file_ts
    rid = rec.get("resource_id", "")
    kind = f"export:{svc}.{section}"
    mk = lambda state, ids, cat="": [make(evidence_id, ts, svc, rid, state, ids, kind, detail, cat)]

    if section == "profile_lookups":
        prof = rec.get("profile")
        if prof is None or rec.get("status_code") == 404:
            return mk(ABSENT, frozenset())
        return mk(PRESENT, ids_of(prof.get("customer_reference"), prof.get("email"), prof.get("phone"),
                                  prof.get("email_hash")))
    if section == "profiles":                         # analytics
        direct = (rec.get("customer_reference"), rec.get("email"), rec.get("phone"), rec.get("email_hash"))
        return mk(IDENTIFIABLE if any_present(*direct) else ANONYMIZED, ids_of(*direct))
    if section == "restore_entries":
        return mk(ACTIVE if rec.get("restore_allowed") else BLOCKED,
                  ids_of(rec.get("customer_email"), rec.get("customer_reference")))
    if section == "exports":
        return mk(PRESENT, ids_of(rec.get("owner_email"), rec.get("owner_phone"), rec.get("customer_reference")))
    if section == "connectors":
        return mk(ACTIVE if rec.get("enabled") else DISABLED,
                  ids_of(rec.get("customer_email"), rec.get("customer_reference")))
    if section == "index_queries":
        hits = (rec.get("hits") or {}).get("documents", [])
        out: list[Obs] = []
        for doc in hits:
            out.append(make(evidence_id, ts, svc, doc.get("resource_id", rid), PRESENT,
                            ids_of(doc.get("email"), doc.get("customer_reference"), doc.get("phone"),
                                   doc.get("email_hash"), rec.get("query")), kind, detail))
        if not hits and rid:
            out.append(make(evidence_id, ts, svc, rid, ABSENT, ids_of(rec.get("query")), kind, detail))
        return out
    if section == "audit_records":
        return mk(PRESENT, ids_of(rec.get("subject_reference")))
    if section == "tickets":
        direct = (rec.get("requester_name"), rec.get("requester_email"), rec.get("requester_phone"))
        return mk(IDENTIFIABLE if any_present(*direct) else REDACTED, ids_of(*direct, rec.get("customer_reference")))
    return []


# ---------------------------------------------------------------------------
# inventory snapshot tables (SQL)
# ---------------------------------------------------------------------------
def inventory_row(evidence_id, table: str, row: dict, detail="") -> list[Obs]:
    rid = row.get("resource_id", "")
    ts = row.get("updated_at", "")
    kind = f"inventory:{table}"
    mk = lambda system, state, ids, cat="": [make(evidence_id, ts, system, rid, state, ids, kind, detail, cat)]

    if table == "customer_profiles":
        return mk("account_service", PRESENT, ids_of(row.get("customer_reference"), row.get("email"),
                                                      row.get("phone"), row.get("email_hash")))
    if table == "customer_search_documents":
        return mk("search_service", PRESENT, ids_of(row.get("customer_reference"), row.get("email"),
                                                    row.get("phone"), row.get("email_hash")))
    if table == "customer_analytics_profiles":
        direct = (row.get("customer_reference"), row.get("email"), row.get("phone"), row.get("email_hash"))
        return mk("analytics_platform", IDENTIFIABLE if any_present(*direct) else ANONYMIZED, ids_of(*direct))
    if table == "support_records":
        direct = (row.get("requester_name"), row.get("requester_email"), row.get("requester_phone"))
        return mk("support_platform", IDENTIFIABLE if any_present(*direct) else REDACTED,
                  ids_of(*direct, row.get("customer_reference")))
    if table == "customer_exports":
        return mk("export_service", PRESENT, ids_of(row.get("owner_email"), row.get("owner_phone"),
                                                    row.get("customer_reference")))
    if table == "connector_subscriptions":
        return mk("integration_service", ACTIVE if row.get("enabled") else DISABLED,
                  ids_of(row.get("customer_email"), row.get("customer_reference")))
    if table == "backup_restore_entries":
        return mk("backup_service", ACTIVE if row.get("restore_allowed") else BLOCKED,
                  ids_of(row.get("customer_email"), row.get("customer_reference")))
    if table == "security_audit_records":
        return mk("security_service", PRESENT, ids_of(row.get("subject_reference")))
    return []


# ---------------------------------------------------------------------------
# customer_profiles_* / search_documents_primary (single-purpose SQL snapshots)
# ---------------------------------------------------------------------------
def profile_row(evidence_id, row: dict, detail="") -> list[Obs]:
    return [make(evidence_id, row.get("updated_at", ""), "account_service", row["resource_id"], PRESENT,
                 ids_of(row.get("user_id"), row.get("email"), row.get("phone")), "sql:profiles", detail)]


def search_doc_row(evidence_id, row: dict, detail="") -> list[Obs]:
    return [make(evidence_id, row.get("indexed_at", ""), "search_service", row["resource_id"], PRESENT,
                 ids_of(row.get("email")), "sql:search_documents", detail)]
