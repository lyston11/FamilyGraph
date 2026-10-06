from __future__ import annotations
import os, threading, time
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("PGTEST_DSN")
if not DSN:
    print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
    raise SystemExit(2)
DDL='''
DROP TABLE IF EXISTS proof_events, proof_attempts, proof_runs, proof_counters;
CREATE TABLE proof_counters(scope text PRIMARY KEY, capacity integer NOT NULL, active integer NOT NULL DEFAULT 0, version integer NOT NULL DEFAULT 0);
CREATE TABLE proof_runs(id bigint PRIMARY KEY, status text NOT NULL, version integer NOT NULL DEFAULT 0);
CREATE TABLE proof_attempts(id bigint PRIMARY KEY, run_id bigint NOT NULL REFERENCES proof_runs(id), tenant text NOT NULL, status text NOT NULL, lease_owner text, lease_generation integer NOT NULL DEFAULT 0);
CREATE TABLE proof_events(run_id bigint NOT NULL, seq integer NOT NULL, body text NOT NULL, PRIMARY KEY(run_id,seq));
'''

def connect(): return psycopg.connect(DSN,row_factory=dict_row)

def setup():
 with connect() as c:
  c.execute(DDL)
  c.execute("INSERT INTO proof_counters VALUES ('global',2,0,0),('tenant:a',2,0,0),('tenant:b',2,0,0)")
  c.execute("INSERT INTO proof_runs VALUES (1,'queued',0),(2,'queued',0),(3,'queued',0),(4,'queued',0),(5,'queued',0),(6,'queued',0)")
  for i in range(1,7): c.execute("INSERT INTO proof_attempts VALUES (%s,%s,%s,'reserved',NULL,0)",(i,i,'a' if i<=3 else 'b'))
  c.commit()

def lease(tenant, owner):
 with connect() as c:
  with c.transaction():
   # fixed lock order: global then tenant; counter is source of capacity truth.
   c.execute("SELECT 1 FROM proof_counters WHERE scope='global' FOR UPDATE")
   with c.cursor() as cur:
    cur.execute("SELECT active,capacity FROM proof_counters WHERE scope=%s FOR UPDATE",(f'tenant:{tenant}',))
    cap=cur.fetchone()
   if cap['active']>=cap['capacity']: return None
   with c.cursor() as cur:
    cur.execute("SELECT id FROM proof_attempts WHERE tenant=%s AND status='reserved' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1",(tenant,))
    row=cur.fetchone()
   if not row:return None
   c.execute("UPDATE proof_counters SET active=active+1,version=version+1 WHERE scope IN ('global',%s)",(f'tenant:{tenant}',))
   with c.cursor() as cur:
    cur.execute("UPDATE proof_attempts SET status='in_flight',lease_owner=%s,lease_generation=lease_generation+1 WHERE id=%s RETURNING id,lease_generation",(owner,row['id']))
    return cur.fetchone()

def settle(attempt_id, owner):
 with connect() as c:
  with c.transaction():
   with c.cursor() as cur:
    cur.execute("UPDATE proof_attempts SET status='succeeded' WHERE id=%s AND status='in_flight' AND lease_owner=%s RETURNING tenant",(attempt_id,owner))
    row=cur.fetchone()
   if not row:return False
   c.execute("UPDATE proof_counters SET active=active-1,version=version+1 WHERE scope IN ('global',%s) AND active>0",(f'tenant:{row["tenant"]}',))
   return True

def append_event(run,seq,body):
 with connect() as c:
  with c.transaction():
   with c.cursor() as cur:
    cur.execute("INSERT INTO proof_events VALUES (%s,%s,%s) ON CONFLICT (run_id,seq) DO NOTHING RETURNING seq",(run,seq,body))
    return cur.fetchone() is not None

def main():
 setup(); results=[]; lock=threading.Lock(); barrier=threading.Barrier(8)
 def w(i):
  barrier.wait(); row=lease('a' if i<4 else 'b',f'w{i}')
  # 记录 (线程序号, 结果)：线程完成顺序不确定，不能用 enumerate 下标当 owner。
  with lock: results.append((i,row))
 ts=[threading.Thread(target=w,args=(i,)) for i in range(8)]
 for t in ts:t.start()
 for t in ts:t.join(10)
 assert not any(t.is_alive() for t in ts), 'worker hung'
 assert len([x for x in results if x[1]])==4, results
 with connect() as c:
  counts=c.execute("SELECT tenant,count(*) n FROM proof_attempts WHERE status='in_flight' GROUP BY tenant ORDER BY tenant").fetchall()
 assert all(x['n']<=2 for x in counts), counts
 assert append_event(1,1,'x') is True and append_event(1,1,'x') is False
 leases=[(i,r) for i,r in results if r]
 for i,r in leases: assert settle(r['id'],f'w{i}') is True, (i,r)
 assert settle(leases[0][1]['id'],f'w{leases[0][0]}') is False
 with connect() as c:
  row=c.execute("SELECT active FROM proof_counters WHERE scope='global'").fetchone(); assert row['active']==0,row
 print({'lease_results':len([x for x in results if x[1]]),'counts':counts,'event_duplicate':True,'settle_idempotent':True,'counter_active':0})

if __name__=='__main__':main()
