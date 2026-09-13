# 修复 familygraph-api 重启 bind 竞态

## Goal

`systemctl --user restart familygraph-api` 后，旧进程端口释放慢，新进程在
`serve.py::_validate_bind_plan` 处反复失败，systemd 以 `RestartSec=5` 循环重启，
约 1 分钟后才有一个实例成功绑定。重启期间三个 listener 全部不可用，且失败窗口
不可预期。需让重启要么快速干净地完成，要么在进程内对 bind 做有限退避重试。

## 过程记录（2026-09-12 服务器实测）

- 操作：为启用 `STEWARD_ASSIST_*` 平台开关（见
  `09-13-steward-assist-platform-switch-admin`）而 `systemctl --user restart
  familygraph-api`。
- 现象：restart 后 `is-active` 长时间停在 `activating`；`journalctl` 时间线为
  `Started → Main process exited, code=exited, status=1/FAILURE
  （RuntimeError: public listener 无法绑定 127.0.0.1:8000（[Errno 98] Address
  already in use）） → Scheduled restart job, restart counter is at N`，N 从 1 计到
  12；期间 `ss -tln` 三个端口无监听者但 bind 仍失败（TIME_WAIT/释放延迟）。
- 约 15:54:58（首次 restart 后约 1 分钟）第 12 次重启成功绑定并稳定运行
  （Main PID 341396），无残留问题，隧道两侧 `/api/health`、`/admin-api/health`
  均 200。
- 推断根因：uvicorn 优雅关闭期间 SSE（agent 事件流）与 in-flight 请求拖延进程退出，
  旧 socket 释放慢；`serve.py` bind 预检失败即 `raise` 退出，没有退避重试；systemd
  固定 5s 间隔重启放大了竞态窗口。
- 本地开发同样出现过该报错：`backend.log` 中曾有 `0.0.0.0:8000 Address already in
  use`（本机端口被占用场景），说明同一预检路径在端口占用时会直接 fail-fast。

## Requirements

- 优雅关闭加速：uvicorn shutdown 时主动关闭长连接（SSE 流、keep-alive），评估
  `timeout_graceful_shutdown` 等参数；目标是 restart 时旧进程在秒级内释放端口。
- bind 预检韧性：`_validate_bind_plan` 对 `EADDRINUSE` 做有限退避重试（如 3 次 ×
  1-2s），仅在超窗后仍失败才报错退出；「端口被他人占用」与「旧进程尚未退出」的
  错误信息需可区分。
- systemd 侧核对：确认 `TimeoutStopSec` 与 `KillSignal`/`KillMode` 与新的关闭行为
  匹配（必要时在 `install-server-automation.sh` 中固化）。
- 不改变三 listener 端口计划与 fail-closed 启动校验语义（弱密钥、通配地址约束不变）。

## Acceptance Criteria

- [x] 服务器实测 `systemctl --user restart familygraph-api` 在数秒内完成且健康检查
      通过，journal 中不再出现 bind 竞态循环。
- [x] 单元/集成测试覆盖：优雅关闭后端口及时可复绑；bind 预检退避重试路径与最终失败路径。
- [x] `install-server-automation.sh` 与实际 unit 配置一致（幂等重跑无漂移）。
- [x] 运维文档记录重启行为与排障口径。

## 实现记录（2026-09-13）

提交 27dd52c `fix(api): absorb restart bind race with retrying probe and bounded graceful shutdown`：

- `backend/app/serve.py`：
  - 三个 uvicorn.Config 增加 `timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS`
    （env 可覆盖，默认 5s），优雅停机到点强断 SSE 长流/慢请求，端口秒级释放；
  - bind 预检拆出 `_probe_bind` + `_ensure_ports_bindable`：probe 设置 SO_REUSEADDR
    （与 asyncio 真实绑定语义一致，TIME_WAIT 残留不再误判为占用），EADDRINUSE 按
    1s/1.5s/2s 退避重试（总窗约 4.5s），重试逐次告警"疑似旧实例退出中"；重试穷尽
    后报错点明两种可能原因并给出 `lsof -iTCP:<port>` 定位口径。
- `scripts/install-server-automation.sh`：unit 增加 `TimeoutStopSec=15`（与 5s grace
  对齐，超时 SIGKILL 兜底）；服务器已幂等重装，`TimeoutStopUSec=15s` 生效。
- `backend/tests/test_serve_bind_plan.py`：6 个单测（退避后成功/立即成功不退避/穷尽
  报错口径/SO_REUSEADDR 语义/活跃 LISTEN 仍拒绝/无监听端口可复绑），在服务器干净树
  上 6 passed。
- `README.md`：启动方式章节下补"重启行为与排障口径"。

服务器实测（lyston）：连续两轮 restart 分别 4.5s / 2.6s 达到 health 200；窗口内
journal `无法绑定` 报错 0 条；此前基线为 restart 循环 12 次、约 1 分钟。

备注：验证当日本地工作区有并发的 assistant-session-management 在途改动（迁移链
尚未稳定），本地全量 pytest 暂不可运行；本任务单测改在服务器干净树上执行通过，
本地全量待该工作线合入后由其自行门禁覆盖。

## Notes

- 触发背景与服务器环境详见同日任务 `09-13-sidecar-server-deployment`、
  `09-13-steward-assist-platform-switch-admin` 的过程记录。
