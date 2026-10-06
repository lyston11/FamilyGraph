from __future__ import annotations
import ast,json,re
from pathlib import Path
def _repo_root() -> Path:
    """解析仓库根目录。

    不要用 `Path(__file__).parents[N]`：任务 worktree 下 tools/ 位于
    `.trellis/tasks/<task>/research/tools/`，深度变化时 N 会静默指错目录，
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
    return Path(__file__).resolve().parents[5]


ROOT = _repo_root()
import os as _os

# 证据输出目录：可用 MIGRATION_PROOF_OUT 覆盖（任务归档后指向持久位置）。
OUT_DIR = Path(_os.environ.get(
    "MIGRATION_PROOF_OUT",
    str(Path(__file__).resolve().parents[2]
        / ".trellis/tasks/10-05-migration-proof-gates/research/evidence"),
))
MODEL_ROOT=ROOT/'backend/app/models'
OUT = OUT_DIR
items=[]

def val(node):
 if isinstance(node,ast.Constant): return node.value
 if isinstance(node,ast.Name): return node.id
 if isinstance(node,ast.Attribute): return node.attr
 return ast.unparse(node) if hasattr(ast,'unparse') else '<expr>'

def call_name(node):
 if isinstance(node,ast.Name):return node.id
 if isinstance(node,ast.Attribute):return node.attr
 return ''
for p in sorted(MODEL_ROOT.rglob('*.py')):
 if '__pycache__' in str(p):continue
 try:t=ast.parse(p.read_text(errors='replace'))
 except SyntaxError:continue
 rel=str(p.relative_to(ROOT))
 for cls in [x for x in ast.walk(t) if isinstance(x,ast.ClassDef)]:
  table=None
  for n in cls.body:
   if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='__tablename__': table=val(n.value)
   if isinstance(n,ast.Assign) and any(isinstance(z,ast.Name) and z.id=='__tablename__' for z in n.targets): table=val(n.value)
  if table:
   items.append({'id':f'SCHEMA-TABLE-{rel}:{cls.lineno}','kind':'table','table':table,'path':rel,'line':cls.lineno,'owner':'backend-model','status':'inventory','evidence':'L0'})
   for n in cls.body:
    target=None; ann=None; value=None
    if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name): target=n.target.id; ann=ast.unparse(n.annotation)
    elif isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name): target=n.targets[0].id
    if target and target!='__tablename__':
     if isinstance(n,(ast.AnnAssign,ast.Assign)): value=n.value
     name=call_name(value.func) if isinstance(value,ast.Call) else ''
     if name in {'mapped_column','Column','relationship','AssociationProxy'} or (ann and ('Mapped[' in ann or 'list[' in ann)):
      items.append({'id':f'SCHEMA-COLUMN-{rel}:{getattr(n,"lineno",cls.lineno)}','kind':'column_or_relation','table':table,'name':target,'annotation':ann,'constructor':name,'path':rel,'line':getattr(n,'lineno',cls.lineno),'owner':'backend-model','status':'inventory','evidence':'L0'})
for p in sorted((ROOT/'backend/migrations/versions').glob('*.py')):
 items.append({'id':f'MIGRATION-{p.name}','kind':'migration','path':str(p.relative_to(ROOT)),'line':1,'owner':'backend-migration','status':'inventory','evidence':'L0'})
summary={}
for x in items:summary[x['kind']]=summary.get(x['kind'],0)+1
(OUT/'schema-inventory.json').write_text(json.dumps({'summary':summary,'items':items},ensure_ascii=False,indent=2)+'\n')
(OUT/'schema-inventory.md').write_text('# Schema stable inventory（自动生成）\n\n'+ '\n'.join(f'- `{k}`: **{v}**' for k,v in sorted(summary.items()))+'\n\n所有条目当前仅为 L0 源码结构证据；运行时约束、方言 render 和迁移往返必须在后续 Gate 升级。\n')
print(json.dumps(summary,ensure_ascii=False))
