from __future__ import annotations
import importlib.metadata, json, os, platform, shutil, subprocess
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

def cmd(*args):
    try: return subprocess.check_output(args,cwd=ROOT,text=True,stderr=subprocess.STDOUT,timeout=10).strip()
    except Exception as e: return f'UNAVAILABLE: {type(e).__name__}: {e}'
def ver(dist):
    try:return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:return 'not-installed'

m={
 'generated_at_utc':cmd('date','-u','+%Y-%m-%dT%H:%M:%SZ'),
 'host':platform.node(), 'platform':platform.platform(), 'python':platform.python_version(),
 'git':{'root':cmd('git','rev-parse','--show-toplevel'),'branch':cmd('git','branch','--show-current'),'commit':cmd('git','rev-parse','HEAD'),'status':cmd('git','status','--short')},
 'tools':{x:shutil.which(x) or 'not-found' for x in ('docker','psql','pg_dump','pg_restore','python3')},
 'tool_versions':{'docker':cmd('docker','--version'),'docker_server':cmd('docker','version','--format','{{.Server.Version}}')},
 'postgres_isolation':{
   'image':'postgres:16-alpine',
   'container':'fg-pg-gate1（一次性，探针结束即删除）',
   'port':'55435 -> 5432',
   'database':'familygraph（独立 database，非开发库）',
   'dsn_env_var':'PGTEST_DSN',
   'execution_host':'开发服务器（经 ssh lyston + docker exec）',
   'local_docker_daemon':'DOWN —— L2/L3 探针无法在本机复现，必须在 PG 主机执行',
 },
 'target_databases':{
   'development':'/home/ubuntu/projects/FamilyGraph/backend/data/db/app.db（systemd 栈，本任务不写入）',
   'production':'Docker Compose familygraph-prod（命名卷，本任务禁止接触）',
   'migration_target':'PostgreSQL（尚未切换，仍为 SQLite）',
 },
 'python_packages':{x:ver(x) for x in ('SQLAlchemy','alembic','psycopg','psycopg2-binary','pytest')},
 'environment_policy':{'production_touched':False,'development_writer_cutover':False,'live_sqlite_copied':False,'redis_source_of_truth':False},
 'isolation_requirements':{'postgres_database':'dedicated database/container only','data_dir':'temporary directory only','network':'no production listener or database'},
}
out=ROOT/'.trellis/tasks/10-05-migration-proof-gates/research/evidence/environment-manifest.json'
out.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(m,ensure_ascii=False))
