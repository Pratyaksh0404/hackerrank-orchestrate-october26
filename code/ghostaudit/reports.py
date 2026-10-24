"""Markdown reports required by the submission contract."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def write_usage_report(path: Path, u: dict) -> None:
    n = max(1, u["requests"])
    tot = u["input_tokens"] + u["output_tokens"]
    offline = u["calls"] == 0
    lines = [
        "# Usage report - final full-dataset run",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `python code/main.py` "
        f"over {u['requests']} requests (the run that produced `output.csv`).",
        "",
        f"Mode: **{u['mode']}**",
        "",
        "| Provider | Model | Calls | Cache hits | Failed calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| {u['provider']} | {u['model']} | {u['calls']} | {u['cache_hits']} | {u['failures']} | "
        f"{u['input_tokens']:,} | {u['output_tokens']:,} | {tot:,} | {u['estimated_cost_usd']:.4f} |",
        "",
        "**Overall totals**",
        "",
        f"- Model calls: {u['calls']}",
        f"- Input / output tokens: {u['input_tokens']:,} / {u['output_tokens']:,}",
        f"- Average tokens per request: {tot / n:.1f} (input {u['input_tokens'] / n:.1f}, output {u['output_tokens'] / n:.1f})",
        f"- Estimated total cost: ${u['estimated_cost_usd']:.4f}; per request: ${u['estimated_cost_usd'] / n:.6f}",
        "",
    ]
    if u.get("cache_hits"):
        ci, co = u.get("cached_input_tokens", 0), u.get("cached_output_tokens", 0)
        lines += [f"**Re-used cached responses:** {u['cache_hits']} answers from earlier calls were read from the on-disk cache "
                  f"(originally {ci:,} input / {co:,} output tokens; not counted in the totals above because no new call was made).", ""]
    if u["by_purpose"]:
        lines += ["**Per purpose**", "", "| Purpose | Calls | Input tokens | Output tokens |", "|---|---:|---:|---:|"]
        lines += [f"| {k} | {v['calls']} | {v['input_tokens']:,} | {v['output_tokens']:,} |" for k, v in u["by_purpose"].items()]
        lines.append("")
    lines += ["**How the model is used**", "",
              "The audit itself (identity resolution, evidence reconciliation, policy, planning) is deterministic code and "
              "never calls a model. A model is consulted only at the edges: (1) request-intent classification for texts the "
              "offline rules cannot decide with confidence (non-English wording, or deletion language mixed with "
              "keep/other-action cues), and (2) transcription of screenshots/PDFs when local OCR is unavailable. "
              "Responses are cached on disk, so a repeated run makes 0 new calls.", ""]
    if offline:
        lines += ["This run was executed **offline** (no `GEMINI_API_KEY`, or `--no-llm`): intent was decided by the offline rules "
                  "and images/PDFs were read by local OCR (`tesseract`, `pdftotext`). Zero tokens were used. "
                  "Re-run `python code/main.py` with a key to enable the model-assisted path; this file is regenerated on every full run.", ""]
    lines += ["Pricing: the Gemini free tier is $0; set `LLM_PRICE_IN` / `LLM_PRICE_OUT` (USD per 1M tokens) to estimate a paid-tier cost.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
