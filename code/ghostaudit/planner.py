"""CaseAudit -> ordered steps_json, following the operation order in the problem statement."""
from __future__ import annotations

import json

from . import models as M
from .engine import CaseAudit, ResourceVerdict
from .models import Step
from .policy import ANONYMIZE, DELETE, REDACT, RETAIN

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]
PHASE_ORDER = {"FINDING": FINDING_ORDER, "REMEDIATION": REMEDIATION_ORDER, "VERIFICATION": VERIFICATION_ORDER}

# action -> (finding op, finding state, remediation op, remediation state, verification op, verification state)
OPS = {
    DELETE: ("REPORT_REMAINING_RECORD", M.PRESENT, "DELETE_RECORD", "ABSENT", "VERIFY_RECORD_ABSENT", "ABSENT"),
    REDACT: ("REPORT_EXPOSED_PII", M.PRESENT, "REDACT_RECORD", "REDACTED", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ANONYMIZE: ("REPORT_EXPOSED_PII", M.PRESENT, "ANONYMIZE_RECORD", "ANONYMIZED", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    "FLOW": ("REPORT_ACTIVE_DATA_FLOW", M.ACTIVE, "DISABLE_DATA_FLOW", "DISABLED", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    "BACKUP": ("REPORT_RESTORE_RISK", M.ACTIVE, "BLOCK_RESTORE", "BLOCKED", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
    RETAIN: (None, None, None, None, "VERIFY_RECORD_PRESENT", "PRESENT"),
}


def _sort(steps: list[Step]) -> list[Step]:
    order = {ph: {op: i for i, op in enumerate(ops)} for ph, ops in PHASE_ORDER.items()}
    phases = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
    return sorted(steps, key=lambda s: (phases[s.phase], order[s.phase][s.operation], s.system_id, s.resource_id))


def plan(audit: CaseAudit) -> list[Step]:
    if not audit.applicable:
        return []
    steps: list[Step] = []
    complete = audit.status == "COMPLETE"
    for r in audit.resources:
        f_op, f_state, r_op, r_state, v_op, v_state = OPS[r.action]
        if not r.compliant and f_op:
            steps.append(Step("FINDING", r.system_id, r.resource_id, f_op, f_state, r.evidence_id))
            steps.append(Step("REMEDIATION", r.system_id, r.resource_id, r_op, r_state, ""))
        # verification: proves the current state (COMPLETE) or the required end state (INCOMPLETE)
        steps.append(Step("VERIFICATION", r.system_id, r.resource_id, v_op, v_state,
                          r.evidence_id if complete else ""))
    steps = _sort(steps)
    for i, s in enumerate(steps, 1):
        s.step_no = i
    return steps


def to_json(steps: list[Step]) -> str:
    return json.dumps([s.to_json() for s in steps], ensure_ascii=False, separators=(",", ":"))
