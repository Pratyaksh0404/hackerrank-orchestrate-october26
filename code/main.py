"""Ghost Data deletion-audit agent - command line entry point.

    python3 code/main.py                       # audit dataset/deletion_requests.csv -> output.csv
    python3 code/main.py --samples             # audit the 32 sample requests -> output_samples.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghostaudit.pipeline import Pipeline   # noqa: E402
from ghostaudit.dataio import DATASET, REPO_ROOT   # noqa: E402


def load_dotenv() -> None:
    """Tiny .env reader (KEY=VALUE lines) so PyCharm users need no extra package. Never overrides real env vars."""
    import os
    for d in (REPO_ROOT, Path(__file__).resolve().parent):
        f = d / ".env"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if v.strip() and k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def check_model() -> int:
    from ghostaudit.llm import GeminiClient
    client = GeminiClient.from_env()
    if not client:
        print("GEMINI_API_KEY is not set (check .env sits in the repo root and has a line GEMINI_API_KEY=...)")
        return 1
    print(f"key found ({len(client.api_key)} characters), model = {client.model}")
    try:
        names = client.list_models()
        print("models this key can use:", ", ".join(names) or "none")
    except Exception as exc:
        print("could not list models:", str(exc).replace(client.api_key, "[REDACTED]")[:300])
    try:
        print("test call ->", client.generate('Reply with the JSON {"ok": true}', "check").strip()[:80])
    except Exception as exc:
        print("test call FAILED:", exc)
        return 1
    return 0


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", action="store_true", help="run the 32 sample requests instead of the full set")
    ap.add_argument("--requests", type=Path, help="alternative requests csv")
    ap.add_argument("--out", type=Path, help="output csv path (default: <repo>/output.csv)")
    ap.add_argument("--no-llm", action="store_true", help="never call a model; deterministic + OCR only")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--check-model", action="store_true", help="test GEMINI_API_KEY / model and list usable models, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    if args.check_model:
        return check_model()

    requests = args.requests or (DATASET / ("sample_deletion_requests.csv" if args.samples else "deletion_requests.csv"))
    out = args.out or (REPO_ROOT / ("output_samples.csv" if args.samples else "output.csv"))
    pipe = Pipeline(use_llm=not args.no_llm)
    rows = pipe.run(requests)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out}")
    usage = pipe.client.usage if pipe.client else None
    report = {"mode": "model-assisted" if usage else "offline (no model calls)",
              "provider": usage.provider if usage else "none", "model": usage.model if usage else "none",
              "calls": usage.calls if usage else 0, "cache_hits": usage.cache_hits if usage else 0,
              "failures": usage.failures if usage else 0,
              "cached_input_tokens": usage.cached_input_tokens if usage else 0,
              "cached_output_tokens": usage.cached_output_tokens if usage else 0,
              "input_tokens": usage.input_tokens if usage else 0, "output_tokens": usage.output_tokens if usage else 0,
              "by_purpose": usage.by_purpose if usage else {}, "estimated_cost_usd": usage.cost() if usage else 0.0,
              "requests": len(rows)}
    (REPO_ROOT / ".cache").mkdir(exist_ok=True)
    (REPO_ROOT / ".cache" / ("usage_samples.json" if args.samples else "usage.json")).write_text(json.dumps(report, indent=2))
    if not args.samples and not args.requests:
        from ghostaudit.reports import write_usage_report   # noqa: E402
        write_usage_report(REPO_ROOT / "code" / "evaluation" / "usage_report.md", report)
    if usage:
        print(f"model calls: {usage.calls} (+{usage.cache_hits} cached), tokens in/out: {usage.input_tokens}/{usage.output_tokens}")
    if not args.samples and not args.requests:
        print("usage_report.md now describes THIS run" + (" (offline: 0 model calls)" if not usage else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
