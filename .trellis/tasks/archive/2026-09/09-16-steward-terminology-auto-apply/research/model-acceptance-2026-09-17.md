# 真实模型验收记录（2026-09-17）

本文件记录 AC6/AC7/AC9 的服务器隔离库与生产核对结果。所有服务器侧验证都在
`DATA_DIR` 指向的隔离副本上运行，脚本内断言 `config.DATABASE_URL` 含隔离目录名；
未向生产库写入测试人物、消息或关系。

## 1. 生产只读核对（未写入）

- 代码：远端检出 `7480367`（含 `b4f1283`、`f98e824`），工作区干净。
- 服务：`systemctl --user` 下 `familygraph-api` / `familygraph-agent` 均 active；sidecar `/healthz` 200。
- 迁移：`alembic_version = 0050_term_alias_spouse_fix`。
- 词表：`Sm-Bm/Sm-Bf/Sf-Bm/Sf-Bf` 四条 locale 行已改正（revision=2）。
- 发布结果（`steward_view_targets`）：`Um-Dm-Dm → 侄子(locale)`、`Um-Um-Dm → 叔伯(locale)`、
  `Sm-Um-Dm → 丈夫的兄弟`、`Sf-Um-Dm → 妻子的兄弟`，均为确定性别名命中，非模型来源。
- 称谓通知：`notifications` 中 `steward_suggestion` 为 0（仅 21 条 `action_card`），
  与 R4「称谓不进入待办」一致。
- 存量建议：535 条 `term_preference` 仍为 `proposed`，但经 `effective_state` 退出活跃消费
  （`notifications.py` 把该 kind 的 `pending` 投影为 `done`）。
- 术语开关：生产 env 只设置 `STEWARD_ASSIST_CANDIDATE/RANKING/EXPLANATION=1`，
  未设置 `STEWARD_ASSIST_TERMINOLOGY`；`platform_feature_configs` 无行；
  `agent_space_provider_settings` 只有 assistant 行，没有 steward 行。
  → 生产 terminology 有效开关为 **关闭**，`steward_model_calls` 中无 terminology 记录。

## 2. 隔离库真实 Provider 全链（AC6）

隔离副本：`sqlite3 .backup` 自生产库，`DATA_DIR=/home/ubuntu/fg-term-accept-20260917-024910`。
Provider 为部署既有 `liu-dada`（`openai-responses`，`gpt-5.6-sol`），云许可为隔离库内设置。

- **真实调度链**：`enqueue_steward_job(cause=integrity_scan)` → maintenance tick →
  delivery drain → 登记 `StewardAssistBatch(kinds=['terminology'])` → `run_due_batch`
  走真实 HTTP。观察到 6 个批次，其中 5 个 `applied`。
- **真实调用记录**（`steward_model_calls`，全部为真实网络）：
  - call 3：`succeeded`，latency 20.6s，prompt 1542 / completion 132，输出 `{"items": []}`；
  - call 4：`succeeded`，latency 157.0s，prompt 1523 / completion 149，输出 `{"items": []}`；
  - call 2：`degraded / invalid_output`（该次上游返回非 JSON 对象，服务端按约定拒绝）；
  - call 1：`unknown / timeout`（超时；批次终态 `failed network_unknown`，未自动重发）。
- **校验器独立探针**（同隔离库，绕过模型直接投喂输出）证明写回闸门不是"永远拒绝"：
  - 合法同义（`Um 爸爸→父亲`、`Uf 妈妈→母亲`、`Um-Dm 哥哥→兄弟`，`reason=synonym`）
    → 3 条全部通过校验并返回带 `semantic_hash` 的产物；
  - `preferred_usage` 但无本人用词 → 拒绝；不在 `allowed_terms` 的词 → 拒绝；概念码不符 → 拒绝。
  → 校验器按设计工作；AC6 的"合法真实模型改善被自动消费"未达成，原因在下游模型。

## 3. AC7 判定：阻塞，且非配置问题

对隔离库中 870 条已发布目标（20 个空间、124 个概念）做了穷尽扫描：

- `baseline_source` 分布：`locale` 43 个模式、`derived` 85 个模式。
- **无任何"真实改善"候选 = 0**：不存在词条层级高于当前 baseline、或长度严格短于 baseline 的
  合法 `allowed_terms`。即所有目标的 baseline 已是候选集合中最优的显示。
  （别名归一化后 `Um-Dm-Dm=侄子`、`Um-Um-Dm=叔伯` 等已由确定性层直接命中，
  这正是 `b4f1283` 的效果。）
- 存在**等长同义候选**（`爸爸↔父亲`、`妈妈↔母亲`、`哥哥↔兄弟`、`妹妹↔姐妹`、
  `孙子↔儿子的儿子` 等 26 个模式），它们通过 `collect_model_groups` 的入选门槛
  （`len(t) <= len(baseline)`），也确实被投喂给了模型；但模型未将其视为改善，
  按 `_PROMPTS['terminology']`"无改善返回空 items 列表"的要求全部弃权。
- 真实模型在 4 个不同 viewer（target 数 2/3/8/8）上均返回空 `items`；其中 viewer 3 的
  输入同时包含 `爸爸/父亲`、`弟弟/兄弟`、`妹妹/姐妹` 等可选同义词，模型仍全部放弃。

结论：**当前词表与数据下不存在可被判定为"改善"的合法变更**，因此无法产生"至少一个合法真实模型改善被自动消费"的实例。
这是上游语义结论而非调用链缺陷：确定性层已把可证明的改善全部做掉（见 §1 生产发布结果），
模型对剩余的等长同义替换正确地选择不动作。
按 AC7 要求"若上游无合格结果则标明阻塞，不能以健康 200 或 fake 成功替代"，AC7 标记为**阻塞（上游无合格结果）**。

## 3.1 因此未启用生产 terminology 开关

生产 env 缺 `STEWARD_ASSIST_TERMINOLOGY`、`platform_feature_configs` 无行、空间无 steward 行，
有效开关为关（§1）。按 §3 结论，开启后 `collect_model_groups` 只会围绕等长同义候选
发起真实调用，而模型已证明对这些候选一律弃权——即产生 token 消耗与云数据外发，
零用户可见改善。此外新建 steward 行需要写 `cloud_allowed=True`，属于空间云同意范围扩大。
综合"零收益 + 云同意扩大 + 需重启服务"，本任务不擅自开启，交由用户决定；
R5 的启用前提（存在可改善目标）在当前数据下不成立。

未做（明确不作为通过依据）：不为了让模型产出结果而降低校验、放开词表或伪造 preferred usage；
不为制造改善率反复请求同输入。

## 4. 未运行的检查

- 浏览器端到端 smoke（`scripts/frontend-api-smoke.sh`）：需运行中的前端+后端会话，未执行。
- `system-admin-frontend` lint/type-check/test/build：本任务未改动管理员前端，未执行。
