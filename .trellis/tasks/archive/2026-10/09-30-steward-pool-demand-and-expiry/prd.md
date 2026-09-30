# 连接需求超过池上限，且 run 244 修复后仍 expired 原因未定

## Goal

事件循环阻塞已修；剩余两个独立问题：(1) steward core 4 线程 + assist 2 + 心跳/lease 轮询共同争抢 15 条连接，实测慢响应时 4 线程等连接仅 2 在执行；(2) run 244 心跳已稳定 20s 但仍 expired，原因未定位

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.


## 执行结果（2026-09-30）

### 缺陷 1：run token 10 分钟硬过期 —— 已修

`AGENT_RUN_TOKEN_TTL_SECONDS_MAX`=600s，而 token 只在租约时签发一次 → 任何 >600s 的
run 心跳必得 **401** → sidecar 判失租。两个样本吻合（401 出现在到期后 8–18 秒）。

修复：`HeartbeatOut.run_token` 随心跳回传续签 token，sidecar 采用。续签只复制已校验
claims，不扩权；additive 字段。

**部署后 401 心跳 0 次**（修复前每个长 run 必有一次）。

### 缺陷 2：池尖峰导致心跳被拖过期 —— 已定位，未修

run 252（存活 501s）心跳空档 **157s** 后收到 **409**（服务端已判失租）。
409 而非 401，说明 token 正常、是心跳请求本身被拖住致租约过期。

实测：稳态下 6 并发 worker 池峰值仅 6/15、心跳最差 5ms（**不饱和**），
因此池耗尽是**尖峰**而非稳态。峰值来源为 steward core（`execute()` 在
`write_transaction` 内跑完整 job，持有时间长）+ assist + 心跳/lease 轮询叠加。

**收敛方案未定**，候选：缩短 core 事务持有时间 / 为 steward 使用独立池 /
限制 assist 与 core 的并发叠加。需先测出尖峰时的持有者与时长，再决定。

### 关键教训：expired 的两种成因

- **401** = token 过期（本任务已修）
- **409** = 服务端已判失租（如租约被拖过期）

两者表象都是「心跳失败」，成因完全不同。我先后两次把 expired 归因错误
（先「上游 stream_interrupted」，后「事件循环阻塞」），根因都是只看「心跳停了」
而不核对**响应码**。
