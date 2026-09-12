# 父任务实施与整合验收计划

## 当前阶段

规划工件已生成；所有任务保持 planning。用户此次要求建任务和详细文档，没有要求本轮改产品代码或提交。后续开始时先评审最新 child 方案再 start，不因存在工件自动进入实现。

## 有序检查单

- [x] 1. production-ops：扫描/重试/重跑/租约栅栏与配置诊断，先完成 core 可恢复路径。（2026-09-12 实现+核验 PASS；迁移 0036）
- [x] 2. assist-execution：网络出事务、预算和 crash 语义（迁移 0037）；projection-consistency：事件失效、初始化、词典、路径重验和 ETag。均实现+核验 PASS。
- [x] 3. quality-security：安全输入/输出、provider 政策和固定评测（steward_guard + 38 例 eval，硬门禁 27/27）；候选类型与 candidate-review 对齐（{"kind","subject_user_id","object_user_id"} 结构化 digest）。
- [x] 4. candidate-review：证据、受众、人工提案/确认、既有通知扩展（迁移 0038 + 前端建议审核 UI）；核验发现并修复列表端点可见性 MAJOR；owner 非当事人不可直接确认有回归。
- [x] 5. release-observability：E2E 驱动（真实 API + 自动 tick）、admin 观测指标/告警、日志脱敏、容量采样、迁移往返、release-evidence.md 与 runbook.md；真实 provider 未运行 → 门禁 partial（stub 口径）。
- [x] 6. 对照 research/findings.md 把本轮 F01–F21 逐条填入实际 AC 证据；F22/F23 保留 deferred（findings.md 修复状态回填节 + coverage.md 状态列）。
- [x] 7. 更新现行 specs（steward-action-card.md 重写模型辅助节 + 新增调度/建议/观测/安全链节）与源码过时注释（steward.py crash 合同 docstring）；保存既有归档历史；质量核验（backend 956 passed / 前端 525 passed / 管理前端 83 passed / ruff / mypy）、提交和父子归档（延期子任务解除父子关联保留活动）。

## 验证命令与限制

在隔离测试环境，从各自包目录执行：

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

frontend 与 system-admin-frontend：npm run type-check、npm run lint、npm test、npm run build。
agent 包只有触及协议/共享边界或最终整合要求时复跑，不能因为管家任务就擅自改其 runtime。
Compose config --quiet 仅验证结构，不打印展开后的 secrets。迁移使用临时 DATA_DIR；API E2E 必须有自动 tick 的结果，单测直接调用 service 不能替代。

## 交付证据

每个 child notes 保存：完成的 AC、命令/退出码、变更文件、迁移 head、已验证风险、没有取得的证据。父任务不直接承载大批产品实现，其出口是整体 AC 与问题台账收敛。
延期研究可以独立继续；本轮修复关闭时若要归档父任务，应把未完成延期任务保留为活动后续（必要时解除父子关联并双向记录），不能跟随父任务假归档。
