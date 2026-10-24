"""Tolerant parser for the many log dialects found in the evidence.

Dialects handled (all produce the same normalized field dict):
  * key=value                      2026-.. level=INFO service=x event=y k=v
  * pipe separated                 ts | LEVEL | service | trace | event | k="v"
  * syslog                         ts <15>1 node service trace - - k="v"
  * JSON lines                     ts {"service": "...", "event": "..."}
  * cloud runtime wrapper          ts requestId=.. function=.. message="k=\"v\" ..."

A lookup key (usually an email) lives on a *started* line while the resource id
lives on the *completed* line of the same trace, so lines are grouped by trace
and the key is propagated to the completed record.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from ..models import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, IDENTIFIABLE,
                      PRESENT, REDACTED, Obs, norm, split_resource)

KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
MSG_RE = re.compile(r'message="((?:[^"\\]|\\.)*)"')
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BRACKET_RE = re.compile(r"\[(\w+)\]\s+\[([\w\-]+)\]\s+\[([^\]]+)\]\s+(\w+):\s*")

KEY_FIELDS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "subject_ref",
              "correlation_key", "subject_reference", "matched_value", "customer_email",
              "customer_reference", "requester_email", "email", "owner_email",
              "lookup_value", "subject_selector")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v.replace('\\"', '"')


def parse_line(line: str) -> dict | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    ts, _, rest = line.partition(" ")
    if not re.match(r"\d{4}-\d{2}-\d{2}T", ts):
        return None
    d: dict[str, str] = {"ts": ts}
    if rest.startswith("{"):
        try:
            for k, v in json.loads(rest).items():
                d[k] = "" if v is None else str(v)
        except json.JSONDecodeError:
            return None
        d.setdefault("service", d.get("logger", ""))
        return d

    # cloud runtime wrapper: the real payload is an escaped string in message="..."
    m = MSG_RE.search(rest)
    if m:
        inner = m.group(1).replace('\\"', '"')
        rest = rest[:m.start()] + " " + inner

    bm = BRACKET_RE.match(rest)
    if bm:                                   # ts [LEVEL] [service] [trace] event: k="v"
        d["level"], d["service"], d["trace_id"], d["event"] = bm.groups()
        rest = rest[bm.end():]
    elif " | " in rest and not rest.startswith("level="):
        parts = rest.split(" | ", 5)
        if len(parts) >= 5:
            d["level"], d["service"], d["trace_id"], d["event"] = (p.strip() for p in parts[:4])
            rest = parts[4] if len(parts) == 5 else " | ".join(parts[4:])
    elif rest.startswith("<"):
        toks = rest.split(" ", 6)
        if len(toks) >= 7:
            d["service"], d["trace_id"] = toks[2], toks[3]
            rest = toks[6]

    for k, v in KV_RE.findall(rest):
        d.setdefault(k, _unquote(v))
    d.setdefault("service", d.get("function", ""))
    d.setdefault("trace_id", d.get("requestId", ""))
    return d


def _is_null(v: str | None) -> bool:
    return v is None or v.strip().lower() in ("", "null", "none", "[redacted]")


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("true", "1", "yes")


def _ids(*vals: str | None) -> frozenset[str]:
    out = set()
    for v in vals:
        if _is_null(v):
            continue
        n = norm(v)
        out.add(n)
        out.update(norm(e) for e in EMAIL_RE.findall(n))
    return frozenset(out)


def record_result_to_state(record_type: str, result: str) -> str | None:
    """Map record_lookup style rows (system/record_type/result) to a state."""
    r = (result or "").strip().upper()
    mapping = {
        "NOT_FOUND": ABSENT, "ABSENT": ABSENT, "DELETED": ABSENT,
        "FOUND": PRESENT, "PRESENT": PRESENT,
        "IDENTIFIABLE": IDENTIFIABLE, "REDACTED": REDACTED, "ANONYMIZED": ANONYMIZED,
        "ACTIVE": ACTIVE, "ENABLED": ACTIVE, "DISABLED": DISABLED, "BLOCKED": BLOCKED,
        "RESTORABLE": ACTIVE, "RETAINED": PRESENT,
    }
    return mapping.get(r)


def parse_log_file(path: Path, evidence_id: str) -> list[Obs]:
    lines = [d for d in (parse_line(l) for l in path.read_text(errors="ignore").splitlines()) if d]
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for d in lines:
        if d.get("trace_id"):
            by_trace[d["trace_id"]].append(d)

    def trace_key(d: dict) -> list[str]:
        """Identifier values visible anywhere in the same trace."""
        vals: list[str] = []
        for other in by_trace.get(d.get("trace_id", ""), [d]):
            for kf in KEY_FIELDS:
                if not _is_null(other.get(kf)):
                    vals.append(other[kf])
        return vals

    out: list[Obs] = []
    for d in lines:
        ev = d.get("event", "")
        rid = d.get("resource_id", "")
        ts = d["ts"]
        svc = d.get("service", "")

        def emit(system: str, resource: str, state: str, extra: list[str], category: str = "") -> None:
            if not resource or state is None:
                return
            sysid = split_resource(resource)
            out.append(Obs(evidence_id, ts, sysid[0] if sysid else system, resource, state,
                           _ids(*extra), kind=f"log:{ev}", detail=path.name, category=category))

        if ev == "record_lookup":                      # notification / search diagnostic logs
            st = record_result_to_state(d.get("record_type", ""), d.get("result", ""))
            emit(d.get("system", ""), rid, st, [d.get("subject_reference")],
                 category=d.get("record_type", ""))
        elif ev == "profile_lookup_completed":
            absent = d.get("rows") == "0" or d.get("http_status") == "404"
            emit("account_service", rid, ABSENT if absent else PRESENT, trace_key(d))
        elif ev == "index_query_completed":
            hit = d.get("result", "hit") not in ("miss",) and d.get("rows", "1") != "0"
            emit("search_service", rid, PRESENT if hit else ABSENT, trace_key(d))
        elif ev in ("analytics_profile_read_completed", "analytics_profile_read"):
            direct = [d.get(k) for k in ("customer_reference", "email", "phone", "email_hash")]
            exposed = any(not _is_null(v) for v in direct) or d.get("direct_identifiers", "0") not in ("0", "")
            emit("analytics_platform", rid, IDENTIFIABLE if exposed else ANONYMIZED, direct + trace_key(d))
        elif ev == "analytics_identity_scan":
            emit("analytics_platform", rid, IDENTIFIABLE, [d.get("matched_value")] + trace_key(d))
        elif ev == "message_pii_scan":
            emit("support_platform", rid, IDENTIFIABLE, [d.get("matched_value")] + trace_key(d))
        elif ev == "attachment_pii_scan":
            emit("support_platform", rid, PRESENT, [d.get("matched_value")] + trace_key(d),
                 category="SUPPORT_ATTACHMENT")
        elif ev == "message_read":
            red = "redacted" in d.get("message_text", "").lower()
            emit("support_platform", rid, REDACTED if red else IDENTIFIABLE, trace_key(d))
        elif ev == "support_ticket_read_completed":
            direct = [d.get(k) for k in ("requester_name", "requester_email", "requester_phone")]
            exposed = any(not _is_null(v) for v in direct)
            emit("support_platform", rid, IDENTIFIABLE if exposed else REDACTED,
                 direct + [d.get("customer_reference")] + trace_key(d))
        elif ev == "audit_record_read_completed":
            emit("security_service", rid, PRESENT, trace_key(d))
        elif ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
            emit("integration_service", rid, ACTIVE if _truthy(d.get("enabled")) else DISABLED, trace_key(d))
        elif ev == "contact_lookup_completed" and rid:
            hit = d.get("rows", "1") != "0"
            emit("marketing_platform", rid, PRESENT if hit else ABSENT, trace_key(d), category="MARKETING_PROFILE")
        elif ev == "restore_policy_read_completed":
            emit("backup_service", rid, ACTIVE if _truthy(d.get("restore_allowed")) else BLOCKED, trace_key(d))
        elif ev == "restore_authorization_completed":
            emit("backup_service", rid, BLOCKED if d.get("decision", "").lower() == "deny" else ACTIVE, trace_key(d))
        elif ev == "restore_candidate_evaluated":
            blocked = _truthy(d.get("deletion_block")) or not _truthy(d.get("eligible"))
            emit("backup_service", rid, BLOCKED if blocked else ACTIVE, trace_key(d))
        elif ev == "contact_query_completed" and svc == "support_replica":
            emit("support_platform", rid, PRESENT, trace_key(d))
        elif ev == "deletion_marker_check" and svc == "support_replica":
            # no deletion marker -> the replica still serves the record
            emit("support_platform", rid, ABSENT if _truthy(d.get("marker_found")) else PRESENT, trace_key(d))
        elif ev in ("export_download_check_completed", "export_metadata_read_completed"):
            avail = d.get("available", d.get("download_available", "true"))
            emit("export_service", rid, PRESENT if _truthy(avail) else ABSENT, trace_key(d))
        elif ev == "retained_order_loaded":
            emit("legal_service", rid, PRESENT, trace_key(d))
    return out


def event_vocabulary(path: Path) -> dict[str, int]:
    """Debug helper: count event names so unhandled events are easy to spot."""
    counts: dict[str, int] = defaultdict(int)
    for l in path.read_text(errors="ignore").splitlines():
        d = parse_line(l)
        if d and d.get("event"):
            counts[d["event"]] += 1
    return dict(counts)
