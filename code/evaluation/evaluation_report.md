# Evaluation report

Generated 2026-10-24 15:19 UTC by `python code/evaluation/main.py`. Ground truth: the 32 labelled public samples (`dataset/sample_output.csv`). The hidden 518-case set has no labels, so these numbers are measured on the samples only; the engine was developed against them, so expect the hidden-set score to be lower (see *Limitations*).

## 1. Case-level deletion status

- Status accuracy: **100.00%** (32/32 cases)

## 2. Step-level accuracy

| Metric | Precision | Recall | F1 |
|---|---:|---:|---:|
| Exact step (phase, system, resource, operation, state, evidence_id) | 97.91% | 96.89% | 97.40% |
| Relaxed step (ignoring evidence_id) | 97.91% | 96.89% | 97.40% |

Gold steps: 193; predicted steps: 191.

## 3. Field-level accuracy

Share of gold steps whose (phase, operation, system, resource) was predicted and whose field value also matches.

| Field | Accuracy |
|---|---:|
| phase | 96.89% |
| system_id | 96.89% |
| resource_id | 96.89% |
| operation | 96.89% |
| state | 96.89% |
| evidence_id | 96.89% |
| **all fields** | **96.89%** |

## 4. Sequence accuracy

- Exact ordered sequence match (phase, operation, system, resource): **75.00%**
- Sequence similarity (LCS ratio, averaged per case): **96.68%**
- Whole-row exact match (status + every step incl. evidence ids): **75.00%**

## 5. Additional evaluation cases (written by the participant)

Synthetic mini-worlds built in memory by `code/evaluation/custom_cases.py` (run with `python code/evaluation/custom_cases.py`). Expected answers come from the policy text, not from the engine's output.

| # | Case | Result | What it checks |
|---|---|---|---|
| 1 | `replica_drift_ghost_copy` | PASS | Primary index says the entry is gone, a later replica read still serves it -> INCOMPLETE, delete it. |
| 2 | `evidence_after_audit_is_ignored` | PASS | A violating record that was only observed after audit_at must not change the verdict. |
| 3 | `retained_audit_log_is_preserved_and_message_redacted` | PASS | Audit log must stay (verify PRESENT, never delete); the support message is REDACTED, not deleted. |
| 4 | `remediation_order_flow_then_restore_then_records` | PASS | Disable the sync flow and block the restore BEFORE deleting dependent records. |
| 5 | `anonymized_analytics_record_is_compliant` | PASS | Analytics profile already anonymized after the request -> compliant, verified as ANONYMIZED. |
| 6 | `request_intent_multilingual_and_negations` | PASS | 7 request texts, 3 languages, negations and opt-outs |

6/6 custom cases pass.

## 6. Failures found and improvements made

Measured on the 32 samples while building (status accuracy / exact step F1): first end-to-end run 81.3% / 74.0% -> final 100.00% / 97.40%.

| Failure found | Improvement |
|---|---|
| Clean resources (deleted accounts, anonymized analytics, redacted tickets, disabled flows, blocked backups) carry no identifier in any evidence, so they were never linked to a person | Match the deletion worker's own events to requests by time window around `reported_at`, claim/result consistency, identifier-based ownership exclusion and global one-to-one assignment across all requests |
| A decoy event could be assigned to two requests | Exclusivity is re-evaluated after every single assignment, not once per pass |
| Row `updated_at` of SQL snapshots treated as observation time | Use the snapshot capture time; replica-only rows are ghost copies |
| Evidence captured after `audit_at` was cited | Content time decides the state; a citation is allowed only if the file itself existed by `audit_at` |
| Catalog link rows (`LINK_RESOURCE_IDENTITY`) pulled in tombstones that have no evidence | A catalog link only grants ownership; the resource must also be observed |
| Non-deletion requests consumed deletion events and some were classified as deletions ("Do not close the account", "remove me from marketing lists only", "not a request to erase it") | Keep/negation cues dominate; non-deletion requests are excluded from event matching; model fallback for uncertain texts |
| Support items deleted by the worker were treated as redactable messages | The worker's event type reveals the policy treatment (DELETE on a support item => customer attachment) |
| PDFs/screenshots produced no observations | OCR (`pdftotext`, `tesseract`) or a vision model, cached on disk, parsed with the same grounded rules |

### Remaining failures (honest list)

- `case_04`: status ok, sequence similarity 66.67%, gold 2 / predicted 1 steps
- `case_09`: status ok, sequence similarity 90.91%, gold 5 / predicted 6 steps
- `case_18`: status ok, sequence similarity 96.30%, gold 14 / predicted 13 steps
- `case_19`: status ok, sequence similarity 94.12%, gold 8 / predicted 9 steps
- `case_20`: status ok, sequence similarity 87.50%, gold 8 / predicted 8 steps
- `case_23`: status ok, sequence similarity 87.50%, gold 8 / predicted 8 steps
- `case_25`: status ok, sequence similarity 80.00%, gold 3 / predicted 2 steps
- `case_26`: status ok, sequence similarity 90.91%, gold 6 / predicted 5 steps

Root causes of the remaining misses: (a) retained audit records for two cases and one deleted account appear in **no** participant-visible evidence, so they cannot be recovered; (b) in clusters of requests reported at the same minute the activity stream cannot tell two near-identical clean events apart - the engine pairs them in order, which is a heuristic and can swap two people's analytics/support resources; (c) a request reported before any evidence existed returns an empty COMPLETE (nothing verifiable).

## 7. Full-run sanity statistics (518 evaluation requests)

- Rows: 518; COMPLETE: 155, INCOMPLETE: 353, NOT_APPLICABLE: 10
- Output validated: header `case_id,deletion_status,steps_json`, rows sorted by case_id, valid JSON, consecutive step numbers, `NOT_APPLICABLE` rows have `[]`.

## 8. Limitations

- No labels exist for the 518 requests; accuracy there is unmeasured. Heuristic components (ordered pairing in symmetric clusters, time windows) were fitted on 32 samples.
- Intent rules are keyword-based; the optional model fallback only helps when `GEMINI_API_KEY` is set.
