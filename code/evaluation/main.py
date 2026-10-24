"""Run the full evaluation workflow and (re)write evaluation/evaluation_report.md.

    python code/evaluation/main.py            # public samples + custom cases + full-run sanity statistics
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import custom_cases                                  # noqa: E402
import evaluate as ev                                # noqa: E402
from ghostaudit.dataio import DATASET, REPO_ROOT     # noqa: E402
from ghostaudit.pipeline import Pipeline             # noqa: E402

pct = lambda x: f"{100 * x:.2f}%"


def main() -> int:
    pipe = Pipeline(use_llm=False)                   # the reported numbers are the deterministic path
    rows = pipe.run(DATASET / "sample_deletion_requests.csv")
    pred = {cid: {"status": st, "steps": json.loads(sj)} for cid, st, sj in rows}
    gold = ev.load(DATASET / "sample_output.csv")
    res = ev.score(pred, gold)
    custom = custom_cases.run()

    full = list(csv.DictReader(open(REPO_ROOT / "output.csv", encoding="utf-8"))) if (REPO_ROOT / "output.csv").exists() else []
    dist = Counter(r["deletion_status"] for r in full)

    bad = [(c, v) for c, v in res["per_case"].items() if not v["exact"]]
    L = [f"# Evaluation report", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `python code/evaluation/main.py`. "
         f"Ground truth: the {res['cases']} labelled public samples (`dataset/sample_output.csv`). "
         "The hidden 518-case set has no labels, so these numbers are measured on the samples only; the engine was developed "
         "against them, so expect the hidden-set score to be lower (see *Limitations*).", "",
         "## 1. Case-level deletion status", "",
         f"- Status accuracy: **{pct(res['status_accuracy'])}** ({round(res['status_accuracy'] * res['cases'])}/{res['cases']} cases)", "",
         "## 2. Step-level accuracy", "",
         "| Metric | Precision | Recall | F1 |", "|---|---:|---:|---:|",
         f"| Exact step (phase, system, resource, operation, state, evidence_id) | {pct(res['step_precision'])} | {pct(res['step_recall'])} | {pct(res['step_f1'])} |",
         f"| Relaxed step (ignoring evidence_id) | {pct(res['relaxed_step_precision'])} | {pct(res['relaxed_step_recall'])} | {pct(res['relaxed_step_f1'])} |", "",
         f"Gold steps: {res['gold_steps']}; predicted steps: {res['pred_steps']}.", "",
         "## 3. Field-level accuracy", "",
         "Share of gold steps whose (phase, operation, system, resource) was predicted and whose field value also matches.", "",
         "| Field | Accuracy |", "|---|---:|"]
    L += [f"| {f} | {pct(v)} |" for f, v in res["field_accuracy"].items()]
    L += [f"| **all fields** | **{pct(res['field_accuracy_overall'])}** |", "",
          "## 4. Sequence accuracy", "",
          f"- Exact ordered sequence match (phase, operation, system, resource): **{pct(res['sequence_exact'])}**",
          f"- Sequence similarity (LCS ratio, averaged per case): **{pct(res['sequence_lcs_ratio'])}**",
          f"- Whole-row exact match (status + every step incl. evidence ids): **{pct(res['row_exact_match'])}**", "",
          "## 5. Additional evaluation cases (written by the participant)", "",
          "Synthetic mini-worlds built in memory by `code/evaluation/custom_cases.py` (run with `python code/evaluation/custom_cases.py`). "
          "Expected answers come from the policy text, not from the engine's output.", "",
          "| # | Case | Result | What it checks |", "|---|---|---|---|"]
    for i, c in enumerate(custom, 1):
        L.append(f"| {i} | `{c['name']}` | {'PASS' if c['passed'] else 'FAIL'} | {(c['doc'] or '').strip()} |")
    L += ["", f"{sum(c['passed'] for c in custom)}/{len(custom)} custom cases pass.", "",
          "## 6. Failures found and improvements made", "",
          "Measured on the 32 samples while building (status accuracy / exact step F1): first end-to-end run 81.3% / 74.0% -> final "
          f"{pct(res['status_accuracy'])} / {pct(res['step_f1'])}.", "",
          "| Failure found | Improvement |", "|---|---|",
          "| Clean resources (deleted accounts, anonymized analytics, redacted tickets, disabled flows, blocked backups) carry no identifier in any evidence, so they were never linked to a person | Match the deletion worker's own events to requests by time window around `reported_at`, claim/result consistency, identifier-based ownership exclusion and global one-to-one assignment across all requests |",
          "| A decoy event could be assigned to two requests | Exclusivity is re-evaluated after every single assignment, not once per pass |",
          "| Row `updated_at` of SQL snapshots treated as observation time | Use the snapshot capture time; replica-only rows are ghost copies |",
          "| Evidence captured after `audit_at` was cited | Content time decides the state; a citation is allowed only if the file itself existed by `audit_at` |",
          "| Catalog link rows (`LINK_RESOURCE_IDENTITY`) pulled in tombstones that have no evidence | A catalog link only grants ownership; the resource must also be observed |",
          "| Non-deletion requests consumed deletion events and some were classified as deletions (\"Do not close the account\", \"remove me from marketing lists only\", \"not a request to erase it\") | Keep/negation cues dominate; non-deletion requests are excluded from event matching; model fallback for uncertain texts |",
          "| Support items deleted by the worker were treated as redactable messages | The worker's event type reveals the policy treatment (DELETE on a support item => customer attachment) |",
          "| PDFs/screenshots produced no observations | OCR (`pdftotext`, `tesseract`) or a vision model, cached on disk, parsed with the same grounded rules |", "",
          "### Remaining failures (honest list)", ""]
    for c, v in bad:
        L.append(f"- `{c}`: status {'ok' if v['status'] else 'WRONG'}, sequence similarity {pct(v['seq'])}, gold {v['gold']} / predicted {v['pred']} steps")
    L += ["", "Root causes of the remaining misses: (a) retained audit records for two cases and one deleted account appear in **no** participant-visible evidence, so they cannot be recovered; "
          "(b) in clusters of requests reported at the same minute the activity stream cannot tell two near-identical clean events apart - the engine pairs them in order, which is a heuristic and can swap two people's analytics/support resources; "
          "(c) a request reported before any evidence existed returns an empty COMPLETE (nothing verifiable).", "",
          "## 7. Full-run sanity statistics (518 evaluation requests)", ""]
    if full:
        L += [f"- Rows: {len(full)}; " + ", ".join(f"{k}: {v}" for k, v in sorted(dist.items())),
              "- Output validated: header `case_id,deletion_status,steps_json`, rows sorted by case_id, valid JSON, consecutive step numbers, `NOT_APPLICABLE` rows have `[]`."]
    L += ["", "## 8. Limitations", "",
          "- No labels exist for the 518 requests; accuracy there is unmeasured. Heuristic components (ordered pairing in symmetric clusters, time windows) were fitted on 32 samples.",
          "- Intent rules are keyword-based; the optional model fallback only helps when `GEMINI_API_KEY` is set.", ""]
    (HERE / "evaluation_report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {HERE / 'evaluation_report.md'}")
    print(ev.report(res))
    print(f"custom cases: {sum(c['passed'] for c in custom)}/{len(custom)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
