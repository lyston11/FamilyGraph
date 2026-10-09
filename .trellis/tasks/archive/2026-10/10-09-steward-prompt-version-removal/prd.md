# 移除 steward 跨层 prompt 版本仪式并加固助手工具名 fence

## Goal

删除 `STEWARD_PROMPT_VERSION` 这一整套跨层版本机制（它保护的是一个已被否决的设计：steward prompt 文本住在 sidecar 镜像里）。保留真正生效的机制：steward prompt 文本由服务端在 context 投影中下发（`steward_instructions`），`prompt_digest` 覆盖实际发送的文本。

同时修正 5 处把事实写反的注释（都声称文本住在 sidecar），并为助手 system prompt 中硬编码的家族图工具名补一条与 `TOOL_VERSIONS` 对齐的回归 fence。

## Background

- 服务端 `steward_assist._PROMPTS[kind]` 是 steward prompt 文本的唯一事实源，经
  `instructions_for()` → `internal_agent.py` 的 context 投影字段 `steward_instructions` 下发；
  sidecar `kind.ts` 的 `stewardAdapter.systemPrompt()` 直接使用它，缺失即抛错。
- `prompt_digest = sha256(f"{instructions}\n{user_content}")`，因此「发送的就是 digest 描述的那段」
  已由 `test_steward_child_run_acceptance.py::test_the_sent_prompt_is_the_one_the_digest_describes`
  从投影重建 digest 检验。这是真正承重的保证。
- `STEWARD_PROMPT_VERSION` 的原始前提是「文本在 sidecar 镜像里，服务端无法哈希」。S1 的本地
  steward prompt 被删除后该前提消失，但机制未拆：服务端在同一个响应里既发真实文本又发一个字面量，
  sidecar 拿自己编译进去的常量比对。文本既由服务端给，版本不匹配也改变不了 sidecar 会发送什么。
- 真正防止「旧镜像发送本地 prompt」的是另外两道，且都更强：`steward_instructions` 缺失即抛错
  （旧镜像的 adapter 不读该字段），以及 `adapters-kind.test.ts` 的 fail-closed 断言。

## Requirements

### R1 删除 steward 跨层版本机制（backend）

- 删除 `backend/app/services/steward_assist.py` 的 `STEWARD_PROMPT_VERSION` 常量及其 `__all__` 条目。
- 删除 `backend/app/schemas/agent.py` 的 `RunContextOut.steward_prompt_version` 字段。
- 删除 `backend/app/api/internal_agent.py` 构造响应时的 `steward_prompt_version=...` 传参。
- 保留 `steward_instructions` 字段与其必填语义，保留 `instructions_for()` 与 `prompt_version()`
  （后者是评测报告的锚点，不属本次删除范围）。

### R2 删除 steward 跨层版本机制（sidecar）

- 删除 `agent/src/prompts/steward.ts` 整个文件（及空目录 `agent/src/prompts/`）。
- 删除 `agent/src/client.ts` 的 `steward_prompt_version` 字段声明与解码分支。
- 删除 `agent/src/adapters/kind.ts` 的 `STEWARD_PROMPT_VERSION` import 与 `verifyProjection` 中的
  版本比对分支；保留 `steward_instructions` 缺失即抛错的检查。

### R3 删除对应的回归测试

- 删除 `backend/tests/test_agent_execution_fence.py::test_steward_prompt_version_is_the_asserted_literal`。
- 从 `backend/tests/test_agent_schema_contract.py` 的投影字段清单中移除 `steward_prompt_version`。
- 删除 `agent/test/worker-slots.test.ts` 中「跨层字面量逐字断言」与「prompt 版本不匹配即拒绝」两个用例。
- 从 `agent/test/worker.integration.test.ts` 移除 `STEWARD_PROMPT_VERSION` import 与投影字段。
- 「镜像里没有本地 steward prompt 可发」这一性质由 `agent/test/adapters-kind.test.ts` 既有的
  fail-closed 用例覆盖，不新增替代断言。

### R4 修正反向注释（5 处）

以下注释都声称 prompt 文本住在 sidecar / 服务端不再持有文本，与实际相反，改为「文本由服务端拥有、
经投影下发，sidecar 不持有本地 steward prompt」：

- `backend/app/services/steward_assist.py`（`STEWARD_PROMPT_VERSION` 上方注释块）
- `backend/app/schemas/agent.py`（`steward_prompt_version` 上方注释）
- `backend/tests/test_agent_execution_fence.py`（`test_steward_prompt_version_is_the_asserted_literal`
  docstring — 随 R3 一并删除）
- `agent/src/adapters/kind.ts`（`verifyProjection` 内注释）
- `agent/test/worker-slots.test.ts`（版本不匹配用例内注释 — 随 R3 一并删除）

### R5 助手工具名 fence

`agent/src/prompt.ts` 的 `ASSISTANT_SYSTEM_PROMPT` 硬编码了家族图工具名，而 `agent/src/tools.ts`
的 `TOOL_VERSIONS` 是另一份且两者之间无任何断言。在 `agent/test/prompt.test.ts` 增加一条回归：
从 prompt 文本中抽取全部 `familygraph.*` 工具名，断言每一个都存在于 `TOOL_VERSIONS`。

助手 prompt 文本本身不下发，保持 sidecar 本地（本次不改动其归属）。

### R6 规范同步

更新 `.trellis/spec/backend/steward-child-run.md`：

- §6 协议表 `GET /runs/{id}/context` 行去掉 `steward_prompt_version`。
- §8 标题与正文去掉跨层字面量、版本比对、fail-closed 相关条目，保留「prompt 文本由服务端拥有」
  「`prompt_digest` 覆盖实际发送文本」「sidecar 不得保留本地 fallback」三条。

## Acceptance Criteria

- [ ] `rg -n "STEWARD_PROMPT_VERSION|steward_prompt_version" backend/app backend/tests agent/src agent/test`
      无匹配（`agent/dist/` 为 gitignore 构建产物，重新构建后自然消失）。
- [ ] `agent/src/prompts/` 目录已删除。
- [ ] `steward_instructions` 仍为投影必填字段；sidecar 缺失时仍 fail-closed。
- [ ] `test_steward_child_run_acceptance.py::test_the_sent_prompt_is_the_one_the_digest_describes` 通过。
- [ ] `agent/test/prompt.test.ts` 新增的工具名 fence 通过，且在 prompt 中植入一个不存在的
      `familygraph.` 工具名时会失败。
- [ ] `cd backend && ruff check . && ruff format --check . && mypy app && pytest` 通过。
- [ ] `cd agent && npm test` 通过（含 build/type-check）。

## Constraints

- 不改动 steward prompt 文本内容、`prompt_digest` 计算方式、`prompt_version()`、`_PROMPTS` 结构。
- 不改动助手 prompt 文本，也不改变其归属（仍住 sidecar）。
- 不引入新字段、新常量或新抽象来替代被删除的版本机制。
- 不触碰 `.trellis/tasks/archive/**` 与 `artifacts/`（历史记录与证明产物）。
