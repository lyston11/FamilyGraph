from __future__ import annotations
import importlib.metadata, json, os, platform, shutil, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]

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
 'python_packages':{x:ver(x) for x in ('SQLAlchemy','alembic','psycopg','psycopg2-binary','pytest')},
 'environment_policy':{'production_touched':False,'development_writer_cutover':False,'live_sqlite_copied':False,'redis_source_of_truth':False},
 'isolation_requirements':{'postgres_database':'dedicated database/container only','data_dir':'temporary directory only','network':'no production listener or database'},
}
out=ROOT/'.trellis/tasks/10-05-migration-proof-gates/research/evidence/environment-manifest.json'
out.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(m,ensure_ascii=False))
