# 实施计划：管家自动修复称谓

顺序执行；命令在 backend/ 内运行（本任务预期前端零改动）。

## 1. 词典枚举与补全

- [ ] `backend/scripts/gen_term_pack.py`：枚举 ≤3 跳全码 + 直系 4 跳高频码，
  生成候选清单（码 + 规范称谓 + 一码多码冲突报告）。
- [ ] 人工校对清单后落 `models/term_registry.py::BUILTIN_ZH_CN_TERMS /
  BUILTIN_SYSTEM_TERMS`。
- [ ] 新 Alembic 迁移写种子（幂等，只补缺失行）；隔离库 `alembic upgrade head`
  + downgrade 往返验证。
- [ ] 测试：码表全集逐码断言词条存在（防手抄缺漏）；截屏黄金用例回归
  （viewer 1953：Um-Df→妹妹、Dm-Dm→孙子、Dm-Df→孙女、Um-Df-Sm→妹夫）。

## 2. 图谱携带出生年

- [ ] `relationship_graph.load_graph` 增 `node_birth_years`（`users.birth` JSON
  容错提取；缺省参数 = 旧行为）。
- [ ] 可见性核对：birth 字段脱敏（FIELD_MASKED）节点年份置 None。
- [ ] 测试：提取容错、脱敏置 None、缺省行为不变。

## 3. 长幼消歧

- [ ] `services/terms.py`：`AGE_VARIANT_CLASSES` 表 + 消歧步接入
  `resolve_term_or_structural`（新可选 `variant_context` 参数；缺省 None 逐字节
  旧行为）。
- [ ] 全部调用方核对与接线：`personal_family_view.rebuild_view`、
  `compose_resolution_view`、`agent_query`/agent 工具链等（逐一列单核对，
  缺上下文处传 None）。
- [ ] 测试：可判长幼 → 哥哥/弟弟/姐姐/妹妹及嫂子/弟妹/姐夫/妹夫组合；
  缺失/脱敏 → 泛化词；personal/space 词条优先（不被消歧覆盖）。

## 4. 长链泛化兜底

- [ ] `services/terms.py`：第 5 级解析——最长命名前缀 + 残链小词表；≤64 字截断。
- [ ] 测试：`Um-Df-Sm-Um`→「妹夫的父亲」类用例；不可命名码维持
  SOURCE_LEVEL_STRUCTURAL；超长截断。

## 5. 一致性与回归

- [ ] `COMPUTATION_VERSION` pfv-v2 → pfv-v3（`services/personal_family_view.py`）。
- [ ] 全量回归：`ruff check . && ruff format --check . && mypy app && pytest`
  （重点 test_terms / test_relationship_resolver / PFV / intake_extractor /
  agent_query_tools）。
- [ ] `COMPUTATION_VERSION` 变更触发重算的既有测试对齐版本号。
- [ ] spec 更新评估（Phase 3.3）：relationship-intelligence 词典/解析层级描述
  是否需补第 5 级泛化与消歧语义。

## 6. 收尾

- [ ] smoke：`./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`
  （退出码 2 = 环境阻塞，如实记录）。
- [ ] 部署说明：升级后第一轮管家扫描（≤5 分钟）自动刷新全空间称谓，无需任何
  用户/管理员操作；如需立即生效可走管理端 rerun。

## 回滚点

- 步骤 1：迁移 downgrade 删新增种子行；词典清单独立可回退。
- 步骤 2-4：均为缺省关闭语义的可选参数/新增分支，revert 即回现状。
- 步骤 5：版本号回退触发既有重算机制恢复旧 payload。

## 审查门

- 步骤 1 完成后：词典清单人工评审（称谓正确性是本任务核心资产）。
- 步骤 3-4 完成后：红线核对（§6 设计对照表）+ 全量回归再收尾。

## 执行记录（2026-09-13）

- 步骤 1-6 全部完成；词典清单经 `gen_term_pack.py` 枚举与种子差集核对（111 条 zh-CN /
  25 条 system），隔离库（`DATA_DIR=/tmp/fg-mig-data`）`alembic upgrade head` +
  downgrade/upgrade 往返通过。
- 门禁：`ruff check` / `ruff format --check` / `mypy app` 全绿；受影响套件
  （term_autofix 新增 12 用例、terms、relationship_resolver、personal_family_view、
  intake_extractor、agent_query_tools）98 用例全绿；全量 pytest 1009 passed /
  3 skipped / 1 failed——唯一失败 `test_system_admin_boundary` 归属并行会话提交
  ba445a2（新增 /admin-api/v1/agent/latency 路由未更新边界测试白名单），main 上即
  失败，与本项目无关。
- **并行会话事件**：共享工作树期间 main 被并行提交推进（assistant-session 等），
  迁移 0040 曾在工作树中丢失（git 历史可溯，已从 main 恢复）；经用户指示迁移至独立
  worktree `/Users/lyston/PycharmProjects/familygraph-term-autofix`（分支
  feat/steward-term-autofix，提交 3b39a6b + merge main），主工作区交还 main。
- 前端门禁跳过：本任务零前端改动（payload 与称谓文本均走既有通道）。
- smoke 未跑：需要运行中的 dev 环境；退出码 2 = 环境阻塞，按约定不算通过。
- spec 更新评估：.trellis/spec 已声明弃用（历史资料），relationship-intelligence
  等不再作为门禁；解析第 5 级与消歧语义已写入本任务 design.md 备查。
