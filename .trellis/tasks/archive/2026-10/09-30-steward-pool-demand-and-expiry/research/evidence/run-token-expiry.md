# 根因确证：run token 10 分钟硬过期，而租约只签发一次

## 结论

run 232 与 run 244 的 `expired` **同一个原因**，且与「上游」「事件循环」「连接池」
都无关：

```
AGENT_RUN_TOKEN_TTL_SECONDS     = 600   （10 分钟）
AGENT_RUN_TOKEN_TTL_SECONDS_MAX = 600   （上限，签发时 min 截断）

run token 只在**租约时签发一次**（internal_agent.py:404 / :463），
心跳请求复用同一个 token。任何存活超过 600 秒的 run，
其心跳必然拿到 401（token 过期）。
```

sidecar 的 `startHeartbeat` 把 `[401, 403, 409, 410]` 视为租约失效
（`worker.ts:304`），立即 `markLeaseLost` → abort → run 被判 `expired`。

## 实测吻合（两个独立样本）

| run | 租约/token 签发 | token 过期（+600s） | 401 出现时刻 | 偏差 |
|---|---|---|---|---|
| 232 | 12:56:09 | 13:06:09 | **13:06:17** | +8s |
| 244 | 14:55:38 | 15:05:38 | **15:05:56** | +18s |

两次心跳序列都以 200 结束、以 401 收尾：

```
run 244:
  15:05:16  200
  15:05:36  200     ← token 仍有效（15:05:38 到期）
  15:05:56  401     ← token 已过期 → lease lost → abort
```

对照：run 243（存活 2 分钟）无 401，正常 `succeeded`。

## 为什么先前两次都误判

- **run 232**：我归因「上游 `stream_interrupted`」。错误——该 run 的 egress 是
  `upstream_status=200`、`succeeded`；`stream_interrupted` 是兜底分支。
- **run 244**：我归因「心跳停滞 / 事件循环阻塞」。错误——心跳有 22 次且**全部 200**
  （除最后一次 401），间隔稳定；停滞修复后仍 expired，正是因为 401 与停滞无关。

两次我都只看到「心跳没推进」的表面，没有逐条核对**响应码**。`401` 与 `超时`
在现象上都是「心跳失败」，但成因完全不同。

## 影响面

任何**存活超过 10 分钟**的 run 都会被误判失租：

- steward child run：真实调用常达 3–8 分钟，多轮工具 + 长流时超过 600s 很常见；
- assistant run：同样受影响（同一 token 机制）。

这不是偶发——它是**运行时长**的确定性函数。

## 修复方向

run token 需要随租约续期。候选：

1. **心跳响应回传新 token**：`HeartbeatOut` 增加可选 `run_token` 字段，服务端在
   心跳时重新签发；sidecar 在收到后替换当前 token。加字段是 additive，
   旧客户端忽略未知字段。
2. **放宽 TTL**：`AGENT_RUN_TOKEN_TTL_SECONDS_MAX` 提高到覆盖最长 run。
   简单，但 token 有效期与租约语义脱钩——租约本身有续期，token 却不续，
   是设计缺口而非配置问题。

**倾向 1**：租约已经续期，token 也应续期；仅调大 TTL 只是把问题推迟到更长的 run。

**不得**用「缩短 run」或「放宽 sidecar 对 401 的处理」掩盖——401 代表凭证过期，
sidecar 按失租处理是正确的。

## 证据边界

- 已用两个独立样本（run 232、244）证明 401 出现在 token 到期后 8–18 秒内。
- 未逐条核对全部历史 expired run 是否都由 401 造成；但从机制看，
  任何 >600s 的 run 都会命中。
- 未测量真实 steward run 的时长分布（用以估计受影响比例）。
