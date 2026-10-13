# 剩余工作交接（2026-10-23 收尾）

本文件汇总 `10-03-multi-tenant-agent-concurrency-storage` 任务树下**未完成**的项、
各自为什么没做完、以及继续做需要什么。九个任务已按用户决定归档；本文是它们的交接记录。

## 一句话结论

**所有能在单机开发环境内验证的项都已完成并落盘。** 剩余项分三类，都不是「代码没写」，
而是**需要本机不具备的环境**或**必须由人决定时机**。

## 一、需要真实上游（无法在本机构造）

| 项 | 任务 | 为什么没做 |
|---|---|---|
| DERP 不可达 | provider-reliability | 需要真实 DERP 中继与可控断网 |
| TLS 握手失败 | provider-reliability | 已用真实 socket 覆盖 `ConnectError`/`RemoteProtocolError`，但真实 TLS 证书链失败需真实上游 |
| 真实 provider 的 stream reset / header timeout | provider-reliability | 已用本地真实 socket 覆盖；真实上游的中间设备行为不同 |
| 真实 provider 流量下的长流多租户 p95/p99 | provider-reliability | 需真实模型流量；探针数字不能冒充 |

**已有覆盖**：`test_provider_transport_faults_real_socket.py` 用真实本地 TCP 服务产生
连接被拒 / DNS 失败 / 响应头超时 / 响应头前 RST / 响应体中途 RST，
验证 httpx 的真实异常映射与 `sent` 语义。

## 二、需要多机 / 主备（单节点实例做不到）

| 项 | 任务 | 为什么没做 |
|---|---|---|
| HA failover / 主备切换 | postgres-operations | 专用验收实例是单节点，无主备 |
| 跨机网络分区 | redis-coordination | 需多主机与网络控制 |
| PG 重启的真实恢复曲线 | postgres-operations | 本轮用同步 `docker restart`（阻塞到就绪），报告 0.0s 不代表瞬时恢复；需 `docker kill` + 轮询 |
| 跨机多实例 lease/配额 | control-plane | 已在同机两真实进程验证；跨机未做 |
| Redis eviction | redis-coordination | 需内存压力配置 |

## 三、需要人决定时机（PRD 停止条件）

| 项 | 任务 | 为什么没做 |
|---|---|---|
| 线上发布与灰度切换 | postgres-operations / postgres-migration | AGENTS.md 明确由人执行 |
| 回滚演练（实机） | postgres-operations | 需改生产 `DATABASE_URL` 并重启服务 |
| PgBouncer 生产启用 | postgres-operations | 需生产决策 |

## 四、需要人工标注或产品决策

| 项 | 任务 | 为什么没做 |
|---|---|---|
| recall@k / precision@k 评测 | lexical-search | 需**人工标注**语料；现有 golden set 是合成/子串真源 |
| 中文分词在专业术语/人名上的调优 | lexical-search | 需真实术语集与调优目标 |
| embedding 满载 vs 空闲的对比 | pgvector-rag | 已造出待索引语料，但控制面探针采样窗口内 gate.active 仍为 0（维护 tick 每 5s 一批、单批 4 chunk），**未取得有效对比**。要做出该对比需调大单批或缩短 tick，属配置决策 |
| 两容器内存限额 | control-plane | 生产 `HostConfig.Memory=0`（无限）；「一个容器 OOM 会否拖垮另一个」未约束也未测，需部署决策 |
| limiter mutation 回归（control-plane reserve / 乘法 retry / visibility filter） | 父任务 | 已做 tenant quota（6 项失败）与 PG 行锁（2 项失败）；其余三种未做 |

## 五、已完成的验证（供继续时复用）

### 探针脚本（`scripts/migration-proof/`）

| 脚本 | 用途 | 需要的环境 |
|---|---|---|
| `two_process_acceptance.py` | 真实两进程容量竞争、kill -9 崩溃、并发归还 | 隔离 PG + baseline |
| `pool_exhaustion_probe.py` | 池耗尽/长事务/断线注入（**带端口白名单护栏**） | 专用实例（非共用 dev） |
| `restart_and_redis_probe.py` | PG 重启、Redis 中断注入（**带护栏**） | 专用实例 |
| `control_plane_latency_probe.py` | 控制面尾延迟与连接饱和 | 运行中的 API |
| `background_budget_probe.py` | 后台维护对控制面的影响 | 运行中的 API + embedding |
| `pgvector_index_comparison_probe.py` | IVFFlat/HNSW 召回与延迟、10 万级规模 | 带 pgvector 的隔离 PG |
| `pgvector_pipeline_probe.py` | 向量全链路（写入/撤权/过滤/RRF） | 带 pgvector 的隔离 PG |
| `seed_golden_embeddings.py` | 在隔离 PG 上用真实模型建 golden 索引 | 隔离 PG + embedding 服务 |
| `seed_pending_chunks.py` | 造待索引语料（产生 embedding 负载） | 隔离 PG + embedding 服务 |
| `pg_baseline_build.py` | 从 ORM 建 PG baseline（含扩展索引、pgvector 表、**门列**） | 带扩展的隔离 PG |

### 专用验收实例的搭建方式

```bash
# PG（pgvector + pgroonga）
docker run -d --name fg-acc-pg -e POSTGRES_PASSWORD=acc -e POSTGRES_DB=familygraph \
  -p 127.0.0.1:55460:5432 familygraph-postgres:pg16-pgvector-pgroonga
docker exec fg-acc-pg psql -U postgres -d familygraph \
  -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgroonga"
# Redis
docker run -d --name fg-acc-redis -p 127.0.0.1:56380:6379 redis:7-alpine --save "" --appendonly no
# baseline（注意 RAG_EMBEDDING_DIMENSION 必须设，否则 pgvector 表被跳过）
DATA_DIR=/tmp/fg-acc-data RAG_EMBEDDING_DIMENSION=512 \
  PGTEST_DSN="postgresql+psycopg://postgres:acc@127.0.0.1:55460/familygraph" \
  python3 scripts/migration-proof/pg_baseline_build.py
# 容量计数行（**必须在灌数据之后**跑，否则对账报缺失）
DATA_DIR=/tmp/fg-acc-data DATABASE_URL="postgresql+psycopg://postgres:acc@127.0.0.1:55460/familygraph" \
  RAG_ENABLED=1 RAG_EMBEDDING_DIMENSION=512 python -m app.capacity_bootstrap
# 第二个 API 进程（独立端口）
DATA_DIR=/tmp/fg-acc-data DATABASE_URL="postgresql+psycopg://postgres:acc@127.0.0.1:55460/familygraph" \
  REDIS_URL="redis://127.0.0.1:56380/0" PUBLIC_API_PORT=8010 INTERNAL_AGENT_API_PORT=8011 \
  ADMIN_API_PORT=8012 RAG_ENABLED=1 RAG_EMBEDDING_DIMENSION=512 python -m app.serve
```

**护栏**：`pool_exhaustion_probe.py` 与 `restart_and_redis_probe.py` 强制要求 PG 端口在
`ACC_ALLOWED_PORTS` 白名单内（默认只含 55460），并**显式拒绝** 55450（共用开发库）与
5432（生产）。继续做这类注入时必须保留该护栏。

## 六、本轮发现并修复的真实缺陷（全部已合入 main）

| 缺陷 | 影响 | 提交 |
|---|---|---|
| `redis` 未声明为运行时依赖 | 生产镜像 `ModuleNotFoundError`，且 `import` 在 try 外不会被降级捕获 | `beb5eacd` |
| 在途 SSE 流被误报为孤儿占用 | `/api/ready` 在有流在途时恒 503，编排器摘除健康实例 | `45c215b8` |
| 迁移的 boolean 列与整数比较 + `json_extract` 进 CHECK | 全新 PG 库无法从迁移建成；守卫只扫 `app/` 不扫 `migrations/` | `a1f83a4c` |
| `pg_baseline_build` 漏建 ORM 外的 4 个门列 | baseline 库维护循环每 tick 失败，日志脱敏后极难定位 | `2b6603f7` |
| 向量写入键与读取键分叉 | 向量检索静默返回零候选，RRF 让词法结果照常返回所以表面看不出 | `ce5f3f4c` |
| 上游 egress 读进程代理环境变量 | `HTTP_PROXY` 可静默改道上游流量，并让 `sent=false` 审计证据失真 | `53bb309c` |

## 七、事故记录

**误提交调试补丁到 origin/main**：排查「维护循环每 tick 失败」时，为取被脱敏掉的堆栈，
我在**远程 git 检出**上临时打了调试补丁；`familygraph-code-sync.timer` 每 30 分钟
`git add -A && push`，于是把该补丁推到了 `origin/main`（`2b6603f7`）。
该补丁会打印 1500 字符 traceback，而原代码**刻意**只记异常类名以防 DB 绑定参数泄漏。
处置：停 timer → 移除调试行 → 提交 `ca163867` → 重启 timer。

**教训**：不得把临时调试改动放到远程检出；临时验证应在 `/tmp` 隔离副本上做
（本轮其余探针都遵守了这一点，只有这一次例外）。
