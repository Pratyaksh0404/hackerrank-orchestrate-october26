"""Evaluation harness: score predictions against labelled answers.

    python3 code/evaluation/evaluate.py                       # score output_samples.csv vs sample_output.csv
    python3 code/evaluation/evaluate.py --pred X --gold Y     # any pair of files
    python3 code/evaluation/evaluate.py --diff case_11        # show a case side by side

Metrics (all computed without looking at any particular case id):
  status accuracy      deletion_status matches
  step precision/recall/F1
                       exact match of (phase, system_id, resource_id, operation, state, evidence_id)
  relaxed step F1      same, ignoring evidence_id
  field accuracy       per field, over gold steps that were found (by phase/op/system/resource)
  sequence accuracy    exact ordered list of (phase, op, system, resource); plus LCS ratio
  row exact match      steps_json identical (apart from step_no)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path: Path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["case_id"]: {"status": r["deletion_status"], "steps": json.loads(r["steps_json"])} for r in csv.DictReader(fh)}


def full(s: dict) -> tuple:
    return tuple(s.get(f, "") for f in FIELDS)


def relaxed(s: dict) -> tuple:
    return tuple(s.get(f, "") for f in FIELDS if f != "evidence_id")


def ident(s: dict) -> tuple:
    return (s["phase"], s["operation"], s["system_id"], s["resource_id"])


def lcs(a: list, b: list) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def score(pred: dict, gold: dict) -> dict:
    cases = sorted(gold)
    m = Counter()
    field_ok, field_n = Counter(), Counter()
    seq_ratio = []
    per_case = {}
    for c in cases:
        g, p = gold[c], pred.get(c, {"status": "", "steps": []})
        m["status_ok"] += g["status"] == p["status"]
        gs, ps = [full(s) for s in g["steps"]], [full(s) for s in p["steps"]]
        gc, pc = Counter(gs), Counter(ps)
        tp = sum((gc & pc).values()); m["tp"] += tp; m["fp"] += sum(pc.values()) - tp; m["fn"] += sum(gc.values()) - tp
        gr, pr = Counter(relaxed(s) for s in g["steps"]), Counter(relaxed(s) for s in p["steps"])
        tpr = sum((gr & pr).values()); m["rtp"] += tpr; m["rfp"] += sum(pr.values()) - tpr; m["rfn"] += sum(gr.values()) - tpr
        pidx = {ident(s): s for s in p["steps"]}
        for s in g["steps"]:
            hit = pidx.get(ident(s))
            for f in FIELDS:
                field_n[f] += 1
                field_ok[f] += bool(hit) and hit.get(f, "") == s.get(f, "")
        gseq, pseq = [ident(s) for s in g["steps"]], [ident(s) for s in p["steps"]]
        m["seq_exact"] += gseq == pseq
        ratio = (2 * lcs(gseq, pseq) / (len(gseq) + len(pseq))) if (gseq or pseq) else 1.0
        seq_ratio.append(ratio)
        m["row_exact"] += gs == ps and g["status"] == p["status"]
        per_case[c] = {"status": g["status"] == p["status"], "exact": gs == ps, "seq": ratio, "gold": len(gs), "pred": len(ps)}
    n = len(cases)
    P, R, F = prf(m["tp"], m["fp"], m["fn"]); rP, rR, rF = prf(m["rtp"], m["rfp"], m["rfn"])
    return {
        "cases": n, "status_accuracy": m["status_ok"] / n,
        "step_precision": P, "step_recall": R, "step_f1": F,
        "relaxed_step_precision": rP, "relaxed_step_recall": rR, "relaxed_step_f1": rF,
        "field_accuracy": {f: field_ok[f] / field_n[f] if field_n[f] else 1.0 for f in FIELDS},
        "field_accuracy_overall": sum(field_ok.values()) / max(1, sum(field_n.values())),
        "sequence_exact": m["seq_exact"] / n, "sequence_lcs_ratio": sum(seq_ratio) / n,
        "row_exact_match": m["row_exact"] / n,
        "gold_steps": sum(len(gold[c]["steps"]) for c in cases), "pred_steps": sum(len(pred.get(c, {"steps": []})["steps"]) for c in cases),
        "per_case": per_case,
    }


def report(res: dict) -> str:
    pct = lambda x: f"{100 * x:6.2f}%"
    lines = [f"cases: {res['cases']}   gold steps: {res['gold_steps']}   predicted steps: {res['pred_steps']}",
             f"status accuracy        {pct(res['status_accuracy'])}",
             f"step P / R / F1        {pct(res['step_precision'])} / {pct(res['step_recall'])} / {pct(res['step_f1'])}",
             f"relaxed (no evidence)  {pct(res['relaxed_step_precision'])} / {pct(res['relaxed_step_recall'])} / {pct(res['relaxed_step_f1'])}",
             f"field accuracy         " + "  ".join(f"{f}={pct(v).strip()}" for f, v in res["field_accuracy"].items()),
             f"field accuracy (all)   {pct(res['field_accuracy_overall'])}",
             f"sequence exact / LCS   {pct(res['sequence_exact'])} / {pct(res['sequence_lcs_ratio'])}",
             f"row exact match        {pct(res['row_exact_match'])}"]
    return "\n".join(lines)


def diff(pred: dict, gold: dict, case: str) -> str:
    g, p = gold[case], pred.get(case, {"status": "?", "steps": []})
    out = [f"== {case}  gold={g['status']}  pred={p['status']}"]
    gset, pset = {full(s) for s in g["steps"]}, {full(s) for s in p["steps"]}
    for tag, steps, other in (("gold", g["steps"], pset), ("pred", p["steps"], gset)):
        for s in steps:
            mark = " " if full(s) in other else ("-" if tag == "gold" else "+")
            out.append(f" {mark} {tag} {s['step_no']:>2} {s['phase'][:4]} {s['operation']:<26} {s['system_id']:<20} {s['resource_id']:<18} {s['evidence_id']}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", type=Path, default=ROOT / "output_samples.csv")
    ap.add_argument("--gold", type=Path, default=ROOT / "dataset" / "sample_output.csv")
    ap.add_argument("--diff", nargs="*", help="case ids to show side by side; 'bad' shows every non-exact case")
    ap.add_argument("--json", type=Path, help="write the metrics as json")
    a = ap.parse_args()
    pred, gold = load(a.pred), load(a.gold)
    res = score(pred, gold)
    print(report(res))
    if a.diff:
        ids = [c for c, v in res["per_case"].items() if not v["exact"]] if a.diff == ["bad"] else a.diff
        for c in ids:
            print("\n" + diff(pred, gold, c))
    if a.json:
        a.json.write_text(json.dumps({k: v for k, v in res.items() if k != "per_case"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
