# E3 证据：真实模型回路（Provider 代理唯一 egress）运行记录

> 任务：`08-29-v2-agent-architecture-release-closure`
> 日期：2026-08-29；基线 commit `6f93f6f`（工作树未提交改动，即本任务全部整改代码）
> 执行环境：macOS (darwin 25.2.0 arm64)；backend Python 3.12.12（`backend/.venv`）；
> sidecar Node 24.14.1（`agent/dist`）。

## 1. 结论

**真实模型回路 E2E 走通**：浏览器 API → AgentRun 队列 → sidecar（Pi SDK）lease →
internal context（仅代理路径，无凭据）→ **ProviderGateway 代理端点**（服务端解密转发）
→ abrdns `GLM-5.2` → 工具调用（`familygraph.get_self_context`）→ 二轮模型调用 →
`succeeded` 结算 + 真实中文回答正文。Run 4 全程约 18 秒。

## 2. 环境与命令（可复现）

```bash
# 0) 空库迁移链（20 个迁移，空 DATA_DIR 自举）
DATA_DIR=/tmp/fg-e3-data SECRET_KEY=$S1 AGENT_SERVICE_SECRET=$S2 AGENT_RUNTIME_ENABLED=1 \
  backend/.venv/bin/alembic upgrade head    # → 0019_web_approved_use_case 为终点

# 1) 双 listener（public 18000 / internal 18001；internal 默认绑 127.0.0.1）
DATA_DIR=... SECRET_KEY=$S1 AGENT_SERVICE_SECRET=$S2 AGENT_RUNTIME_ENABLED=1 \
  STEWARD_ENABLED=1 STEWARD_WORKER_ENABLED=1 \
  PUBLIC_API_PORT=18000 INTERNAL_AGENT_API_PORT=18001 \
  backend/.venv/bin/python -m app.serve

# 2) sidecar（仅指向 api，不持任何 Provider 凭据）
FG_API_BASE_URL=http://127.0.0.1:18000 FG_INTERNAL_API_BASE_URL=http://127.0.0.1:18001 \
  AGENT_SERVICE_SECRET=$S2 AGENT_RUNTIME_ENABLED=1 HEALTH_PORT=18080 node dist/main.js

# 3) API 流（curl）：bootstrap → login → PUT /me/pin → POST /api/spaces →
#    POST /api/admin/agent/providers（secret 服务端 secretbox 加密）→
#    PUT /api/admin/agent/spaces/1/provider-settings → POST /api/agent/sessions →
#    POST /api/agent/sessions/{id}/messages（Idempotency-Key 头）→
#    GET /api/agent/runs/{id}（轮询）→ GET /api/agent/runs/{id}/events（SSE）
```

## 3. Provider 配置（用户指定）

- 上游：abrdns（`https://new-api.abrdns.com/v1`，openai-completions；来自用户 pi 配置
  `~/.pi/agent/models.json` 的 `abrdns` profile）
- 模型：**`GLM-5.2`（精确大小写）**——小写 `glm-5.2` 在该 new-api 网关下报
  `model_not_found / No available channel`（503），精确大小写 200（2.5s 冒烟）
- guga-copy / `deepseek-v4-pro-0813`（备选）：上游也 200 可用，但重载荷（11 工具 +
  reasoning）下间歇 `503 service_busy`，本轮未出成功正文

## 4. Run 4 证据（session 3，space 1）

### 4.1 运行结果

```
{"id":4,"session_id":3,"kind":"assistant","status":"succeeded","attempt":1,
 "max_attempts":3,"error_code":null,
 "created_at":"2026-08-29T11:02:06.9Z","settled_at":"2026-08-29T11:02:21.3Z"}
```

### 4.2 事件序列（SSE，11 条）

```
message.user_added  {"text": "请用一句话说明家庭图谱是什么。"}
run.started         {}
turn.started        {}
message.assistant_added {"text": "让我先查看当前空间的概况，以便为您准确说明。"}
tool.execution.started  {"tool_name": "familygraph.get_self_context", "tool_version": 1}
tool.execution.completed {"tool_name": "familygraph.get_self_context", "is_error": false}
turn.completed      {}
turn.started        {}
message.assistant_added {"text": "家庭图谱（FamilyGraph）是一个以您本人为起点、…"}
turn.completed      {}
run.settled         {"status": "succeeded"}
```

### 4.3 助手最终正文（真实模型输出）

> 家庭图谱（FamilyGraph）是一个以您本人为起点、记录家庭成员之间结构化亲属关系的
> 数字化图谱，它通过确定性的关系路径将空间内的人物连接起来，并支持亲属称谓的解析、
> 查询与个性化记录。您当前所在的「E3 家谱空间」正是这样一个空间，目前以您
> （e3-operator）为中心，已确认身份并关联了可见的家庭成员。

（模型正确引用了工具返回的空间名与用户名——工具结果确实进入模型上下文。）

### 4.4 Provider 代理 egress 审计（`audit_log`，action=agent_provider_egress）

```
{"provider_id":1,"status":"succeeded","upstream_status":200,"bytes_read":5574}    # 第一轮（含工具决策）
{"provider_id":1,"status":"succeeded","upstream_status":200,"bytes_read":16359}   # 第二轮（工具结果回填）
```

两次模型调用均经 internal 代理端点（`POST /internal/agent/runs/4/provider/chat/completions`）
服务端解密转发；`api_key` 从未离开 api 进程，sidecar 仅持 run token。

### 4.5 fail-closed 负向证据（同一环境的先前 run）

- run 1（guga-copy/deepseek-v4-pro-0813，重载荷）：上游间歇 503 → sidecar 重试耗尽 →
  `failed / PROVIDER_STREAM_ERROR`（明确失败终态，未伪造成功）
- run 2/3（body 捕获阶段）：上游 400 → 同样 failed
- internal listener 无 token 访问：401；公开 listener 上 `/internal/*` 一律 404（curl 实测）

## 5. 本轮暴露并修复的两个实现缺陷

1. **代理未转发 Content-Type**：httpx 对原始字节 body 不自动设 Content-Type，中转网关
   强校验该头 → 上游 400。修复：代理透传原请求的 `Content-Type`/`Accept`/`User-Agent`
   三个无副作用头（`provider_proxy.stream_provider_response`），其余头不转发。
2. **sidecar 模型调用无重试**：pi-ai `retryProviderRequest` 默认 maxRetries=0，中转型
   上游间歇 503 直接判死。修复：新增 `AGENT_PROVIDER_STREAM_MAX_RETRIES`（默认 5）/
   `AGENT_PROVIDER_STREAM_MAX_RETRY_DELAY_MS`（默认 20s），在 `guardedStreamSimple`
   注入 pi-ai 重试（5xx/408/409/429 指数退避，可被 abort 打断）。

## 6. 未覆盖（诚实边界）

- 本证据为本机（127.0.0.1 双 listener）部署形态；compose 空库部署 + internal 网络
  负向连通性（web/宿主不可达 8001）需 `docker compose up` 真实栈复验（用户运行中的
  OrbStack 栈为旧镜像，重建需其确认）。
- 375px/桌面人工 UI 走查、第二卷恢复、FTS/SSE 断线重连等其余 E3 项仍待执行。
- guga-copy/deepseek-v4-pro-0813 的成功正文因上游间歇 503 暂缺（上游可用时同流程可
  复跑；run 1 的 failed 终态即该情况的真实记录）。
