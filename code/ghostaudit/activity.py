"""system_activity.csv: identity-link rows and the global account-event assignment.

The activity stream is global and noisy. Two things are extracted from it:

1. LINK_RESOURCE_IDENTITY rows - explicit catalog statements that tie a resource
   (marketing profile, attachment, tombstone, ...) to an email and a user_id.
2. The deletion worker's DELETE_RECORD events on account_service. A deleted
   account carries no identifier anywhere in the evidence, so its resource id can
   only come from the workflow's own event. Because the stream interleaves many
   people, events are matched to requests with hard constraints, then one-to-one
   propagation:
     * time      - the account deletion lands shortly before ``reported_at``
     * claim     - the event result must agree with ``reported_status``
     * ownership - events on accounts that evidence ties to someone else are excluded
     * exclusivity - an event can belong to only one request
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from .models import Case, norm

_P = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")

# reported_status (claim) -> event results that can have produced it
CLAIM_RESULTS = {
    "COMPLETE": {"SUCCESS"},
    "PARTIALLY_COMPLETE": {"PARTIAL", "SUCCESS"},
    "NOT_STARTED": {"FAILED"},
}
H = 3600.0
ACCOUNT_WINDOW = (-1.5 * H, 0.3 * H)    # seconds relative to reported_at

# Workflow events that leave a record in its *compliant* state carry no identifier anywhere in the
# evidence, so the only route to the resource is the worker's own event. Each group below is one
# kind of event, matched to requests independently (window = seconds relative to reported_at).
EVENT_GROUPS = {
    # account deletions land 0.7-1.3 h before the claim is reported; a tighter second window covers the
    # same-minute reporting seen in the oldest requests. The second window is used only if the first has no candidate.
    "account":   dict(system="account_service",   types={"DELETE_RECORD"},         window=ACCOUNT_WINDOW, claim=True,
                      windows=[(-1.3 * H, -0.7 * H), (-0.1 * H, 0.15 * H)]),
    "analytics": dict(system="analytics_platform", types={"ANONYMIZE_RECORD"},     window=(-0.6 * H, 0.05 * H), claim=False),
    "support":   dict(system="support_platform",   types={"REDACT_RECORD"},        window=(-0.6 * H, 0.05 * H), claim=False),
    "flow":      dict(system="integration_service", types={"DISABLE_DATA_FLOW"},   window=(-0.6 * H, 0.05 * H), claim=False),
    "backup":    dict(system="backup_service",     types={"BLOCK_RESTORE"},        window=(-0.6 * H, 0.05 * H), claim=False),
    "tombstone": dict(system="identity_service",   types={"REDACT_RECORD"},        window=(-0.6 * H, 0.05 * H), claim=False),
}

LINK_RE = re.compile(r"linked (?P<res>\S+) \((?P<alt>[^)]*)\) to (?P<email>\S+@\S+) and (?P<uid>\w+)\.")
ATTACH_RE = re.compile(r"confirmed (?P<res>\S+) \((?P<alt>[^)]*)\) belongs only to (?P<email>\S+@\S+) and (?P<uid>\w+);")


class ActivityIndex:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.by_resource: dict[str, list[dict]] = defaultdict(list)
        self.by_id: dict[str, dict] = {}
        for r in rows:
            self.by_resource[r["resource_id"]].append(r)
            self.by_id[r["activity_id"]] = r
        self.links: dict[str, dict] = {}          # resource_id -> {keys, activity_id, exclusive}
        for r in rows:
            if r["event_type"] != "LINK_RESOURCE_IDENTITY":
                continue
            m = ATTACH_RE.search(r["details"]) or LINK_RE.search(r["details"])
            if m:
                self.links[m["res"]] = {
                    "keys": {norm(m["email"].rstrip(".,;")), norm(m["uid"])},
                    "activity_id": r["activity_id"],
                    "exclusive": "belongs only" in r["details"],
                    "time": r["event_time"],
                }
        self.account_events = self.group_events("account")

    def group_events(self, group: str) -> list[dict]:
        g = EVENT_GROUPS[group]
        return [r for r in self.rows if r["source_system"] == "deletion_worker"
                and r["target_system"] == g["system"] and r["event_type"] in g["types"]
                and (g["claim"] or r["reported_result"] == "SUCCESS")]

    def events_for(self, resource_id: str) -> list[dict]:
        return self.by_resource.get(resource_id, [])

    def non_deletion_flag(self, resource_id: str) -> dict | None:
        """CLASSIFY_REQUEST rows: privacy intake classified the request as non-deletion."""
        for r in self.by_resource.get(resource_id, []):
            if r["event_type"] == "CLASSIFY_REQUEST" and "non-deletion" in r["details"]:
                return r
        return None


def assign_events(cases: list[Case], resolver, owner: dict[str, set[str]],
                  activity: ActivityIndex, group: str, occupied: dict[str, set[str]] | None = None) -> dict[str, dict]:
    """case_id -> {"resource_id", "activity_id", "confidence"} for one event group."""
    spec = EVENT_GROUPS[group]
    events = activity.group_events(group)
    cands: dict[str, dict[str, tuple[float, dict]]] = {}
    users: dict[str, str] = {}
    for c in cases:
        u = resolver.resolve(c.email)
        if not u:
            continue
        users[c.case_id] = u.user_id
        if occupied and spec["system"] in occupied.get(c.case_id, ()) and group != "account":
            continue      # this person's copy in that system is already accounted for by identifier evidence
        rep = _P(c.reported_at)
        allowed = CLAIM_RESULTS.get(c.reported_status, {"SUCCESS", "FAILED", "PARTIAL"}) if spec["claim"] else None
        cs: dict[str, tuple[float, dict]] = {}
        for lo, hi in spec.get("windows", [spec["window"]]):
            for a in events:
                d = (_P(a["event_time"]) - rep).total_seconds()
                o = owner.get(a["resource_id"])
                if lo <= d <= hi and (allowed is None or a["reported_result"] in allowed) and (not o or u.user_id in o):
                    cs[a["resource_id"]] = (d, a)
            if cs:
                break          # earlier windows take precedence
        cands[c.case_id] = cs

    assigned: dict[str, dict] = {}

    def take(cid: str, rid: str, conf: str) -> None:
        assigned[cid] = {"resource_id": rid, "activity_id": cands[cid][rid][1]["activity_id"], "confidence": conf}

    # 1. evidence says this resource belongs to this person
    for cid, cs in cands.items():
        mine = [r for r in cs if users[cid] in owner.get(r, ())]
        if len(mine) == 1:
            take(cid, mine[0], "evidence")

    # 2. exclusivity propagation until nothing changes
    changed = True
    while changed:
        changed = False
        for cid, cs in cands.items():
            if cid in assigned:
                continue
            taken = {a["resource_id"] for a in assigned.values()}
            for r in list(cs):
                if r in taken:
                    del cs[r]
            if len(cs) == 1:
                take(cid, next(iter(cs)), "unique")
                changed = True

    # 3. symmetric clusters: requests that share an identical candidate set are paired
    #    with its events in order (heuristic - the stream carries no better tie-breaker)
    pending = defaultdict(list)
    for cid, cs in cands.items():
        if cid not in assigned and cs:
            pending[frozenset(cs)].append(cid)
    for rids, cids in pending.items():
        if len(cids) == len(rids):
            ordered_cases = sorted(cids, key=lambda x: int(re.sub(r"\D", "", x) or 0))
            ordered_res = sorted(rids, key=lambda r: cands[cids[0]][r][1]["event_time"])
            for cid, rid in zip(ordered_cases, ordered_res):
                take(cid, rid, "ordered")
    return assigned


def assign_accounts(cases, resolver, owner, activity):
    return assign_events(cases, resolver, owner, activity, "account")


def assign_all(cases, resolver, owner, activity, occupied=None) -> dict[str, dict[str, dict]]:
    """group -> case_id -> assignment."""
    return {g: assign_events(cases, resolver, owner, activity, g, occupied) for g in EVENT_GROUPS}
