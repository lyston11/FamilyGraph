# 实施计划：Agent 策略边界

## 当前阶段

- 当前为 planning，用户本轮仅要求规划；尚未 `task.py start`。
- 已形成 PRD、design 和研究摘要。没有业务代码或环境修改。
- 用户审阅最新规划并授权实施后，再启动任务；进入任务生成的专属 worktree 写代码。
- 本任务为共享策略修复，步骤顺序执行；不并行编辑相同 policy/worker 文件。

## P0 实施前合同验证

- [x] 阅读任务 PRD/design 与 manifests；确认 scope 仍是 R1–R7。`agent-runtime.md` 超过默认注入单文件大小，未放入 manifests；手动按需完整读取其中 Internal 协议、执行模型/取消终态、Provider 治理及输出安全边界相关章节，禁止依赖截断注入。
- [x] 在主检出执行 `task.py start .trellis/tasks/09-29-agent-policy-boundary`，读取 task.json 的 branch/worktree_path，并进入该 worktree。
- [x] 按锁定依赖版本读取 Pi SDK 的 hook/abort/retry 文档和类型；使用现有 `worker.integration.test.ts` 的真实 SDK seam 验证如何停止执行。不得把模拟回调当作真实 SDK 终止证据。
- [x] 核对 ContextOut → session 和 tool output → SDK 的直接路径：可信数据块元数据在哪一层校验、哪些内容字段可改写、thinking 不透明字段如何保留。
- [x] 核对后端 `input_hook` / `tool_call_hook` 的 `PolicyDecision` 经 `enforce()` 后的返回值；保持现有接口行为和 HTTP 错误域。
- [x] 核对 logger 的 run 关联、未知工具名与 tool_call_id 的安全/长度约束；优先复用已有安全字段。
- [ ] 若 SDK 机制不能实现 R4，或必要安全合同需要改 wire schema，先修订 design；不得直接降级为“只在最后失败”。

完成门槛：来源、阻断与异常路径可明确落在既有接口；无未解决的安全语义问题。

## P1 错误分类与安全诊断（A 阶段）

- [x] 在 policy 现有类型上补固定规则、阶段、来源、动作字段；避免通用配置引擎。
- [x] 统一正常路径/catch 的策略错误映射，保留历史错误码读取；未知原因使用通用策略码。
- [x] 记录首个硬阻断与有界诊断摘要；去重依据来源坐标，不保存正文/内容哈希。
- [x] 保证日志失败不放行请求、notice 不覆盖硬阻断主因。
- [x] 添加前端新错误文案与历史 `POLICY_SECRET_LEAK` 兼容。
- [x] 验证本阶段尚未改变策略拒绝判据，形成可回退的独立提交。

验收：AC-6、AC-7。可回退点：本阶段提交。

## P2 按合同检查内容（B 阶段第一部分）

- [x] 后端拆分 `contains_prompt_injection` 与 `contains_secret` 的组合拒绝；词面提示不改变授权、secret 与参数验证。
- [x] sidecar input/tool_call/tool_result/context/payload 一致移除“词面命中就硬阻断”的逻辑；保留有界 notice。
- [x] 保留服务端固定位置的 masked/trust/kind 拒绝，以及真正的数据可见性限制；正文伪造 metadata 不赋权。
- [x] 不以 assistant role 跳过所有检查；任意角色出站仍检查密钥和 Provider 限制。
- [x] 移除整消息关键词删除；脱敏/标注只处理允许字段且幂等，不破坏工具配对、调用 id 和不透明签名。
- [x] 覆盖安全替换内容再次经过 context → provider 的链路，消除自触发。

验收：AC-1、AC-2、AC-3、AC-4。不得删除仍有效的安全负例，仅更新已明确改变的关键词语义用例。

## P3 硬阻断后的停止与结算（B 阶段第二部分）

- [x] 在 guard 内实现不可清除的 run 级硬阻断状态；先置位再通知停止。
- [x] 接入现有 session abort；transport 和工具执行入口再次检查状态，阻止 SDK 重试和后续工具轮。
- [x] worker 正常/catch/stream error/abort 分支读取统一决定，符合已有取消/失租和终态幂等合同。
- [x] 保留已发生的计费，不自动重放整个 attempt；不改 Steward apply/recovery。
- [x] 真实 SDK 集成用实际计数证明阻断后无新增 Provider 请求/工具执行；覆盖同轮多个工具与重试情形。

验收：AC-5，并重跑 AC-6。B 阶段行为必须成套交付，不只上线半套 gate。

## P4 最小充分回归与审查

从任务 worktree 执行，依项目现有虚拟环境/依赖安装方式准备；本任务无数据库迁移。

```bash
# agent/：共享 policy 涉及两类 run，执行 sidecar 全套一次
npm run type-check
npm run lint
npm test
npm run build

# backend/：策略与对应授权/出站边界
.venv/bin/pytest tests/test_policy_guard.py tests/test_agent_tools.py tests/test_steward_tools.py tests/test_provider_proxy.py tests/test_steward_candidate_policy.py
.venv/bin/ruff check app/services/policy_guard.py tests/test_policy_guard.py
.venv/bin/ruff format --check app/services/policy_guard.py tests/test_policy_guard.py
.venv/bin/mypy app

# frontend/：只改错误码显示
npm run type-check
npx vitest run src/api/__tests__/agentErrors.spec.ts
```

- [x] 后端额外改到的直接入口或测试文件加入受影响检查，不用上述固定列表漏掉实际改动。
- [x] 检查新错误码的前后端显示、已有密钥/PII/token cap 类型保护、可信数据块拒绝与 scope 参数约束。
- [x] 按源码 diff 检查日志不存在任意正文/凭据，范围内 lint/format 通过；前端仅对实际改动文件运行现有 lint 命令。
- [x] 对 SDK hook、最终出站、服务端工具执行分别检查可绕过点；安全词面检测不能成为权限唯一防线。
- [x] 不默认跑未改模块的整套构建或 backend 全套；若公共 helper 的实际调用范围扩大，再说明依据并扩大检查。

## P5 开发环境验收

- [x] 确认环境是 `/home/ubuntu/projects/FamilyGraph`、systemd **用户**单元 `familygraph-api` / `familygraph-agent`、`~/.config/familygraph/familygraph.env`。核对有效配置来源但不输出密钥。
- [x] 确认代码 SHA、安装依赖版本、agent/dist 新构建以及进程运行版本一致；只发布到开发服务。
- [x] 无迁移、无业务数据修补；写入型验证必须使用隔离 DATA_DIR。正常开发业务链路按现有授权路径触发，不直接造库行。
- [x] 选择当前已授权 Provider 的空间，记录至少一次真实 Steward 正常闭环（run 65 / attempt 1057，egress 200）。
- [!] **Assistant 真实流量未观察到**：开发库最后一次 assistant run 是 2026-09-20，本任务期间无 assistant 请求。assistant 路径由 `worker.integration.test.ts` 的真实 SDK 集成用例与前端文案断言覆盖，但**不得**宣称已做真实 assistant 验收（见 `research/acceptance-dev.md`）。
- [x] 分列启用、真实调用、合法产物、应用/可见改善：`succeeded` 不替代产物验收，`applied_at` 不替代用户可见改善；无改善时明确 no-op/未观察到，不伪造业务变化。
- [x] 硬阻断、越权和密钥诱饵在隔离测试中验证，不向真实 Provider 发送生产/开发真实密钥或构造越权探针。
- [x] 记录 run/attempt id、版本、错误分类与日志可诊断性，不保存原始模型文本或密钥。
- [x] 健康检查、无残留执行；不以一次成功宣称历史 run 48 原因已确定。

禁止操作：`/home/ubuntu/fg-prod`、`familygraph-prod-*`、线上 env/卷、公网发布与还原脚本。禁止改 Provider 白名单以促成验收。环境或上游阻塞时 AC-8 保持未完成并准确报告，不标记通过。

## P6 合同与任务收尾

- [x] 在 spec 中记录本次经过验证的策略合同；独立叶文件承载来源/动作/阻断/诊断，index 仅加链接，旧 runtime 规范只链接不复制正文。
- [x] 逐项记录 AC 证据、实际验证命令与未执行的高成本检查理由。
- [x] 串行提交/集成，遵守单一 main 集成点；不 reset/rebase 他人分支。
- [x] 完成后归档任务；分支已合并进 main，worktree 无未提交业务改动，删除 worktree 与任务分支。
- [ ] 交付明确开发环境结果与线上未操作。若仍有 AC 未达成，保留任务，不以“代码完成”替代整体完成。

## 验收映射与回退

| 验收 | 主要证据 |
|---|---|
| AC-1 | backend input/tool policy + agent 多轮关键词回归 |
| AC-2 | backend 授权/结构化 context + sidecar allowlist/scope/Provider 负例 |
| AC-3 | agent 全角色出站密钥 + backend Provider + token cap 回归 |
| AC-4 | policy hook 串联/真实消息结构、重复处理不变性 |
| AC-5 | worker 真实 SDK transport/工具计数、重试与取消竞争 |
| AC-6 | worker 正常/异常一致性 + 前端 agentErrors |
| AC-7 | 敏感诱饵零日志泄漏、有界/去重/日志异常回归 |
| AC-8 | 开发环境验收记录，四项结果分列 |

回退只回退本任务代码/构建，不还原数据库、不关闭 guard；B 出现回归时退回 A 的诊断版本。任何需改范围/安全合同的回退须写入任务工件，不能静默删除需求。
