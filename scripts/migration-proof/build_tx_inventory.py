from __future__ import annotations
import ast,json
from pathlib import Path

# 证据输出目录：可用 MIGRATION_PROOF_OUT 覆盖（任务归档后指向持久位置）。
import os as _os
OUT_DIR = Path(_os.environ.get(
    "MIGRATION_PROOF_OUT",
    str(Path(__file__).resolve().parents[2]
        / ".trellis/tasks/10-05-migration-proof-gates/research/evidence"),
))
def _repo_root() -> Path:
    """解析仓库根目录。

    不要用 `Path(__file__).parents[N]`：任务 worktree 下 tools/ 位于
    `.trellis/tasks/<task>/research/tools/`（工具当时的存放位置），深度变化时 N 会静默指错目录，
    扫描结果为空却不报错（本文件初版即因此产出空 inventory）。
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, cwd=Path(__file__).parent
        ).strip()
        if out:
            return Path(out)
    except Exception:
        pass
    return Path(__file__).resolve().parents[2]


ROOT = _repo_root()
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
( OUT_DIR/'tx-inventory.json').write_text(json.dumps({'count':len(out),'items':out},ensure_ascii=False,indent=2)+'\n')
(OUT_DIR/'tx-inventory.md').write_text('# Transaction stable inventory（自动生成）\n\n'+f'- 真实候选调用点：**{len(out)}**\n- 当前分类：全部 `UNCLASSIFIED`，禁止将候选数量当作已完成语义分析。\n- 下一步：为每个条目补 Contract ID、保护不变量、锁参与者、锁序、CAS/counter/retry 分类和 L2/L3 证据。\n')
print(len(out))
