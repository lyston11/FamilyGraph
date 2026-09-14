# Steward 证据版本与行为投影验证

## 环境与证据边界

- 用户于 2026-09-14 明确要求执行既有任务；启动基线 `0db88c2`，任务分支 `feat/09-13-steward-memory-evidence-projections`。
- 业务 worktree：`/Users/lyston/PycharmProjects/fg-09-13-steward-memory-evidence-projections`。仅共享 backend/.venv 依赖，`PYTHONPATH=.` 已验证导入本 worktree 的 app。
- pytest 使用真实 Alembic 迁移的独立临时 SQLite；模型仅 fake transport。未操作生产数据、模型服务或线上开关。
- 启动前主检出既有 52 个脏/未跟踪文件及 index 已记录哈希，原有工作不纳入本任务提交。渐进重算的共有服务/迁移在另一个 worktree 合并，独立测试可先准备，共有实现串行修改。

## MR-26 失败回归（修复前）

仅新增 `backend/tests/test_steward_behavior_rebuild.py`，真实调用 SourceFact、PFV、推荐 dismiss、推荐读取和行为重建。没有 mock 冷却生产者或通过直接改生产数据库构造结论。

工作目录为本任务 backend：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_behavior_rebuild.py --tb=short
.venv/bin/ruff check tests/test_steward_behavior_rebuild.py
.venv/bin/ruff format --check tests/test_steward_behavior_rebuild.py
```

初轮结果：4 failed / 4 passed，1.45 秒。失败均符合原缺陷：

- enabled/account 与 enabled/space：真实推荐冷却被删除，已忽略推荐重新出现。
- account/space 无匹配重放事件：其他键族被删除。
- disabled 两组合、有效事件账户/空间过滤与两次回放两组合通过。
- Ruff lint/format 通过，无夹具或迁移错误。后续补入大小写近似键反例，防止修复误用 SQLite 不区分大小写的 LIKE。

此处是修复前的失败证据，不能作为 MR-26 验收通过。

## 验收映射

| AC | 必须取得的证据 | 当前状态 |
| --- | --- | --- |
| SP-AC1 | 四组合、无事件、未知/近似键、其他账户/空间保全，所属语义幂等 | 12 项相关测试与独立复核通过 |
| SP-AC2 | 真实多作业相关证据换版，无关/无新事实和批次重试不重复 | 待实现 |
| SP-AC3 | 当前空间授权、父母节点、撤销、原 revision、租约、unsupported 降级 | 已冻结合同，待实现 |
| SP-AC4 | 唯一约束/并发、来源与旧确认历史、原地无损迁移与拒绝破坏性降级 | 待实现 |
| SP-AC5 | 私人/共享驳回保留；新/旧/反向候选均无新增关系待办和推测边 | 已冻结合同，待实现 |
| SP-AC6 | 最新迁移链、受影响服务及完整后端质量检查 | 待修复后运行 |

MR-23 新证书的 `projected` 仅表示记录时点核验通过，不声明持续有效或正式亲属事实。首版没有个人证书 API，不以内部空间级范围替代查看者授权；旧 candidate 的首个 job CASCADE 仍是既有删除边界。

## 串行依赖解除与实施基线

2026-09-14 按用户要求等待渐进重算完成集成。`dee91a1` 已进入 main，相关 worktree 当时无未提交代码；本任务随后先同步 origin/main，再 fast-forward 到本地已集成的 main。业务代码实施基线为 `dee91a14e85ca69d4c39044c1da359ea46700612`，单一迁移 head 为 `0048_steward_terminology_publication`。再次确认 `PYTHONPATH=.` 导入本任务 worktree 的 app，两个任务 manifest 各 5 条均通过 validate。

既有任务文件先于 Context Curation adoption marker 创建，保留原审计位置与 manifest；新增合同按现行叶文档规则组织。SourceFact 没有 TTL，SP-AC3 的过期条件已明确为批次/交付租约过期，事实自身核验 state、原 revision 与作用域。

## MR-26 修复后验收

实施与独立检查均在任务 worktree 进行。修复限定删除谓词和重放前缀定义，原真实推荐夹具在集成后的 progressive 基线正常，无需 mock 或弱化断言。

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_behavior_rebuild.py tests/test_family_recommendations.py tests/test_steward.py::test_projection_roundtrip_and_whitelist tests/test_steward.py::test_kind_cooldown_roundtrip_and_blocks_card --tb=short
.venv/bin/ruff check app/services/steward.py tests/test_steward_behavior_rebuild.py
.venv/bin/ruff format --check app/services/steward.py tests/test_steward_behavior_rebuild.py
PYTHONPATH=. .venv/bin/python -m mypy app
git diff --check
```

结果：12 passed，1.86 秒；Ruff lint/format、mypy 204 source files、diff 检查均通过。独立检查再次确认两个删除分支及 caller transaction、真实生产/忽略/读取链、外部键 ID/值/更新时间和跨作用域保全；未发现问题，未改代码，静态检查通过。没有新代码变化或疑点，因此独立复核未重复 pytest。全套后端留待 MR-23 完成后统一验证。
