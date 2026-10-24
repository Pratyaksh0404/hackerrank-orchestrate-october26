"""End-to-end pipeline: requests.csv -> [(case_id, status, steps_json)]."""
from __future__ import annotations

import logging
from pathlib import Path

from . import intent as intent_mod
from . import llm as llm_mod
from .activity import ActivityIndex
from .dataio import DATASET, REPO_ROOT, load_activity, load_cases
from .engine import AuditEngine, CaseAudit
from .evidence import EvidenceStore
from .identity import IdentityResolver
from .planner import plan, to_json
from .parsers.visual import VisualReader
from .policy import Policy

log = logging.getLogger("ghostaudit")


def natural_key(case_id: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", case_id)]


class Pipeline:
    def __init__(self, use_llm: bool = True) -> None:
        self.client = llm_mod.GeminiClient.from_env() if use_llm else None
        self.resolver = IdentityResolver()
        self.activity = ActivityIndex(load_activity())
        self.policy = Policy()
        vlm = (lambda path: llm_mod.read_screen(self.client, path)) if self.client else None
        self.store = EvidenceStore(visual=VisualReader(cache_dir=REPO_ROOT / ".cache" / "ocr", vlm=vlm))
        self.intent_reasons: dict[str, str] = {}
        self._intent_cache: dict[str, tuple[bool, str]] = {}
        # every known request participates in the global event matching, not just the one being run;
        # requests that do not authorize deletion never consumed a deletion-worker event
        everyone = load_cases(DATASET / "sample_deletion_requests.csv") + load_cases(DATASET / "deletion_requests.csv")
        self.universe = [c for c in everyone if self.authorizes_deletion(c)]
        self.engine = AuditEngine(self.store, self.resolver, self.activity, self.policy, self.universe)
        self.audits: dict[str, CaseAudit] = {}

    def authorizes_deletion(self, case) -> bool:
        """Offline rules decide clear-cut texts; the model is consulted only for uncertain ones."""
        text = case.request_text
        if text not in self._intent_cache:
            ok, why, confident = intent_mod.classify(text)
            if not confident and self.client and not self.client.disabled:
                try:
                    ok, why = llm_mod.classify_intent(self.client, text)
                    why = f"model: {why}"
                except Exception as exc:
                    log.info("intent model unavailable (%s); using offline rules", exc)
            self._intent_cache[text] = (ok, why)
        ok, why = self._intent_cache[text]
        self.intent_reasons[case.case_id] = why
        return ok

    def run(self, requests_csv: Path) -> list[tuple[str, str, str]]:
        rows = []
        for case in load_cases(requests_csv):
            audit = self.engine.audit(case, applicable=self.authorizes_deletion(case))
            self.audits[case.case_id] = audit
            rows.append((case.case_id, audit.status, to_json(plan(audit))))
        rows.sort(key=lambda r: natural_key(r[0]))
        return rows
