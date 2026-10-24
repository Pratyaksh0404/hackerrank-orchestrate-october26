"""Build a self-contained HTML dashboard from output.csv:   python code/dashboard.py  ->  dashboard.html"""
from __future__ import annotations

import csv
import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ghostaudit.dataio import DATASET, REPO_ROOT   # noqa: E402

OUT = REPO_ROOT / "dashboard.html"


def main() -> int:
    reqs = {r["case_id"]: r for r in csv.DictReader(open(DATASET / "deletion_requests.csv", encoding="utf-8"))}
    rows = list(csv.DictReader(open(REPO_ROOT / "output.csv", encoding="utf-8")))
    cases = []
    for r in rows:
        q = reqs[r["case_id"]]
        steps = json.loads(r["steps_json"])
        cases.append({"id": r["case_id"], "status": r["deletion_status"], "claimed": q["reported_status"],
                      "email": q["email"], "audit_at": q["audit_at"], "text": q["request_text"][:140], "steps": steps})
    status = Counter(c["status"] for c in cases)
    ghost = sum(1 for c in cases if c["claimed"] == "COMPLETE" and c["status"] == "INCOMPLETE")
    findings = Counter(s["operation"] for c in cases for s in c["steps"] if s["phase"] == "FINDING")
    systems = Counter(s["system_id"] for c in cases for s in c["steps"] if s["phase"] == "FINDING")
    data = json.dumps({"cases": cases, "status": status, "ghost": ghost, "findings": findings, "systems": systems})
    OUT.write_text(TEMPLATE.replace("__DATA__", data.replace("</", "<\\/")), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ghost Data Audit</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mute:#5d6673;--line:#e3e6eb;--ok:#1a7f4b;--bad:#c2410c;--na:#6b7280;--acc:#3b4cca}
@media(prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b23;--ink:#e8ebf0;--mute:#98a2b3;--line:#2a303b;--ok:#4ade80;--bad:#fb923c;--na:#9ca3af;--acc:#8b9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 16px 64px}h1{font-size:26px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.k{font-size:30px;font-weight:700;line-height:1.1}.l{color:var(--mute);font-size:13px}.ok{color:var(--ok)}.bad{color:var(--bad)}.na{color:var(--na)}
h2{font-size:17px;margin:28px 0 10px}.bar{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}.bar .n{width:190px;color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .t{flex:1;background:var(--line);border-radius:4px;height:10px}.bar .f{height:10px;border-radius:4px;background:var(--acc)}.bar .v{width:46px;text-align:right;font-variant-numeric:tabular-nums}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}input,select{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}input{flex:1;min-width:200px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
th{color:var(--mute);font-weight:600}tr.c{cursor:pointer}tr.c:hover{background:color-mix(in srgb,var(--acc) 6%,transparent)}.pill{padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid currentColor;white-space:nowrap}
.steps{font:12px ui-monospace,Menlo,monospace;color:var(--mute);margin:0;padding:0;list-style:none}.steps li{padding:2px 0}.PH{display:inline-block;width:88px}
@media(max-width:640px){.hide{display:none}}
</style></head><body><main>
<h1>Ghost Data Audit</h1><p class="sub">Northstar Shop deletion requests, reconciled against evidence at each case's audit time.</p>
<div class="grid" id="cards"></div>
<h2>What the audit found</h2><div class="grid"><div class="card"><div class="l" style="margin-bottom:6px">Findings by type</div><div id="f1"></div></div>
<div class="card"><div class="l" style="margin-bottom:6px">Systems still holding data</div><div id="f2"></div></div></div>
<h2>Cases</h2><div class="tools"><input id="q" placeholder="Search case id, email or request text"><select id="s"><option value="">All statuses</option><option>COMPLETE</option><option>INCOMPLETE</option><option>NOT_APPLICABLE</option></select>
<select id="g"><option value="">Any workflow claim</option><option value="ghost">Claimed COMPLETE, actually INCOMPLETE</option></select></div>
<table><thead><tr><th>Case</th><th>Audit status</th><th class="hide">Workflow claimed</th><th>Steps</th></tr></thead><tbody id="rows"></tbody></table>
</main><script>
const D=__DATA__;const $=s=>document.querySelector(s);
const cls={COMPLETE:'ok',INCOMPLETE:'bad',NOT_APPLICABLE:'na'};
const card=(k,l,c)=>`<div class="card"><div class="k ${c||''}">${k}</div><div class="l">${l}</div></div>`;
$('#cards').innerHTML=card(D.cases.length,'requests audited')+card(D.status.INCOMPLETE||0,'incomplete deletions','bad')+card(D.status.COMPLETE||0,'verified complete','ok')+card(D.status.NOT_APPLICABLE||0,'not deletion requests','na')+card(D.ghost,'claimed COMPLETE but data remains','bad');
function bars(el,obj){const e=Object.entries(obj).sort((a,b)=>b[1]-a[1]),m=Math.max(...e.map(x=>x[1]),1);$(el).innerHTML=e.map(([k,v])=>`<div class="bar"><span class="n">${k}</span><span class="t"><div class="f" style="width:${100*v/m}%"></div></span><span class="v">${v}</span></div>`).join('')}
bars('#f1',D.findings);bars('#f2',D.systems);
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function draw(){const q=$('#q').value.toLowerCase(),s=$('#s').value,g=$('#g').value;
$('#rows').innerHTML=D.cases.filter(c=>(!s||c.status===s)&&(!g||(c.claimed==='COMPLETE'&&c.status==='INCOMPLETE'))&&(!q||(c.id+c.email+c.text).toLowerCase().includes(q))).map(c=>
`<tr class="c" data-id="${c.id}"><td><b>${c.id}</b><div class="l">${esc(c.email)}</div></td><td><span class="pill ${cls[c.status]}">${c.status}</span></td><td class="hide">${c.claimed}</td><td>${c.steps.length}</td></tr>`).join('')}
$('#rows').onclick=e=>{const tr=e.target.closest('tr.c');if(!tr)return;const nx=tr.nextElementSibling;if(nx&&nx.classList.contains('d')){nx.remove();return}
const c=D.cases.find(x=>x.id===tr.dataset.id);const d=document.createElement('tr');d.className='d';
d.innerHTML=`<td colspan="4"><div class="l" style="margin-bottom:6px">${esc(c.text)}</div><ul class="steps">${c.steps.map(s=>`<li><span class="PH">${s.phase}</span> ${s.operation} · ${s.system_id} · ${s.resource_id}${s.evidence_id?' · '+s.evidence_id:''}</li>`).join('')||'<li>no steps</li>'}</ul></td>`;tr.after(d)};
['#q','#s','#g'].forEach(i=>$(i).oninput=draw);draw();
</script></body></html>"""

if __name__ == "__main__":
    sys.exit(main())
