# 实施计划

## 规划与启动

- [x] 在隔离库核实：种子里**没有**「朱元璋–李贞」关系；真实存在的是 `朱元璋⇄朱佛女 direct_sibling` 与 `李贞⇄朱佛女 spouse`。
- [x] 核实「看不出关系」的两层原因：家庭卡成员行只有 `household_label`（不投影称谓）；且「李家」内 朱元璋 视角解析不出路径（`朱元璋→李贞 found=False`，因 sibling 不产生 `Relation`）。
- [x] 核实读取授权不依赖投影：删除成员行后 朱元璋 读「李家」立即 404；但**未重建的物化 PFV 仍含该成员**（故迁移需标 stale）。
- [x] 核实名册模式：10 对 household/lineage 中，只有「李家」存在 household-only 成员（朱元璋）；其余 9 对成员完全子集。
- [x] 核实称谓来源：`household_card_payload` 已调用 `current_view_payload`，其 `edges` 即 viewer 锚定的已授权称谓（实测 `{10: '妻子'}` 可直接用于成员）。
- [x] **评审门（已确认）**：用 朱佛女 替换 朱元璋，保持「admin + 帝室 member」2 人模式（用户判定原设计为种子缺陷，需补齐）。
- [x] 评审通过，进入隔离 worktree。
- [x] 用**生产副本 + 生产真实 env**（含 STEWARD_*，config 指纹参与 generation 有效性）复现：
  - 迁移前 李贞@李家 = `[('朱元璋','成员','妻子的兄弟')]`；
  - 迁移后成员立即变为 `朱佛女`，但称谓暂时为 `None`（投影被标 stale）；
  - 迁移对 `space_members` 的写入触发既有 `sri_space_members_structural_*` 触发器 →
    结构修订失效 → steward 重算 → **最终 `[('朱佛女','成员','妻子')]`**，`versions_match=True`；
  - 朱元璋@李家、@李氏家族 均为 404（AC4）；马皇后@马府 未受影响（`丈夫`）。

## 实现顺序

### A. 种子与迁移（R1/R2）

1. `backend/app/dev_seed.py`
   - `_SPACE_ROSTERS["李家"]`：`朱元璋` → `朱佛女`（推荐方案；若评审选只删不补，则删去该行）。
   - 模块 docstring L18 与 L233-235 注释按新语义重写（不得留下「朱元璋进李家」的残留说明）。
2. 新增 `backend/migrations/versions/0054_*.py`（`down_revision = "0053_member_approval_and_labels"`）
   - **upgrade**：
     - `DELETE` 收窄条件（空间名=李家、kind=household、用户=朱元璋、`status='active'`、`added_by = f.owner_id`）；
     - 幂等补建 朱佛女 在 李家的 active 成员行（`WHERE NOT EXISTS` 等价检查）；
     - 将「李家」的全部 `personal_family_views` 行标记 `status='stale'`（**不删行、不写计算内容**）。
   - **downgrade**：沿用 0052 模式——先履行父级 preflight（0053 结构迁移 + 0052 的 `_refuse_if_timing_evidence`，按 `iterate_revisions` **消费生成器**判断目的地），再执行；**数据修正刻意不逆向**。
3. `backend/tests/test_dev_seed.py`：更新名册断言（空间数、成员集合、admin 归属）。
4. 新增 `backend/tests/test_seed_household_removal_migration.py`（对照 0052 的迁移测试形态）：
   - 建旧库 → seed → upgrade → 断言该行消失、朱佛女行存在、其他空间与其他行不变、迁移无意外 DDL（纯数据 + 一次 `UPDATE`，`ACTUAL_ALEMBIC_DDL_COUNT` 断言）；
   - 幂等：重复 upgrade 不重复插入；
   - downgrade：跨 0053 时父级合同先于版本移动（含深层相对目标）。
5. `docs/DEV-DATA-SEEDING.md`：同步李家构成。

### B. 家庭卡关系称谓（R3/R4）

6. `backend/app/services/household_card.py`
   - 从已有 `payload["edges"]` 建 `target_user_id → term` 映射（主方向 `from_user_id == actor.id`，并防御性处理反向）；
   - 成员行新增 `relation_term`：有授权边取 term，否则 `null`；
   - 不新增 `load_graph` 调用、不新增授权判定、不改其他字段与 `allowed_actions`。
7. `backend/tests/test_household_card.py`
   - 成员 key 白名单加入 `relation_term`；
   - 新增用例：有路径成员带称谓（如 马皇后 视角 → 「丈夫」）、无路径成员为 `null`（space_member 孤立节点）。
8. `frontend/src/types/api.ts`：`HouseholdCardMember` 增可选 `relation_term?: string | null`。
9. `frontend/src/views/HouseholdCardView.vue`：在 `household_label` 旁按存在性渲染称谓（grid 与 list 两处）；`null` 时不渲染、不留占位。
10. `frontend/src/views/__tests__/household-card.spec.ts`：新增「显示称谓」与「无称谓不显示占位」断言。

### C. 连带说明修订

11. `frontend/src/composables/__tests__/{spaceSelection,useSpaceContext}.spec.ts`：把引用「朱元璋—李家」的注释改为合成场景表述（fixture 本身是合法的「我加入的、别人拥有的、排序靠前 household」场景，保留不删）。
12. **不改** `.trellis/spec/frontend/state-management.md` 的 09-20 走查记录（历史实况，按规范冻结）。

## 验证

- `cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app`
- `cd backend && .venv/bin/python -m pytest -q tests/test_dev_seed.py tests/test_household_card.py tests/test_seed_household_removal_migration.py tests/test_seed_lineage_boundary_migration.py`
- `cd backend && .venv/bin/python -m pytest -q tests/`（全量，本改动跨种子/服务/迁移）
- `cd frontend && npm run lint && npm run type-check && npm test && npm run build`
- `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`
- **隔离库端到端**（必做，勿在生产库跑 Python 写库脚本）：
  - 全新空库 `alembic upgrade head` + seed → 断言语义；
  - 从生产库 online backup 复制到隔离目录 → `upgrade head` → 断言 朱元璋 的该成员行消失、朱元璋 读「李氏家族」仍 404、李家卡成员与称谓正确。
- 生产部署（如需要）：备份 → 迁移 → 重启 → 实测（朱元璋 空间列表不再含李家；李贞 李家卡显示与 朱佛女 的关系）。

## 回滚点

- A 段：迁移是数据修正，`downgrade` 不逆向（与 0052 同约定）；如需恢复，从备份取回该行。
- B 段：`relation_term` 是纯新增只读字段，回退服务端与前端两处即回到原状，无数据影响。
