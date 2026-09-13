# 平台级 Steward 辅助开关纳入管理端治理

## Goal

steward 三类 LLM 辅助（候选补全/推荐排序/卡片解释）的有效开关是「平台级 AND 空间级」
（`steward_assist.assist_enabled`）。平台级三个布尔值 `STEWARD_ASSIST_CANDIDATE /
STEWARD_ASSIST_RANKING / STEWARD_ASSIST_EXPLANATION` 只能通过服务器环境变量开启：
没有 admin API、没有管理界面、没有向 owner 暴露的状态。结果是空间 owner 在家庭端
把开关全部打开后，辅助仍然一次都不会发生，且界面不显示任何原因。

## 过程记录（2026-09-12 真实 Provider E2E）

- 用户已在空间 2（王氏家族）打开三个空间级辅助开关（`GET /api/spaces/2/model-settings`
  确认 `assist_candidate/ranking/explanation` 全为 true）。
- 服务器 DB 实测：steward 核心作业每分钟照常 succeeded，但 `steward_model_calls` 恒为
  0、`steward_assist_batches` 为空——辅助批次从未登记。
- 排查 `steward_assist.py`：`register_batch_for_job` 先查 `_enabled_kinds` →
  `assist_enabled = _platform_flag(kind) AND _space_flag(...)`；`_platform_flag` 读
  `config.STEWARD_ASSIST_*`（env，默认全关）。
- 服务器 env 实测：`grep -cE '^STEWARD_ASSIST_(CANDIDATE|RANKING|EXPLANATION)='` 计数
  为 0（三个键完全不存在）。`admin_steward.py` 的 `/steward/status` 只读展示
  switch 状态，无任何修改端点。
- 临时处置：向 `/home/ubuntu/.config/familygraph/familygraph.env` 追加三个 `=1`
  （备份 `familygraph.env.bak-20260912`）并 `systemctl --user restart familygraph-api`。
  下一轮 integrity_scan 后辅助立刻生效：candidate 调用 9 次 succeeded、explanation
  8 次 succeeded，4 张 pending 卡全部写入 `reason_text_llm`。
- 管理员视角缺陷确认：管理端「模型治理」页与家庭端模型设置面板都没有任何平台级
  开关状态提示，owner 无法自行发现该根因，只能靠运维登服务器。

## Requirements

- 平台级三个辅助开关必须有管理端治理入口（admin API + 系统管理后台 UI + 审计），
  形态可以是：持久化到现有平台配置表（参照 `agent_platform_defaults` 单行懒建先例）
  并由 admin API 读写；env 仅作为初始值/紧急通道，需在文档中明确优先级关系。
- `/admin-api/v1/steward/status` 的 switch 展示改为反映「生效开关 = 平台 AND 空间」，
  或同时展示平台级状态，避免误导。
- 家庭端空间模型设置面板：当空间级开关打开但平台级未开启时，给出明确的平台级未
  开启提示（可解释、可行动），替代当前的静默不生效。
- 语义约束保持 fail-closed：默认仍为关；开关只控制 LLM 辅助点，绝不改变确定性
  core 结论；预算（`STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB` 等）语义不变。

## Acceptance Criteria

- [ ] 管理员可在系统管理后台查看并切换三个平台级开关，变更写审计。
- [ ] 平台级关闭时，家庭端模型设置面板对已开启的空间级开关显示可解释的平台级未开启提示。
- [ ] `/admin-api/v1/steward/status` switch 展示与实际生效语义一致。
- [ ] 后端测试覆盖：平台级/空间级组合真值表、admin 写路径审计、家庭端提示 detail。
- [ ] admin-web 相关组件测试与 lint/type-check 通过；spec（agent-runtime / steward 相关）更新。

## Notes

- 相关过程证据（延迟、超时数据）见 `09-13-agent-latency-tuning`；部署层问题见
  `09-13-sidecar-server-deployment`。
