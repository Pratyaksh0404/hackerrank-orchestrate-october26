# Usage report - final full-dataset run

Generated 2026-10-24 15:19 UTC by `python code/main.py` over 518 requests (the run that produced `output.csv`).

Mode: **model-assisted**

| Provider | Model | Calls | Cache hits | Failed calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Google Gemini (AI Studio) | gemini-3.1-flash-lite | 12 | 0 | 0 | 2,887 | 466 | 3,353 | 0.0000 |

**Overall totals**

- Model calls: 12
- Input / output tokens: 2,887 / 466
- Average tokens per request: 6.5 (input 5.6, output 0.9)
- Estimated total cost: $0.0000; per request: $0.000000

**Per purpose**

| Purpose | Calls | Input tokens | Output tokens |
|---|---:|---:|---:|
| intent | 12 | 2,887 | 466 |

**How the model is used**

The audit itself (identity resolution, evidence reconciliation, policy, planning) is deterministic code and never calls a model. A model is consulted only at the edges: (1) request-intent classification for texts the offline rules cannot decide with confidence (non-English wording, or deletion language mixed with keep/other-action cues), and (2) transcription of screenshots/PDFs when local OCR is unavailable. Responses are cached on disk, so a repeated run makes 0 new calls.

Pricing: the Gemini free tier is $0; set `LLM_PRICE_IN` / `LLM_PRICE_OUT` (USD per 1M tokens) to estimate a paid-tier cost.
