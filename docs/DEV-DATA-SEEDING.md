# 开发/演示环境数据播种与默认管理员指南

> 适用范围：本地与 Docker Compose 开发部署。管理员 bootstrap 和演示种子的实际行为以
> `backend/app/services/admin_bootstrap.py`、`backend/app/dev_seed.py` 及其测试为准；
> 本文只在播种、重置、管理员初始化或存量升级时按需阅读；相关任务和规范见 `.trellis/`。
> 高层架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 1. 账号从哪来（三条路径，各司其职）

| 主体 | 创建者 | 触发条件 | 凭据交付 |
|---|---|---|---|
| 系统管理员 `admin` | admin bootstrap（09-04 合同，`app/services/admin_bootstrap.py`） | 启动 preflight 时 `system_admins` 表为空 | CSPRNG 强密码明文原子写入 `DATA_DIR/bootstrap/admin-credentials`（0600）；**首次成功改密后自动删除** |
| 家庭演示用户（明皇室等） | dev 种子（`app/dev_seed.py`） | `DEV_SEED_DEMO_DATA=1`；启动时按**固定清单增量补缺**（只增不改不删） | 演示 PIN 统一 `123456`（公开 dev 值） |
| 真实家庭账号 | 建档/邀请/注册等业务流程 | 用户操作 | 业务流一次性交付（不归本文管） |

执行播种或重置前确认以下不变量：

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
# 一次性清库并重播种（破坏性操作；先确认目标为开发库，脚本会备份后删除 db/-wal/-shm）
docker compose exec api python -m app.dev_seed --reset
docker compose restart api          # 重启后：迁移 → admin bootstrap → 播种/补缺

# 忘记管理员密码：生成一次性恢复密码（写入 DATA_DIR/bootstrap/admin-recovery，0600）
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

## 5. 演示数据集（明皇室 + 朱氏皇族 + 马府/马氏家族 + 徐达家/徐氏家族）

- 六空间（三对 household+lineage；household=家庭卡投影、lineage=家族树投影，
  新建 household 显式配对所属 lineage）：`明皇室`（6 人）+ `朱氏皇族`（30 人），
  owner 朱元璋；`马府`（2 人）+ `马氏家族`（4 人），owner 马皇后；`徐达家`（2 人）
  + `徐氏家族`（6 人），owner 徐达——三管理员、跨空间成员资格与「当前家族空间」
  切换场景都可实测；
- 成员（36 人，全部 claimed + identity_confirmed，PIN `123456`，含 solar 结构化
  出生日期；生卒为演示用近似换算，明代纪年各源存在差异）：明皇室核心家庭卡 6 人
  ——朱世珍 1283、朱元璋 1328、马皇后 1332、朱标 1355、朱棣 1360、朱允炆 1377；
  朱氏皇族再含六世帝系与姻亲（陈氏、朱兴隆、朱文正、朱樉、朱棡、朱橚、宁国公主、
  安庆公主、常氏、吕氏、徐皇后、梅殷、欧阳伦、朱允熥、朱高炽、朱高煦、朱高燧、
  张皇后、朱瞻基、朱瞻墡、孙皇后、朱祁镇、朱祁钰、钱皇后）；马氏家族为马皇后本家
  （马公、郑氏）；徐氏家族为徐皇后本家（徐达、谢氏、徐辉祖、徐增寿）。
  全员历史人物、无未成年人——minor 保护 overlay 改由测试夹具覆盖；
- 旁系与姻亲（陈氏/朱兴隆/朱文正/常氏/吕氏/梅殷/欧阳伦/朱允熥/朱高煦/朱高燧/
  张皇后/朱瞻墡/孙皇后/朱祁钰/钱皇后 等）只进 `朱氏皇族`、不进 `明皇室`——
  家庭卡（6 人）与家族树（30 人）的投影差异可对照验证；
- 跨空间成员资格：朱元璋以 member 身份进 `马府`/`马氏家族`（马皇后本家），
  朱棣以 member 身份进 `徐达家`/`徐氏家族`（徐皇后本家）；
- 关系以**全局** confirmed SourceFact 落库（space_id=NULL，所有空间共享投影；
  spouse ×11 + biological_parent ×44），家族树呈六世结构（朱世珍→朱元璋→朱标→
  朱高炽→朱瞻基→朱祁镇），称谓正确；注意：亲子成环检测是全局的，同一亲子事实
  不可按空间重复落行；
- `徐达家` 名册刻意只放 徐达+朱棣（翁婿二人无直接事实）——可验证空图谱渲染
  路径；`马府` 二人恰有全局夫妻事实——可验证小空间投影渲染；
- 披露默认：基础五类（头像/相册/生卒/简介/链接附件）全局开放（成员互见有资料）；
  高敏感五类（健康/住址/学校/联系方式/私人描述）保持关闭——09-05 起本人可开启
  （需二次确认，未成年人被 422 拒绝）；
- 形态契约与 `backend/tests/conftest.py` 造数助手保持同步（改动须两侧同步）；
- 数据卫生约定：E2E/联调不要向真实部署库写入管理员行或测试账号——历史上
  `e2e-liu-*` 残留行曾阻塞 0028 迁移并让 bootstrap 被跳过（见 §4）。

### 5.1 往固定清单加数据（增量补缺工作流）

播种语义是「固定清单 + 增量补缺（insert-only 收敛）」：`dev_seed.py` 里的
空间配对（`_SEED_SPACE_PAIRS`）/ 各空间名册（`_SEED_SPACE_MEMBERS`）/ 生辰
（`_SEED_BIRTHS`）/ 边（`_SEED_EDGES`）常量就是唯一清单，每次启动与库内现状
逐行比对，缺什么补什么，任何已存在行绝不 UPDATE/DELETE。

往清单里加演示数据的步骤：

1. 编辑 `backend/app/dev_seed.py` 常量：新成员加入 `_SEED_SPACE_MEMBERS` 对应
   空间名册（同一用户可进多个空间）、在 `_SEED_BIRTHS` 补结构化生辰（缺了建户
   时会 KeyError）、需要关系时往 `_SEED_EDGES` 加边；
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
