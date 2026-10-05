from __future__ import annotations
import ast,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
items=[]
for p in sorted((ROOT/'backend/app').rglob('*.py')):
 if '__pycache__' in str(p):continue
 try:t=ast.parse(p.read_text(errors='replace'))
 except SyntaxError:continue
 parents=[]
 for n in ast.walk(t):
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
   for child in ast.walk(n):
    if isinstance(child,ast.Call):
     text=ast.unparse(child)
     hit=('immediate=True' in text or '_immediate_tx' in text or 'BEGIN IMMEDIATE' in text)
     if hit:
      items.append({'id':f'TX-{p.relative_to(ROOT)}:{child.lineno}','path':str(p.relative_to(ROOT)),'line':child.lineno,'function':n.name,'expression':text[:500],'classification':'UNCLASSIFIED','evidence':'L0','status':'needs-contract-card','owner':'backend-transaction'})
# dedup same AST nested function walk
seen=set(); out=[]
for x in items:
 if x['id'] not in seen:seen.add(x['id']);out.append(x)
( ROOT/'.trellis/tasks/10-05-migration-proof-gates/research/evidence/tx-inventory.json').write_text(json.dumps({'count':len(out),'items':out},ensure_ascii=False,indent=2)+'\n')
(ROOT/'.trellis/tasks/10-05-migration-proof-gates/research/evidence/tx-inventory.md').write_text('# Transaction stable inventory（自动生成）\n\n'+f'- 真实候选调用点：**{len(out)}**\n- 当前分类：全部 `UNCLASSIFIED`，禁止将候选数量当作已完成语义分析。\n- 下一步：为每个条目补 Contract ID、保护不变量、锁参与者、锁序、CAS/counter/retry 分类和 L2/L3 证据。\n')
print(len(out))
