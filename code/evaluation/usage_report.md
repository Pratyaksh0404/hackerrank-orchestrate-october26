# Usage report - final full-dataset run

Generated 2026-10-24 16:50 UTC by `python code/main.py` over 518 requests (the run that produced `output.csv`).

Mode: **model-assisted**

| Provider | Model | Calls | Cache hits | Failed calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Google Gemini (AI Studio) | gemini-3.1-flash-lite | 0 | 12 | 0 | 0 | 0 | 0 | 0.0000 |

**Overall totals**

- Model calls: 0
- Input / output tokens: 0 / 0
- Average tokens per request: 0.0 (input 0.0, output 0.0)
- Estimated total cost: $0.0000; per request: $0.000000

**Re-used cached responses:** 12 answers from earlier calls were read from the on-disk cache (originally 2,887 input / 466 output tokens; not counted in the totals above because no new call was made).

**How the model is used**

The audit itself (identity resolution, evidence reconciliation, policy, planning) is deterministic code and never calls a model. A model is consulted only at the edges: (1) request-intent classification for texts the offline rules cannot decide with confidence (non-English wording, or deletion language mixed with keep/other-action cues), and (2) transcription of screenshots/PDFs when local OCR is unavailable. Responses are cached on disk, so a repeated run makes 0 new calls.

This run was executed **offline** (no `GEMINI_API_KEY`, or `--no-llm`): intent was decided by the offline rules and images/PDFs were read by local OCR (`tesseract`, `pdftotext`). Zero tokens were used. Re-run `python code/main.py` with a key to enable the model-assisted path; this file is regenerated on every full run.

Pricing: the Gemini free tier is $0; set `LLM_PRICE_IN` / `LLM_PRICE_OUT` (USD per 1M tokens) to estimate a paid-tier cost.
