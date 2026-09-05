# 开发种子模块与空库自动播种机制 PRD

## 1. 目标与背景

当前 dev 部署的数据库积累大量测试残留（无空间归属的建档裸用户、E2E 测试账号、
联调空间），导致后台读模型看不到有效家庭数据，且旧 PIN 管理员行曾阻塞 09-04
迁移。用户决策：清空全部存量数据，并提供一套**按系统规则造数**的种子机制——
每次镜像更新后的全新环境自动拥有可登录的演示家庭与默认管理员。

## 2. 需求

### 2.1 一次性清库（运维）
- 清空 /data/db 全部业务数据（保留迁移框架，由启动流程重建 schema）；
- 清库前留一份带时间戳的备份到 /data/backups；
- 清库后重启走正常启动链：迁移 → admin bootstrap（默认 admin + 凭据文件）→ 种子。

### 2.2 种子模块（按系统规则造数）
- 数据形态与测试基建（backend/tests/conftest.py 的 create_user_with_pin /
  seed_space_with_owner / create_v1_relation / seed_structural_edge_to_fact）同构：
  正确的 PIN 哈希、账号状态（claimed）、成员角色（space_admin/active）、
  关系行与 confirmed SourceFact 映射；不使用裸 SQL 绕过模型约束；
- 演示数据集（一个家庭空间）：
  - 空间「王德海家」（household），王德海为 owner/space_admin；
  - 成员：王德海、周秀英、王建军、王小雨、王远山、王小虎（全部
    identity_confirmed、claimed，家庭端 PIN 统一 123456，pin_must_change=False）；
  - 关系（confirmed SourceFact，使家族树/家庭卡有内容）：王德海⇄周秀英 spouse；
    王远山→王德海 elder；王德海→王建军、王德海→王小雨 elder；
  - 系统管理员不由种子创建——它由既有 admin bootstrap 在空 system_admins 表上
    自动创建（维持 09-04 约定：第一个默认管理员 = admin + 0600 凭据文件）。

### 2.3 触发机制（用户选定：空库自动播种）
- 启动 preflight 中，在 admin bootstrap 之后执行 `maybe_seed_demo_data`；
- 双重门控：`DEV_SEED_DEMO_DATA=1`（默认缺失即关闭）且 `users` 表为空；
  任一不满足则跳过并记日志，绝不触碰已有数据；
- compose 的 api 服务透传该变量（默认 0），本机 .env 显式开启。

### 2.4 手动重置命令
- `python -m app.dev_seed --reset`：备份当前 db 文件到 /data/backups 后删除
  db/-wal/-shm，打印重启指引（容器重启后走迁移→bootstrap→播种）；
  不在活进程内原地重建 SQLite（避免服务进程持有已删除 inode）。

## 3. 红线

1. 生产安全：种子逻辑必须 env 门控默认关闭；误开时若库非空也只跳过；
2. 不绕过模型：全部经 SQLAlchemy 模型与既有 security 工具（hash_pin）造数；
3. 不动 admin bootstrap 合同（09-04）：种子不创建/修改 system_admins；
4. 日志不落 PIN 明文以外的敏感信息（PIN 本身是公开的 dev 演示值，允许日志）；
5. 测试与 lint 门禁全绿（backend quality guidelines）。

## 4. 验收标准

- [ ] 清库重启后：/data/bootstrap/admin-credentials 生成（admin 可登录后台）；
- [ ] 家庭端以 王德海/123456 可登录，家庭卡显示 6 名成员，家族树渲染关系；
- [ ] 后台概览出现「王德海家」空间与 6 名成员；
- [ ] `DEV_SEED_DEMO_DATA` 未设置（或=0）时启动不做任何种子；
- [ ] 库非空时启动跳过播种且数据零变动（有测试证明）；
- [ ] backend lint/type-check/pytest 门禁全绿。
