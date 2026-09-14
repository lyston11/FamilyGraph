# Steward 本地提交计划（验收完成，待一次确认）

分支：`feat/09-13-steward-snapshot-progressive-recompute`；基点：`0baf299`。仅计划以下本地提交，不含 push、main merge、部署、archive 或 worktree 清理。文件按本任务当前 dirty 状态枚举，提交前再核对变化。

主检出的本任务 PRD/design/implement/context/research 是已知任务文档，继续留在主检出供后续串行集成处理；不通过本次确认在 main 提交，不夹带其他任务。

## 提交顺序

### fix(steward): atomically publish fenced snapshot recomputations

- `.trellis/spec/backend/steward-action-card.md`
- `backend/app/api/admin_steward.py`
- `backend/app/api/personal_family_view.py`
- `backend/app/config.py`
- `backend/app/models/steward.py`
- `backend/app/models/steward_suggestion.py`
- `backend/app/schemas/admin_steward.py`
- `backend/app/schemas/personal_family_view.py`
- `backend/app/services/derived_facts.py`
- `backend/app/services/family_recommendations.py`
- `backend/app/services/household_card.py`
- `backend/app/services/kinship_presentation.py`
- `backend/app/services/maintenance.py`
- `backend/app/services/personal_family_view.py`
- `backend/app/services/relationship_graph.py`
- `backend/app/services/relationship_resolver.py`
- `backend/app/services/space_stats.py`
- `backend/app/services/steward.py`
- `backend/app/services/steward_assist.py`
- `backend/app/services/steward_delivery.py`
- `backend/app/services/steward_demand.py`
- `backend/app/services/steward_gc.py`
- `backend/app/services/steward_inferred.py`
- `backend/app/services/steward_overlay.py`
- `backend/app/services/steward_pipeline.py`
- `backend/app/services/steward_runtime.py`
- `backend/app/services/steward_snapshot.py`
- `backend/app/services/steward_suggestions.py`
- `backend/app/services/steward_views.py`
- `backend/app/services/terms.py`
- `backend/migrations/versions/0041_term_pack_expansion.py`
- `backend/migrations/versions/0042_steward_inferred_edges.py`
- `backend/migrations/versions/0045_steward_staged_publication.py`
- `backend/tests/conftest.py`
- `backend/tests/test_household_card.py`
- `backend/tests/test_maintenance.py`
- `backend/tests/test_personal_family_view_progressive.py`
- `backend/tests/test_relationship_snapshot_compute.py`
- `backend/tests/test_space_stats.py`
- `backend/tests/test_steward.py`
- `backend/tests/test_steward_assist.py`
- `backend/tests/test_steward_delivery_recovery.py`
- `backend/tests/test_steward_generations.py`
- `backend/tests/test_steward_inferred.py`
- `backend/tests/test_steward_input_versions.py`
- `backend/tests/test_steward_log_hygiene.py`
- `backend/tests/test_steward_publication_consumers.py`
- `backend/tests/test_steward_runtime_recovery.py`
- `backend/tests/test_steward_short_tx.py`
- `backend/tests/test_steward_snapshot_fences.py`
- `backend/tests/test_steward_staged_pipeline.py`
- `backend/tests/test_system_admin_boundary.py`
- `backend/tests/test_term_autofix.py`

### feat(pfv): progressively fill kinship labels on a stable family skeleton

- `.trellis/spec/frontend/state-management.md`
- `frontend/src/__tests__/personalFamilyViewFixtures.ts`
- `frontend/src/api/__tests__/personalFamilyView.spec.ts`
- `frontend/src/api/__tests__/personalFamilyViewProgress.spec.ts`
- `frontend/src/api/personalFamilyView.ts`
- `frontend/src/components/canvas/MemberNode.vue`
- `frontend/src/components/member/__tests__/SpaceModelSettingsPanel.spec.ts`
- `frontend/src/composables/__tests__/usePersonalFamilyViewPolling.spec.ts`
- `frontend/src/composables/__tests__/useSpaceContext.spec.ts`
- `frontend/src/composables/useFamilyTreeCanvas.ts`
- `frontend/src/composables/usePersonalFamilyViewPolling.ts`
- `frontend/src/stores/__tests__/personalFamilyView.spec.ts`
- `frontend/src/stores/__tests__/personalFamilyViewProgress.spec.ts`
- `frontend/src/stores/personalFamilyView.ts`
- `frontend/src/types/api.ts`
- `frontend/src/views/FamilyTreeView.vue`
- `frontend/src/views/PersonProfileView.vue`
- `frontend/src/views/__tests__/family-tree.spec.ts`
- `frontend/src/views/__tests__/notifications.spec.ts`
- `frontend/src/views/__tests__/person-profile.spec.ts`

### test(steward): verify concurrent writes and progressive browser loading

- `backend/tests/test_steward_benchmark_measurement.py`
- `scripts/benchmark-pfv-browser.py`
- `scripts/benchmark-steward-recompute.py`
- `scripts/smoke/run_api_smoke.py`

第一组含 0041/0042 的必要格式修正；两文件 AST 与 HEAD 相同，不改历史迁移逻辑。0045 与 main 新增 0044 的集成冲突仍留给后续串行通道。

## 主检出中未纳入的其他 dirty 文件

以下均非本次提交范围，保留现场；无需为本任务另行决定是否收录。

- `.trellis/config.yaml`
- `.trellis/scripts/hooks/auto_worktree.py`
- `.trellis/tasks/09-11-steward-capability-followups/check.jsonl`
- `.trellis/tasks/09-11-steward-capability-followups/design.md`
- `.trellis/tasks/09-11-steward-capability-followups/implement.jsonl`
- `.trellis/tasks/09-11-steward-capability-followups/implement.md`
- `.trellis/tasks/09-11-steward-capability-followups/notes.md`
- `.trellis/tasks/09-11-steward-capability-followups/prd.md`
- `.trellis/tasks/09-12-agent-provider-config-ux/check.jsonl`
- `.trellis/tasks/09-12-agent-provider-config-ux/implement.jsonl`
- `.trellis/tasks/09-12-agent-provider-config-ux/prd.md`
- `.trellis/tasks/09-12-agent-provider-config-ux/research/learngraph-provider-pattern.md`
- `.trellis/tasks/09-12-agent-provider-config-ux/task.json`
- `.trellis/tasks/09-13-agent-memory-capability-plan/check.jsonl`
- `.trellis/tasks/09-13-agent-memory-capability-plan/design.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/implement.jsonl`
- `.trellis/tasks/09-13-agent-memory-capability-plan/implement.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/prd.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/behavior-projection-rebuild-results.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/capability-decision-register.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/context-budget-design-results.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/context-budget-probe-results.json`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/context_budget_probe.py`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/module-resolution-check.json`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/research-validation.json`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/research-validation.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/steward-candidate-evidence-results.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/steward-capability-handoff.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/steward-capability-results.json`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/steward-reproduction-protocol.md`
- `.trellis/tasks/09-13-agent-memory-capability-plan/research/steward_capability_probe.py`
- `.trellis/tasks/09-13-agent-memory-capability-plan/task.json`
- `.trellis/tasks/09-13-agent-memory-rag-remediation/implement.md`
- `.trellis/tasks/09-13-agent-memory-rag-remediation/prd.md`
- `.trellis/tasks/09-13-agent-memory-rag-remediation/research/execution.md`
- `.trellis/tasks/09-13-agent-memory-rag-remediation/task.json`
- `.trellis/tasks/09-13-assistant-context-compaction/check.jsonl`
- `.trellis/tasks/09-13-assistant-context-compaction/design.md`
- `.trellis/tasks/09-13-assistant-context-compaction/implement.jsonl`
- `.trellis/tasks/09-13-assistant-context-compaction/implement.md`
- `.trellis/tasks/09-13-assistant-context-compaction/prd.md`
- `.trellis/tasks/09-13-assistant-context-compaction/research/check.md`
- `.trellis/tasks/09-13-assistant-context-compaction/research/implementation.md`
- `.trellis/tasks/09-13-assistant-context-compaction/task.json`
- `.trellis/tasks/09-13-memory-contract-repair/check.jsonl`
- `.trellis/tasks/09-13-memory-contract-repair/design.md`
- `.trellis/tasks/09-13-memory-contract-repair/implement.jsonl`
- `.trellis/tasks/09-13-memory-contract-repair/implement.md`
- `.trellis/tasks/09-13-memory-contract-repair/prd.md`
- `.trellis/tasks/09-13-memory-contract-repair/research/api-contract.md`
- `.trellis/tasks/09-13-memory-contract-repair/research/backend-check.md`
- `.trellis/tasks/09-13-memory-contract-repair/research/backend-validation.md`
- `.trellis/tasks/09-13-memory-contract-repair/research/frontend-check.md`
- `.trellis/tasks/09-13-memory-contract-repair/research/real-api-smoke.json`
- `.trellis/tasks/09-13-memory-contract-repair/research/smoke-boundary.md`
- `.trellis/tasks/09-13-memory-contract-repair/research/validation.md`
- `.trellis/tasks/09-13-memory-contract-repair/task.json`
- `.trellis/tasks/09-13-rag-index-lifecycle/check.jsonl`
- `.trellis/tasks/09-13-rag-index-lifecycle/design.md`
- `.trellis/tasks/09-13-rag-index-lifecycle/implement.jsonl`
- `.trellis/tasks/09-13-rag-index-lifecycle/implement.md`
- `.trellis/tasks/09-13-rag-index-lifecycle/prd.md`
- `.trellis/tasks/09-13-rag-index-lifecycle/research/implementation.md`
- `.trellis/tasks/09-13-rag-index-lifecycle/task.json`
- `.trellis/tasks/09-13-rag-retrieval-citations/check.jsonl`
- `.trellis/tasks/09-13-rag-retrieval-citations/design.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/implement.jsonl`
- `.trellis/tasks/09-13-rag-retrieval-citations/implement.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/prd.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-check.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-implementation-backend.log`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-implementation.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-r04-followup.log`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-r04-probes.log`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-retrieval.json`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-review-backend.log`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-review-retrieval.json`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/acceptance-smoke.json`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/dispatch-boundaries.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/implementation.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/protocol-preflight.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/retrieval-baseline.json`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/retrieval-baseline.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/retrieval-fixture-v1.json`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/smoke-harness-update.md`
- `.trellis/tasks/09-13-rag-retrieval-citations/research/test_retrieval_probe.py`
- `.trellis/tasks/09-13-rag-retrieval-citations/task.json`
- `.trellis/tasks/09-13-steward-assist-platform-switch-admin/check.jsonl`
- `.trellis/tasks/09-13-steward-assist-platform-switch-admin/implement.jsonl`
- `.trellis/tasks/09-13-steward-assist-platform-switch-admin/prd.md`
- `.trellis/tasks/09-13-steward-assist-platform-switch-admin/task.json`
- `.trellis/tasks/09-13-steward-memory-evidence-projections/check.jsonl`
- `.trellis/tasks/09-13-steward-memory-evidence-projections/design.md`
- `.trellis/tasks/09-13-steward-memory-evidence-projections/implement.jsonl`
- `.trellis/tasks/09-13-steward-memory-evidence-projections/implement.md`
- `.trellis/tasks/09-13-steward-memory-evidence-projections/prd.md`
- `.trellis/tasks/09-13-steward-memory-evidence-projections/task.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/check.jsonl`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/design.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/implement.jsonl`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/implement.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/prd.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/acceptance-matrix.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/architecture.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/artifacts.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/b-integration-check.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/c/check.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/c/implementation.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/code-baseline.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/coverage-and-roadmap.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/d-execution-preflight.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/d-integration-check.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/evidence.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/findings.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/planning-sources.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/planning-validation.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/planning-validation.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/b-additional.log`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/b-byte-control.log`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/b-first.log`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/d-additional.log`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/d-first.log`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/d-revocation.log`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/raw/b-additional.log.gz`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/raw/b-byte-control.log.gz`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/raw/b-first.log.gz`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/raw/d-additional.log.gz`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/raw/d-first.log.gz`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/raw/d-revocation.log.gz`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/test_b_contract_probes.py`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/probes/test_d_contract_probes.py`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/protocol-preflight.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/retrieval/retrieval-baseline.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/retrieval/retrieval-baseline.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/retrieval/retrieval-bd899b9.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/retrieval/retrieval-fixture-v1.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/retrieval/test_retrieval_probe.py`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/retrieval/test_retrieval_probe_a.py`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/smoke/agent_memory_worker.mjs`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/smoke/harness-strict-sse-draft.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/smoke/initial-api-smoke.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/smoke/real-agent-memory-smoke.json`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/smoke/run_agent_memory_smoke.py`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/research/validation.md`
- `.trellis/tasks/09-14-memory-rag-acceptance-audit/task.json`
- `AGENTS.md`
- `backend/migrations/versions/0041_term_pack_expansion.py`

## 确认规则

依据 `.trellis/workflow.md` §3.4："Present the plan once, ask for one-shot confirmation"。全部实现和验收完成后一次确认上述三组本地提交；确认不包含任何对外操作或 main 集成。
