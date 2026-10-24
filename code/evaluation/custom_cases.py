"""Participant-authored evaluation cases.

Each case builds a tiny synthetic world in memory (identities, observations, activity) and states
the answer a careful auditor would give from the policy text alone. They exercise rules the 32 public
samples cover only thinly: replica drift, evidence from the future, retained records, remediation order,
and request-intent edge cases. No dataset files are read except deletion_rules.csv.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit import intent                                   # noqa: E402
from ghostaudit.activity import ActivityIndex                   # noqa: E402
from ghostaudit.engine import AuditEngine                       # noqa: E402
from ghostaudit.identity import IdentityResolver                # noqa: E402
from ghostaudit.models import Case, Coverage, Identity, Obs    # noqa: E402
from ghostaudit.planner import plan                             # noqa: E402
from ghostaudit.policy import Policy                            # noqa: E402

EMAIL = "ada.example@mail.test"
T0, REQ, REP, AUD = "2026-05-01T00:00:00Z", "2026-05-02T10:00:00Z", "2026-05-03T10:00:00Z", "2026-05-10T10:00:00Z"


def world(observations: list[Obs], coverage: list[Coverage] | None = None, captured: dict | None = None):
    store = SimpleNamespace(obs=observations, coverage=coverage or [], by_key=defaultdict(list),
                            by_resource=defaultdict(list), cov_by_system=defaultdict(list))
    for o in observations:
        store.by_resource[o.resource_id].append(o)
        for k in o.ids:
            store.by_key[k].append(o)
    for c in store.cov_by_system and [] or (coverage or []):
        store.cov_by_system[c.system_id].append(c)
    store.captured_at = lambda ev, _c=captured or {}: _c.get(ev, "2026-05-05T00:00:00Z")
    res = IdentityResolver.__new__(IdentityResolver)
    ident = Identity("user_900", "Ada Example", {EMAIL}, {"+1 555 0100"}, set(), EMAIL)
    res.by_user, res.email_index, res.audit_by_user = {"user_900": ident}, {EMAIL: "user_900"}, {}
    case = Case("custom", EMAIL, REQ, "COMPLETE", REP, AUD, "Please delete my account.")
    return AuditEngine(store, res, ActivityIndex([]), Policy(), [case]), case


def steps_of(eng, case):
    audit = eng.audit(case, applicable=True)
    return audit.status, [(s.phase, s.operation, s.resource_id, s.state) for s in plan(audit)]


def obs(ev, ts, rid, state, system, ids=(EMAIL,), cat=""):
    return Obs(ev, ts, system, rid, state, frozenset(ids), kind="custom", category=cat)


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def replica_drift_ghost_copy():
    """Primary index says the entry is gone, a later replica read still serves it -> INCOMPLETE, delete it."""
    eng, c = world([
        obs("e1", "2026-05-04T08:00:00Z", "search_11", "ABSENT", "search_service"),
        obs("e2", "2026-05-05T08:00:00Z", "search_11", "PRESENT", "search_service")])
    return (eng, c), ("INCOMPLETE", [("FINDING", "REPORT_REMAINING_RECORD", "search_11", "PRESENT"),
                                     ("REMEDIATION", "DELETE_RECORD", "search_11", "ABSENT"),
                                     ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_11", "ABSENT")])


@case
def evidence_after_audit_is_ignored():
    """A violating record that was only observed after audit_at must not change the verdict."""
    eng, c = world([
        obs("e1", "2026-05-04T08:00:00Z", "search_12", "ABSENT", "search_service"),
        obs("e2", "2026-05-20T08:00:00Z", "search_12", "PRESENT", "search_service")])
    return (eng, c), ("COMPLETE", [("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_12", "ABSENT")])


@case
def retained_audit_log_is_preserved_and_message_redacted():
    """Audit log must stay (verify PRESENT, never delete); the support message is REDACTED, not deleted."""
    eng, c = world([
        obs("e1", "2026-05-04T08:00:00Z", "audit_record_5", "PRESENT", "security_service"),
        obs("e2", "2026-05-04T08:00:00Z", "support_21", "IDENTIFIABLE", "support_platform")])
    return (eng, c), ("INCOMPLETE", [("FINDING", "REPORT_EXPOSED_PII", "support_21", "PRESENT"),
                                     ("REMEDIATION", "REDACT_RECORD", "support_21", "REDACTED"),
                                     ("VERIFICATION", "VERIFY_RECORD_PRESENT", "audit_record_5", "PRESENT"),
                                     ("VERIFICATION", "VERIFY_RECORD_REDACTED", "support_21", "REDACTED")])


@case
def remediation_order_flow_then_restore_then_records():
    """Disable the sync flow and block the restore BEFORE deleting dependent records."""
    eng, c = world([
        obs("e1", "2026-05-04T08:00:00Z", "data_flow_7", "ACTIVE", "integration_service"),
        obs("e1", "2026-05-04T08:00:00Z", "backup_9", "ACTIVE", "backup_service"),
        obs("e1", "2026-05-04T08:00:00Z", "search_13", "PRESENT", "search_service")])
    status, steps = steps_of(eng, c)
    order = [s[1] for s in steps if s[0] == "REMEDIATION"]
    ok = order == ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD"]
    return None, ("INCOMPLETE", "remediation order", ok, status, order)


@case
def anonymized_analytics_record_is_compliant():
    """Analytics profile already anonymized after the request -> compliant, verified as ANONYMIZED."""
    eng, c = world([
        obs("e1", "2026-05-03T08:00:00Z", "analytics_31", "IDENTIFIABLE", "analytics_platform"),
        obs("e2", "2026-05-06T08:00:00Z", "analytics_31", "ANONYMIZED", "analytics_platform")])
    return (eng, c), ("COMPLETE", [("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "analytics_31", "ANONYMIZED")])


INTENT_CASES = [
    ("Please permanently delete the account associated with this email address.", True),
    ("Solicito la eliminación permanente de la cuenta asociada a este correo.", True),
    ("Je demande la suppression définitive du compte associé à cette adresse e-mail.", True),
    ("Please remove me from marketing lists only. I still want receipts for this account.", False),
    ("Can you tell me what personal information you hold about me? This is an information request, not a request to erase it.", False),
    ("My surname is misspelled on the account. Can you correct it without removing any of my records?", False),
    ("I found an old report saying my account still existed. Please confirm the profile is gone.", True),
]


def run() -> list[dict]:
    results = []
    for fn in CASES:
        built, expected = fn()
        if built is None:                                  # structural check
            _, label, ok, status, order = expected
            results.append({"name": fn.__name__, "doc": fn.__doc__, "passed": ok and status == "INCOMPLETE",
                            "detail": f"status={status}, remediation order={order}"})
            continue
        status, steps = steps_of(*built)
        want_status, want_steps = expected
        results.append({"name": fn.__name__, "doc": fn.__doc__, "passed": status == want_status and steps == want_steps,
                        "detail": f"status={status}; steps={steps}"})
    got = [(t, intent.classify(t)[0]) for t, _ in INTENT_CASES]
    wrong = [t[:50] for (t, g), (_, w) in zip(got, INTENT_CASES) if g != w]
    results.append({"name": "request_intent_multilingual_and_negations", "doc": "7 request texts, 3 languages, negations and opt-outs",
                    "passed": not wrong, "detail": "all 7 correct" if not wrong else f"wrong: {wrong}"})
    return results


if __name__ == "__main__":
    for r in run():
        print(("PASS" if r["passed"] else "FAIL"), r["name"], "-", r["detail"][:150])
