# 开发种子模块与空库自动播种机制 Technical Design

## 1. 模块落点与结构

- 新文件 `backend/app/dev_seed.py`：
  - `maybe_seed_demo_data(session) -> bool`：门控判断 + 播种入口；
  - `_seed_demo_family(session)`：演示数据集实现；
  - `reset_database()`：`--reset` CLI 路径（备份 + 删 db/-wal/-shm + 重启指引）；
  - `__main__` 入口：`python -m app.dev_seed [--reset]`；
  - 模块级 `_SEED_DONE` 防重入（与 admin_bootstrap._BOOTSTRAP_DONE 同款语义）。
- 启动挂点：`app/main.py` lifespan 中 `run_startup_preflight` 内、
  `_bootstrap_admin_if_needed` 之后调用（保持"先管理员后演示数据"次序）。

## 2. 门控逻辑

```python
if config.DEV_SEED_DEMO_DATA != "1": skip   # 新配置项，默认 "0"
if users COUNT > 0: skip（记日志，绝不变更数据）
if _SEED_DONE: skip
```
config.py 新增 `DEV_SEED_DEMO_DATA: str = os.environ.get("DEV_SEED_DEMO_DATA", "0")`。

## 3. 演示数据集实现（与 conftest 同构，禁止裸 SQL）

按依赖顺序（单事务/逐段提交参照 conftest 造数函数）：
1. 用户+账号：复用 create_user_with_pin 的模型形态（User+Account,
   hash_pin("123456"), status=claimed, identity_confirmed），六名成员；
2. 空间+成员行：seed_space_with_owner 形态（FamilySpace + SpaceMember
   space_admin/active；其余成员 role="member"/active）；
3. v1 关系行：create_v1_relation 形态（spouse/elder）；
4. 事实映射：seed_structural_edge_to_fact 同款（Relation → confirmed SourceFact，
   带 space_id），使 PersonalFamilyView 投影与家庭卡可渲染。

实现方式建议：conftest 的助手函数是测试件不宜直接 import；将同构逻辑内聚到
dev_seed.py（可引用相同模型与 security.timeutil 工具），注释标注"形态契约与
tests/conftest.py 保持同步"。

## 4. reset 与部署接线

- `--reset`：shutil.copy db→/data/backups/pre-reset-<ts>.db → 删除
  app.db/-wal/-shm → stdout 打印「docker compose restart api」指引；进程退出码 0；
  不 unlink uploads/backups/bootstrap；
- docker-compose.yml api environment 增加一行：
  `DEV_SEED_DEMO_DATA: ${DEV_SEED_DEMO_DATA:-0}`；
- 本机 .env 追加 `DEV_SEED_DEMO_DATA=1`（部署阶段由主会话执行）。

## 5. 测试

`backend/tests/test_dev_seed.py`：
1. env 未开启 → maybe_seed_demo_data 返回 False 且零写入；
2. env=1 + 空库 → 创建 6 用户/1 空间/成员行/关系/confirmed facts，PIN 校验通过
   （security.verify_pin("123456")），且重复调用（_SEED_DONE 或非空库）幂等跳过；
3. env=1 + 非空库（预置一个用户）→ 跳过且该用户数据零变动；
4. 系统管理员表零写入断言（种子不碰 system_admins）。

## 6. 红线自查

- 只在 main.py/dev_seed.py/config.py/docker-compose.yml/backend tests 落笔；
- 不改 admin_bootstrap.py 合同；日志不含新增敏感字段；
- 遵循 database-guidelines（WAL/事务）与 logging-guidelines（结构化日志）。
