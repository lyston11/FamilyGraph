from __future__ import annotations
import ast, json, os, re, subprocess
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
BACKEND = ROOT / 'backend'
OUT = OUT_DIR
OUT.mkdir(parents=True, exist_ok=True)

def git(cmd):
    return subprocess.check_output(cmd, cwd=ROOT, text=True).strip()

def files(glob):
    return sorted(p for p in BACKEND.glob(glob) if p.is_file() and '__pycache__' not in str(p))

def grep(pattern, paths):
    out=[]
    rx=re.compile(pattern)
    for p in paths:
        text=p.read_text(errors='replace')
        for n,line in enumerate(text.splitlines(),1):
            if rx.search(line): out.append({'path':str(p.relative_to(ROOT)), 'line':n, 'text':line.strip()[:240]})
    return out

py=files('app/**/*.py')
models=files('app/models/**/*.py')
migrations=files('migrations/versions/*.py')
all_py=files('**/*.py')
raw_patterns={
 'begin_immediate': r"BEGIN\s+IMMEDIATE|immediate\s*=\s*True|_immediate_tx",
 'raw_json_extract': r"json_extract\s*\(",
 'raw_sql': r"(?i)(?:text\(|exec_driver_sql\(|execute\()",
 'sqlite_where': r"sqlite_where",
 'postgresql_where': r"postgresql_where",
 'sqlite_specific': r"(?i)(?:sqlite|pragma|fts5|autoincrement|without\s+rowid)",
 'backup': r"(?i)(?:backup|pg_dump|restore|snapshot|export|import)",
}
report={
 'root':str(ROOT), 'branch':git(['git','branch','--show-current']),
 'commit':git(['git','rev-parse','HEAD']),
 'worktree':git(['git','rev-parse','--show-toplevel']),
 'counts':{'app_python':len(py),'model_python':len(models),'migration_python':len(migrations)},
 'matches':{k:grep(v, all_py if k not in ('sqlite_where','postgresql_where') else models) for k,v in raw_patterns.items()},
 'tables':[], 'indexes':[], 'constraints':[], 'migrations':[], 'triggers':[], 'virtual_tables':[],
}
# AST inventory: model table names, Index/CheckConstraint/ForeignKey declarations.
for p in models:
    try: tree=ast.parse(p.read_text())
    except SyntaxError: continue
    rel=str(p.relative_to(ROOT))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name=node.func.id
            if name in {'Table','Index','CheckConstraint','ForeignKeyConstraint','UniqueConstraint','PrimaryKeyConstraint'}:
                bucket = ('constraints'
                          if name in {'CheckConstraint','ForeignKeyConstraint','UniqueConstraint','PrimaryKeyConstraint'}
                          else 'indexes' if name == 'Index' else 'tables')
                # PG-1 要求每个条目记录 owner/status/evidence；缺字段的条目无法验收。
                entry = {'id': f'SCHEMA-{name}-{rel}:{node.lineno}', 'kind': name,
                         'path': rel, 'line': node.lineno,
                         'owner': 'backend-model', 'status': 'inventory', 'evidence': 'L0'}
                report[bucket].append(entry)
for p in migrations:
    report['migrations'].append({'path':str(p.relative_to(ROOT)),'lines':len(p.read_text(errors='replace').splitlines())})
# 表清单：模型用 __tablename__ 而不是 Table(...)，必须单独扫描。
# 否则 gate-1-inventory 的 tables 恒为空，与 schema-inventory 的 88 表口径不一致。
for p in models:
    src = p.read_text(errors='replace')
    rel = str(p.relative_to(ROOT))
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for cls in [x for x in ast.walk(tree) if isinstance(x, ast.ClassDef)]:
        for n in cls.body:
            value = None
            if (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                    and n.target.id == '__tablename__'):
                value = n.value
            elif isinstance(n, ast.Assign) and any(
                    isinstance(z, ast.Name) and z.id == '__tablename__' for z in n.targets):
                value = n.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                report['tables'].append({
                    'id': f'SCHEMA-TABLE-{rel}:{cls.lineno}', 'kind': 'table',
                    'table': value.value, 'path': rel, 'line': cls.lineno,
                    'owner': 'backend-model', 'status': 'inventory', 'evidence': 'L0',
                })
# trigger / virtual table：SQLite 专属 DDL，PostgreSQL 上不存在
report['triggers'] = []
report['virtual_tables'] = []
for p in migrations + py:
    src = p.read_text(errors='replace')
    rel = str(p.relative_to(ROOT))
    for n, line in enumerate(src.splitlines(), 1):
        up = line.upper()
        if 'CREATE TRIGGER' in up:
            report['triggers'].append({'id': f'TRIGGER-{rel}:{n}', 'path': rel, 'line': n,
                                       'owner': 'backend-migration', 'status': 'inventory', 'evidence': 'L0',
                                       'count_basis': 'source-site',
                                       'note': 'SQLite/PostgreSQL 触发器语法与语义不同；本项是**源码位点**计数，'
                                               '循环展开后的实际对象数见 trigger-inventory.json（69）'})
        if 'VIRTUAL TABLE' in up:
            report['virtual_tables'].append({'id': f'VIRTUAL-{rel}:{n}', 'path': rel, 'line': n,
                                             'owner': 'backend-migration', 'status': 'blocked',
                                             'evidence': 'L2',
                                             'note': 'FTS5 虚拟表在 PostgreSQL 上不存在（已实测 SyntaxError）'})
for key in ('tables','indexes','constraints','migrations','triggers','virtual_tables'):
    report['counts'][key]=len(report[key])
(OUT/'gate-1-inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

md=['# Gate 0/1 Stable Inventory（自动生成）','',f'- root: `{report["root"]}`',f'- branch: `{report["branch"]}`',f'- commit: `{report["commit"]}`',f'- worktree: `{report["worktree"]}`','', '## 统计', '']
for k,v in report['counts'].items(): md.append(f'- `{k}`: **{v}**')
md += ['', '## 稳定 ID 规则', '', '- `SCHEMA-TABLE-<relative path>:<line>`', '- `SCHEMA-INDEX-<relative path>:<line>`', '- `SCHEMA-CONSTRAINT-<relative path>:<line>`', '- `SQL-<category>-<relative path>:<line>`', '- `MIGRATION-<filename>`', '', '## 证据约束', '', '此文件只记录 L0（源码结构扫描）证据；它不能证明 PostgreSQL 运行时语义。每个条目在后续 Gate 必须升级为 L2/L3，或明确标记为阻塞。', '', '## 分类命中摘要', '']
for k,v in report['matches'].items():
    md.append(f'### `{k}`：{len(v)} 条')
    for item in v[:12]: md.append(f'- `{item["path"]}:{item["line"]}` `{item["text"]}`')
    if len(v)>12: md.append(f'- ……其余 {len(v)-12} 条见 `gate-1-inventory.json`')
(OUT/'gate-1-inventory.md').write_text('\n'.join(md)+'\n')
print(json.dumps({'counts':report['counts'],'matches':{k:len(v) for k,v in report['matches'].items()}},ensure_ascii=False))
