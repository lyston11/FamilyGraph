from __future__ import annotations
import ast, json, os, re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / 'backend'
OUT = Path(__file__).resolve().parents[1] / 'evidence'
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
 'begin_immediate':r'BEGIN\\s+IMMEDIATE|immediate\\s*=\\s*True|_immediate_tx',
 'raw_json_extract':r'json_extract\\s*\\(',
 'raw_sql':r'(?i)(?:text\\(|exec_driver_sql\\(|execute\\()',
 'sqlite_where':r'sqlite_where',
 'postgresql_where':r'postgresql_where',
 'sqlite_specific':r'(?i)(?:sqlite|pragma|fts5|autoincrement|without\\s+rowid)',
 'backup':r'(?i)(?:backup|pg_dump|restore|snapshot|export|import)',
}
report={
 'root':str(ROOT), 'branch':git(['git','branch','--show-current']),
 'commit':git(['git','rev-parse','HEAD']),
 'worktree':git(['git','rev-parse','--show-toplevel']),
 'counts':{'app_python':len(py),'model_python':len(models),'migration_python':len(migrations)},
 'matches':{k:grep(v, all_py if k not in ('sqlite_where','postgresql_where') else models) for k,v in raw_patterns.items()},
 'tables':[], 'indexes':[], 'constraints':[], 'migrations':[],
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
                report['constraints' if name in {'CheckConstraint','ForeignKeyConstraint','UniqueConstraint','PrimaryKeyConstraint'} else 'indexes' if name=='Index' else 'tables'].append({'kind':name,'path':rel,'line':node.lineno})
for p in migrations:
    report['migrations'].append({'path':str(p.relative_to(ROOT)),'lines':len(p.read_text(errors='replace').splitlines())})
for key in ('tables','indexes','constraints','migrations'):
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
