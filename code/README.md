# Ghost Data Audit - deletion-audit agent for Northstar Shop

For every request in `dataset/deletion_requests.csv` the agent decides whether the person really asked for deletion,
reconstructs what is true at that case's `audit_at`, applies `deletion_rules.csv`, and writes `output.csv`
(`COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` plus ordered FINDING -> REMEDIATION -> VERIFICATION steps).

## Setup (Windows, macOS, Linux)

Requirements: **Python 3.10+**. No pip packages are needed (standard library only).
Optional, only for screenshots/PDFs when no model key is set: `tesseract` and `pdftotext` (poppler) on PATH.

```bash
git clone https://github.com/interviewstreet/hackerrank-orchestrate-october26
cd hackerrank-orchestrate-october26
# put this solution's files in the repo's code/ folder (code/main.py, code/ghostaudit/, code/prompts/, code/evaluation/)
python code/main.py            # -> output.csv at the repo root (518 rows, ~3 s, offline)
python code/evaluation/main.py # -> code/evaluation/evaluation_report.md (samples, custom cases)
python code/dashboard.py       # -> dashboard.html (open in a browser)
```

Optional model assistance (free tier of Google AI Studio): `export GEMINI_API_KEY=...` (or put it in an uncommitted
`.env`, see `.env.example`), then run `python code/main.py`. Without a key everything runs offline.
`python code/main.py --no-llm` forces offline mode. `python code/main.py --samples` audits the 32 public samples to
`output_samples.csv`; `python code/evaluation/evaluate.py --diff bad` shows every differing sample side by side.

## How it works

Deterministic core, model only at the edges.

1. **Identity** (`identity.py`): request email -> `user_id` -> every email / phone / sha256 hash the person ever had
   (`user_identities.csv` + `user_audit_log.csv`). Historical emails resolve too.
2. **Evidence -> observations** (`parsers/`): SQL snapshots (run in SQLite; primary/replica/failover tables compared),
   JSON exports, service logs in four dialects (trace-id association), handover text, and PDFs/screenshots
   (OCR or vision model). Every fact becomes `Obs(evidence_id, observed_at, resource, state, identifiers)`.
   A snapshot that enumerates a system is also a *coverage* record = proof that a missing record is really absent.
3. **Time rules** (`engine.py`): a fact counts only if observed between `requested_at` and `audit_at`; a citation is
   allowed only if the evidence file itself was captured by `audit_at`; a present copy that is as new as or newer than
   the latest absence (replica drift) is a violation. `reported_status` and workflow results are treated as claims.
4. **Linking records to people** : identifiers/hashes/resource ids in evidence; catalog link rows; and for compliant
   resources that carry no identifier at all (deleted accounts, anonymized analytics...), the deletion worker's own
   events in `system_activity.csv`, matched to requests by time window, claim consistency, ownership exclusion and
   one-to-one assignment (`activity.py`). Ties between near-identical events are broken in order - a disclosed heuristic.
5. **Policy** (`policy.py`): the action per data category is read from `deletion_rules.csv`.
   Retained records (audit logs, legal holds) must still be present; tombstones keep only the stable user id.
6. **Plan** (`planner.py`): findings, then remediation (disable flows -> block restores -> delete/redact/anonymize),
   then verification; evidence ids only where evidence proves the current state.
7. **Model edges** (`llm.py`, `prompts/`): request intent for texts the rules are unsure about, and transcription of
   images when OCR is unavailable. Cached on disk; token usage is recorded for `evaluation/usage_report.md`.

## Layout

```
main.py                 CLI
dashboard.py            HTML dashboard from output.csv
ghostaudit/             library (identity, parsers/, evidence, activity, policy, engine, planner, intent, llm, pipeline, reports)
prompts/                model prompts
evaluation/             evaluate.py (metrics), custom_cases.py, main.py, evaluation_report.md, usage_report.md
```

Secrets are read from environment variables only. Only participant-facing files under `dataset/` are used.
