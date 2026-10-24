"""Audit engine: reconstruct what is true at audit_at and decide every resource's compliance."""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

from . import models as M
from .activity import ActivityIndex, assign_all
from .evidence import EvidenceStore
from .identity import IdentityResolver
from .models import Case, Coverage, Identity, Obs, norm
from .policy import ANONYMIZE, BACKUP_CATEGORY, DELETE, FLOW_CATEGORY, REDACT, RETAIN, Policy


@dataclass
class ResourceVerdict:
    system_id: str
    resource_id: str
    category: str
    action: str                      # DELETE / REDACT / ANONYMIZE / RETAIN / FLOW / BACKUP
    compliant: bool
    evidence_id: str                 # evidence proving the *current* state ("" if none)
    state: str                       # latest normalized state at audit_at
    how_linked: str                  # identifier / activity / link-row ...
    note: str = ""


@dataclass
class CaseAudit:
    case: Case
    identity: Identity | None
    applicable: bool
    resources: list[ResourceVerdict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.applicable:
            return "NOT_APPLICABLE"
        return "COMPLETE" if all(r.compliant for r in self.resources) else "INCOMPLETE"


class AuditEngine:
    def __init__(self, store: EvidenceStore, resolver: IdentityResolver, activity: ActivityIndex,
                 policy: Policy, all_cases: list[Case]) -> None:
        self.store, self.resolver, self.activity, self.policy = store, resolver, activity, policy
        self.key2user = {k: u.user_id for u in resolver.by_user.values() for k in u.all_keys()}
        self.owner: dict[str, set[str]] = collections.defaultdict(set)
        for o in store.obs:
            for k in o.ids:
                if k in self.key2user:
                    self.owner[o.resource_id].add(self.key2user[k])
        for res, link in activity.links.items():
            for k in link["keys"]:
                if k in self.key2user:
                    self.owner[res].add(self.key2user[k])
        # systems in which identifier evidence already places a (violating) copy of each person's data
        occupied: dict[str, set[str]] = {}
        for c in all_cases:
            u = resolver.resolve(c.email)
            if not u:
                continue
            occupied[c.case_id] = {o.system_id for k in u.all_keys() for o in store.by_key.get(k, [])
                                   if self._in_window(o, c) and o.state in (M.PRESENT, M.IDENTIFIABLE, M.ACTIVE)}
        self.assigned = assign_all(all_cases, resolver, self.owner, activity, occupied)

    # ------------------------------------------------------------------ discovery
    def _in_window(self, o: Obs, case: Case) -> bool:
        """State is judged by when the observation was made (content time)."""
        return case.requested_at <= o.observed_at <= case.audit_at

    def _cite(self, case: Case, hits: list[Obs], state_set) -> str:
        """Evidence id to cite: latest matching observation whose file existed by audit_at ('' if none)."""
        ok = [o for o in hits if o.state in state_set and (self.store.captured_at(o.evidence_id) or "") <= case.audit_at]
        return max(ok, key=lambda o: (o.observed_at, o.evidence_id)).evidence_id if ok else ""

    def discover(self, case: Case, ident: Identity) -> dict[str, str]:
        """resource_id -> how it was linked to the subject."""
        found: dict[str, str] = {}
        keys = ident.all_keys()
        for k in keys:
            for o in self.store.by_key.get(k, []):
                if self._in_window(o, case):
                    found.setdefault(o.resource_id, "identifier")
        for res, link in self.activity.links.items():
            if link["keys"] & keys and link["time"] <= case.audit_at and res in self.store.by_resource:
                found.setdefault(res, "catalog-link")
        for group, by_case in self.assigned.items():
            a = by_case.get(case.case_id)
            if a and (group == "account" or a["resource_id"] in self.store.by_resource):
                found.setdefault(a["resource_id"], f"workflow-event:{group}:{a['confidence']}")
        return found

    # ------------------------------------------------------------------ evaluation
    def _category(self, rid: str, obs: list[Obs], link: dict | None) -> str:
        cats = [o.category for o in obs if o.category]
        if link and link.get("exclusive"):
            return "SUPPORT_ATTACHMENT"
        # the worker's own event type reveals the treatment it was asked to apply: deleting a
        # support item means it is a customer-owned attachment (messages are redacted, never deleted)
        if M.system_of(rid) == "support_platform" and any(
                e["source_system"] == "deletion_worker" and e["event_type"] == "DELETE_RECORD"
                for e in self.activity.events_for(rid)):
            return "SUPPORT_ATTACHMENT"
        if cats:
            return collections.Counter(cats).most_common(1)[0][0]
        return M.category_of(rid) or ""

    def evaluate(self, case: Case, rid: str, how: str) -> ResourceVerdict | None:
        obs = sorted((o for o in self.store.by_resource.get(rid, []) if self._in_window(o, case)),
                     key=lambda o: o.observed_at)
        link = self.activity.links.get(rid)
        cat = self._category(rid, obs, link)
        system = M.system_of(rid) or (obs[0].system_id if obs else "")
        if cat == "SUPPORT_MESSAGE" and any(o.category == "SUPPORT_ATTACHMENT" for o in obs):
            cat = "SUPPORT_ATTACHMENT"
        if system == "object_storage":
            cat = "SUPPORT_ATTACHMENT"
        action = self.policy.action(cat)
        if cat == FLOW_CATEGORY:
            action = "FLOW"
        elif cat == BACKUP_CATEGORY:
            action = "BACKUP"
        if action is None:
            return None

        def latest(state_set):
            hits = [o for o in obs if o.state in state_set]
            return max(hits, key=lambda o: (o.observed_at, o.evidence_id)) if hits else None

        bad_states, good_states = {
            DELETE: ({M.PRESENT, M.IDENTIFIABLE}, {M.ABSENT}),
            REDACT: ({M.IDENTIFIABLE}, {M.REDACTED}),
            ANONYMIZE: ({M.IDENTIFIABLE}, {M.ANONYMIZED}),
            "FLOW": ({M.ACTIVE}, {M.DISABLED}),
            "BACKUP": ({M.ACTIVE}, {M.BLOCKED}),
            RETAIN: (set(), {M.PRESENT}),
        }[action]

        bad, good = latest(bad_states), latest(good_states)
        # a violating observation only counts if no later (or simultaneous) compliant one supersedes it
        violating = bad is not None and (good is None or bad.observed_at >= good.observed_at)
        if action == RETAIN:
            ok_obs = latest({M.PRESENT})
            return ResourceVerdict(system, rid, cat, action, ok_obs is not None,
                                   self._cite(case, obs, {M.PRESENT}) if ok_obs else "", M.PRESENT if ok_obs else "UNKNOWN", how)
        if violating:
            return ResourceVerdict(system, rid, cat, action, False, self._cite(case, obs, bad_states), bad.state, how)
        if good is not None:
            return ResourceVerdict(system, rid, cat, action, True, self._cite(case, obs, good_states), good.state, how)

        # No direct observation. A DELETE target is provably absent only if a snapshot that
        # covers its system, captured after the request, does not contain it.
        if action == DELETE:
            cov = self._coverage_proof(case, system)
            if cov:
                return ResourceVerdict(system, rid, cat, action, True, cov.evidence_id, M.ABSENT, how,
                                       note=f"absent from {cov.detail}")
        return ResourceVerdict(system, rid, cat, action, True, "", "UNVERIFIED", how, note="no evidence either way")

    def _coverage_proof(self, case: Case, system: str) -> Coverage | None:
        cands = [c for c in self.store.cov_by_system.get(system, [])
                 if case.requested_at <= c.captured_at <= case.audit_at]
        if not cands:
            return None
        return max(cands, key=lambda c: (c.captured_at, c.evidence_id))

    def audit(self, case: Case, applicable: bool = True) -> CaseAudit:
        ident = self.resolver.resolve(case.email)
        if not applicable or ident is None:
            return CaseAudit(case, ident, False)
        out = CaseAudit(case, ident, True)
        for rid, how in sorted(self.discover(case, ident).items()):
            v = self.evaluate(case, rid, how)
            if v:
                out.resources.append(v)
        return out
