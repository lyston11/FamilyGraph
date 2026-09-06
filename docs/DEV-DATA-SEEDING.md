# 开发/演示环境数据播种与默认管理员指南

> 适用范围：本地与 docker compose 开发部署。权威合同来源：`.trellis/spec/backend/`、
> `backend/app/services/admin_bootstrap.py`、`backend/app/dev_seed.py`（本文与其冲突时以代码为准）。
> 高层架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 1. 账号从哪来（三条路径，各司其职）

| 主体 | 创建者 | 触发条件 | 凭据交付 |
|---|---|---|---|
| 系统管理员 `admin` | admin bootstrap（09-04 合同，`app/services/admin_bootstrap.py`） | 启动 preflight 时 `system_admins` 表为空 | CSPRNG 强密码明文原子写入 `DATA_DIR/bootstrap/admin-credentials`（0600）；**首次成功改密后自动删除** |
| 家庭演示用户（王德海家） | dev 种子（`app/dev_seed.py`） | `DEV_SEED_DEMO_DATA=1`；启动时按**固定清单增量补缺**（只增不改不删） | 演示 PIN 统一 `123456`（公开 dev 值） |
| 真实家庭账号 | 建档/邀请/注册等业务流程 | 用户操作 | 业务流一次性交付（不归本文管） |

约定（务必理解后再动数据）：

1. **"第一个默认管理员"指 bootstrap 创建的 `admin`**，只在空 `system_admins` 表上生成；
   已有管理员（无论 active/disabled）的部署重启**绝不**生成第二账号——这是防意外的安全设计。
2. dev 种子**绝不创建/修改 system_admins**（bootstrap 专属职责）；env 未开启或
   本进程已播种过即跳过并零写入；开启时按固定清单 **insert-only 收敛**——缺什么
   补什么，任何已存在行绝不 UPDATE/DELETE，误开种子或清单外数据一律不动。
3. 忘记管理员密码走运维恢复（见 §4），不要手工改库。

## 2. 开关速查（docker-compose api 服务均已透传）

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `DEV_SEED_DEMO_DATA` | `0`（关） | `=1` 时启动按固定清单自动播种/补缺演示家庭（insert-only，只增不改不删）；生产保持缺省 |
| `PERSONAL_FAMILY_VIEW_ENABLED` | `""`（关） | `=1` 启用家庭卡/家族树投影端点；关闭时对应接口 503 |
| `DEV_ALLOW_WEAK_SECRETS` | `0`（关） | 仅本地裸跑放行弱 SECRET_KEY；compose 部署显式提供强密钥时无需它 |

本机开发在 `.env` 中设 `DEV_SEED_DEMO_DATA=1`、`PERSONAL_FAMILY_VIEW_ENABLED=1`。

## 3. 常用命令

```bash
# 一次性清库并重播种（备份当前库到 /data/backups/ 后删除 db/-wal/-shm）
docker compose exec api python -m app.dev_seed --reset
docker compose restart api          # 重启后：迁移 → admin bootstrap → 播种/补缺

# 忘记管理员密码：生成一次性恢复密码（写入 DATA_DIR/bootstrap/admin-recovery，0600）
docker compose exec api python -m app.dev_seed --help     # 种子 CLI 自身
docker compose exec api python -m app.admin_recovery --username admin

# 验证播种结果
docker compose exec api python -c "import sqlite3;c=sqlite3.connect('/data/db/app.db');\
print(c.execute('SELECT name FROM users ORDER BY id').fetchall())"
```

## 4. 存量部署升级 runbook：旧 PIN 管理员处置（2026-09-05 实战验证）

**背景**：09-04 之前的系统管理员是 PIN 体系。迁移 `0028_admin_password_credentials`
按设计 fail-closed——检测到任何旧 PIN 管理员行即拒启（**不做 PIN→密码静默转换**），
要求运维显式处置后才能升级。09-04 交付时缺这份 runbook，本文补齐（步骤在
`familygraph` dev 部署上端到端验证过）。

**处置步骤**（旧账号在新代码中本就无 PIN 登录通道，删除无能力损失；家庭用户数据
在别的表，零影响）：

```bash
# 1. 备份（api 停机后在数据卷上操作，保证 WAL 一致性）
docker compose stop api
docker run --rm -v familygraph_app_data:/data --entrypoint python familygraph-api:0.1.0 -c "
import sqlite3, datetime
ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
src = sqlite3.connect('/data/db/app.db'); dst = sqlite3.connect(f'/data/backups/pre-0028-{ts}.db')
src.backup(dst); dst.close(); src.close(); print('backup done')"

# 2. 显式删除旧管理员主体（三张表全部清空，迁移要求空表）
docker run --rm -v familygraph_app_data:/data --entrypoint python familygraph-api:0.1.0 -c "
import sqlite3
conn = sqlite3.connect('/data/db/app.db'); cur = conn.cursor()
for t in ('system_admin_refresh_sessions', 'system_admin_accounts', 'system_admins'):
    print(t, cur.execute(f'DELETE FROM {t}').rowcount, 'rows')
conn.commit()"

# 3. 重启：迁移 0028/0029 放行 → bootstrap 创建唯一 admin → 凭据文件生成
docker compose start api
docker exec familygraph-api-1 cat /data/bootstrap/admin-credentials   # 一次性密码
```

之后首次登录后台强制改密，凭据文件自动删除；若再遗忘走 §3 的 `admin_recovery`。

## 5. 演示数据集（王德海家 + 王氏家族 + 李国强家）

- 三空间：`王德海家`（household，家庭卡，6 人）+ `王氏家族`（lineage，家族树，
  11 人）+ `李国强家`（household，2 人）——王德海在三个空间均可见（前两者
  owner/space_admin；李国强家中为 member），李国强为第二位空间管理员，
  用于实测跨空间成员资格与多管理员场景；
- 成员（12 人，全部 claimed + identity_confirmed，PIN `123456`，含 solar 结构化
  出生日期）：王远山 1940、赵桂兰 1942（祖辈）、张伟 1961、王秀兰 1962、
  李国强 1963、王德海 1965、周秀英 1967（妻）、王建军 1990、刘婷婷 1992、
  王小雨 1993、王小虎 2018、王朵朵 2021（王小虎/王朵朵为**未成年人**——演示
  minor 保护 overlay）；
- 旁系亲属（赵桂兰/王秀兰/张伟/刘婷婷/王朵朵）只加入 `王氏家族`、不进
  `王德海家`——家庭卡（6 人）与家族树（11 人）的投影差异可对照验证；
- 关系以**全局** confirmed SourceFact 落库（space_id=NULL，所有空间共享投影；
  spouse ×4 + biological_parent ×10），家族树呈四代结构，称谓正确；
  注意：亲子成环检测是全局的，同一亲子事实不可按空间重复落行；
- `李国强家` 故意不含任何关系事实——可验证空图谱渲染路径；
- 披露默认：基础五类（头像/相册/生卒/简介/链接附件）全局开放（成员互见有资料）；
  高敏感五类（健康/住址/学校/联系方式/私人描述）保持关闭——09-05 起本人可开启
  （需二次确认，未成年人被 422 拒绝）；
- 形态契约与 `backend/tests/conftest.py` 造数助手保持同步（改动须两侧同步）；
- 数据卫生约定：E2E/联调不要向真实部署库写入管理员行或测试账号——历史上
  `e2e-liu-*` 残留行曾阻塞 0028 迁移并让 bootstrap 被跳过（见 §4）。

### 5.1 往固定清单加数据（增量补缺工作流）

播种语义是「固定清单 + 增量补缺（insert-only 收敛）」：`dev_seed.py` 里的名册 /
生辰 / 边常量就是唯一清单，每次启动与库内现状逐行比对，缺什么补什么，任何已
存在行绝不 UPDATE/DELETE。

往清单里加演示数据的步骤：

1. 编辑 `backend/app/dev_seed.py` 常量：新成员加入对应名册（`_SEED_HOUSEHOLD_MEMBERS`
   / `_SEED_LINEAGE_EXTRA_MEMBERS` / `_SEED_SECOND_SPACE_MEMBERS`）、在 `_SEED_BIRTHS`
   补结构化生辰（缺了建户时会 KeyError）、需要关系时往 `_SEED_EDGES` 加边；
2. 重启 api（`docker compose restart api`）——启动播种自动补齐缺失行，既有行
   （含运行期积累的 PIN 修改、披露调整、清单外账号）零改动；
3. 验证用 §3 的 sqlite 查询或直接登录（新成员 PIN 仍是演示值 `123456`）。

注意：

- 收敛匹配口径：用户按姓名（未删行取最旧）、空间按名、成员行按
  (space_id, user_id)、关系边按两人任一方向的 pending/active 边——同两人已有
  边（哪怕 dir_class 不同）视为已覆盖，跳过且不为既有边补建 fact（fact 只随
  关系新建一起落）；
- 只增不改不删：改生辰/性别等常量对已存在的行**不生效**（也不会报错），要彻底
  重置仍走 `--reset` 清库重播（会丢运行期状态）；
- 清单外数据（同名用户除外）永远不被纳入演示集；同名撞车时清单复用最旧的同名
  未删行，dev 库请避免用演示角色姓名建真实账号。
