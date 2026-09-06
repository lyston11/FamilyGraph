# Implement

> 前置阅读：prd.md → design.md（裁定 B1–B6）→ 归档 09-01 决策记录。实现顺序按阶段，每阶段末跑验证。

## 阶段 1：数据层

1. `backend/app/models/agent_provider.py`：AgentSpaceProviderSetting 加三列 assist_*（Boolean default False）。
2. `backend/app/models/steward.py`：ActionCard 加 reason_text_llm、presentation_rank；新增 StewardModelCall、StewardLlmCandidate 模型（design §1.2/1.3）；`models/__init__.py` 导出。
3. 迁移 `0034_steward_model_assist.py`（down_revision=0033）：三列 add_column + 两新表 + action_cards 两列；downgrade 逐项 drop。
4. conftest `_clean_tables` 加两新表（子→父序：steward_model_calls/steward_llm_candidates 先于 steward_jobs/action_cards）。
5. `backend/app/config.py`：design §2 五个配置项。
6. 验证：alembic upgrade head + pytest tests/test_steward.py（基线不变）。

## 阶段 2：服务层

7. 新建 `backend/app/services/steward_assist.py`：design §3（_post_json 底座 + _call_model + 三个 maybe_* + run_assists）。
8. `backend/app/services/steward.py`：`_execute_locked` 尾部 hook（design §3.3，broad except + logger）。
9. `backend/app/api/action_cards.py` list_cards 排序改造（design §4）。

## 阶段 3：schema/端点

10. `backend/app/schemas/agent.py`：请求/响应加三 flags 字段（assistant 维度拒绝）。
11. `backend/app/api/space_model_settings.py`：PUT 存 flags、GET 透出。

## 阶段 4：后端测试

12. 新建 `backend/tests/test_steward_assist.py`（design §6 全矩阵，fake transport monkeypatch _post_json）。
13. 扩展 `backend/tests/test_space_model_settings.py`（flags 用例）。
14. 验证：`.venv/bin/pytest` 全量。

## 阶段 5：前端

15. `frontend/src/types/agent.ts` / `api/spaceModelSettings.ts`：flags 字段。
16. `frontend/src/components/member/SpaceModelSettingsPanel.vue`：steward 区块三开关。
17. `frontend/src/types/actionCard.ts` + `components/actioncard/ActionCardItem.vue`：reason_text_llm 优先。
18. 前端 spec：panel 开关、ActionCardItem 文案优先。验证：npm test + type-check。

## 阶段 6：收尾

19. grep 复核：无 AgentRun 伪造、无 prompt 明文落库、助手三表零写入。
20. 全量回归 + 提交（本任务文件）+ 归档 + 父任务归档 + 日志（主会话执行）。
