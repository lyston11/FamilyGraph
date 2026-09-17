# 2026-09-17 后台刷新修复与 terminology 部署启用验收

## 1. 管理员会话恢复竞态（AC1）

复现（修复前，main@5a2d01f）：硬刷新受保护页时守卫看到 `restoring=true` 跳过等待 → 按未登录跳 `/login`；恢复随后成功也不再重算目标路由。启动恢复与 401 重试各发一笔轮换。

修复 commit `a5094ba`（合入 `66edd59`）：

| 文件 | 改动 |
| --- | --- |
| `system-admin-frontend/src/stores/auth.ts` | `runRefresh()` 成为启动恢复/守卫/401 唯一轮换实现；`restoreSession()` 复用同一笔在途（`restoreInFlight`），至多轮换一次 |
| `system-admin-frontend/src/router/index.ts` | 守卫未登录时 `await auth.restoreSession()` 等待在途恢复，不再跳过 |
| `system-admin-frontend/src/main.ts` | `app.use(router)` 前发起唯一恢复；`isReady()` 解析后挂载；首次导航 reject 仍挂载 |

独立核验提出的两项本次改动引入的健壮性回归已一并修复：

- 首次导航 reject（部署后懒加载 chunk 失效）时 `isReady()` reject 导致永挂不载 + 未处理 rejection → `.catch(() => undefined)` 后仍挂载；
- `localStorage` 不可用（隐私模式）时 `readStoredRefreshToken()` 同步抛错 → `restoring` 永久为 true 且入口模块中断 → 读取移入 `requestRefresh()` 的 `try`，转为失败 Promise。

回归证据（`system-admin-frontend`）：

- 红：`git stash push -- src/*`（保留新测试）→ **7 failed / 26 passed**；恢复后 **33 passed**。
- 门禁：`npm run lint`（0）、`npm run type-check`（0）、`npm test`（**16 files / 127 tests passed**）、`npm run build`（✓ 1.69s）。
- 真实浏览器（Chrome/Playwright，5174 dev server，`/admin-api/*` 全量 route mock，未触生产）：

| 场景 | 结果 |
| --- | --- |
| 硬刷新 `/operations`（refresh 延迟） | 留在 `/operations`，渲染后台壳层，`/auth/refresh` 恰好 1 次 |
| refresh 401 失败 | `/login?redirect=/operations`，`fg.admin.refresh_token` 已清空 |
| `password_must_change` 会话刷新 `/operations` | 改派 `/force-change-password` |

## 2. terminology 部署启用（AC3）

只读核对后仅启用部署级开关：

- 备份：`/home/ubuntu/.config/familygraph/familygraph.env.bak-20260917-terminology`（0600，789 bytes）；原子追加唯一键 `STEWARD_ASSIST_TERMINOLOGY=1`（无重复），env 仍 0600。
- 重启 `familygraph-api.service`（pid 3890702）：`active`，运行进程 environ 含该键 1 次，`/api/health` 200、`/admin-api/health` 200。
- 部署 env 加载后生效值（断言 DB 路径为生产 `.../backend/data/db/app.db`，只读会话）：

| 空间 | 平台 terminology | 空间 steward 行 | 有效 terminology |
| --- | --- | --- | --- |
| 2（owner 已授权，`cloud_allowed=1`） | 1 | `enabled=1, assist_terminology=1` | **True** |
| 1 / 3（无 steward 行） | 1 | 无 | **False**（未扩权） |

平台行 `steward_assist_terminology=1`；空间 2 另有独立 `assistant` 行，未被改动。Provider/协议/预算/模型保持不变（liu-dada，`openai-responses`，`gpt-5.6-sol`）。

## 3. 真实模型消费结果（如实记录：启用生效，改善未取得）

启用后正常后台链路自主触发，无人工植入人物或强写投影：

1. 空间 2 产生新 generation `1852`（04:58:09 published，config 版本因开关变化而变，属预期重算）。
2. 04:59:24 登记 terminology 批次 `8`，04:59:25 预留并**真实发送** 2 笔 terminology 调用：

| call | viewer | prompt_chars | 预留 in/out | 终态 | latency | 输出 |
| --- | --- | --- | --- | --- | --- | --- |
| 21 | 1 | 1154 | 1610 / 1000 | `unknown` / `network_unknown` | 未记录 | 无 |
| 22 | 2 | 995 | 1511 / 1000 | `unknown` / `network_unknown` | 未记录 | 无 |

3. 批次 `8` 终态 `failed` / `network_unknown`（05:01:29）；同批 candidate/ranking 记 `skipped` / `lease_lost`。

分项结论（AC6/AC7 口径）：

| 项目 | 结果 |
| --- | --- |
| 部署有效启用 | **是**（见 §2） |
| 真实模型调用已发生 | **是**（2 笔已发送，非 fake） |
| 取得合法输出 | **否**（无 completion、latency 未记录） |
| 自动写回/用户可见改善 | **否**（无 terminology 建议或投影写入；称谓仍为确定性结果） |
| 新增称谓通知 | 无（`term_preference` 不产生通知的合同未变） |

原因判断（证据见上）：两笔调用在发送后未在批次租约墙钟（`lease_until` 05:01:25）内返回，恢复流程把仍为 `in_flight` 的 attempt 收敛为 `unknown`（`steward_assist.py` 租约恢复路径），因此按设计**不重放**、不计为改善。不是 Provider 配置错误：同一时段 `api.liu-dada.com` 可达（`/v1/models` 401，50ms），同批其他 kind 的实际耗时 54–60s（candidate 命中 `STEWARD_ASSIST_TIMEOUT_SECONDS=60` 记 `timeout`）。

**遗留阻塞（不掩盖）**：`terminology_target_retryable` 对 `unknown` 恒返回 False，故这两组 viewer 的目标在当前输入哈希下永久不再发送；只有输入/证据变化带来新 `request_hash` 才会重新尝试。即在既有 60s 单次超时与 120s 批次租约下，推理型 Provider 的慢响应会一次性消耗掉该目标的许可尝试并视为失败终态。

未采取的绕过手段（按 PRD 非目标）：未放宽校验制造"改善"、未重发同输入、未调预算/超时/租约、未伪造 preferred usage、未开启其他空间许可。

## 4. 验证与未运行项

- 目标：`system-admin-frontend` lint / type-check / test（127）/ build 全绿；上文红-绿基线。
- 后端未改源码，未重复全量 pytest/ruff/mypy；未运行 `frontend-api-smoke.sh`（本次无 API/认证合同改动）。
- 服务器只做了只读 DB 探针（显式 `DATA_DIR` 并断言生产库路径）与一次服务重启；未写入验收人物、消息或关系。
- 回滚：仅移除本次新增的 `STEWARD_ASSIST_TERMINOLOGY` 键并重启 API，不使用备份整体覆盖（避免覆盖用户同时段的其他配置更改）；代码回滚不影响正确词表与用户词条。
