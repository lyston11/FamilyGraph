# F 技术设计

## 隔离拓扑

合成fixture → 独立DATA_DIR+真实FastAPI listeners → 真实sidecar+项目锁定Pi → loopback假上游；真实Vite页面通过指定测试proxy访问这套后端。使用专用端口，与launchd管理的8000/8001/8002 SSH隧道分开，先用lsof确认所有监听者。backend/sidecar共享测试service secret，仅环境传入。

普通Provider生产profile门禁继续保留。复用现有测试注入或允许的本地Provider路径，假上游只模拟协议与时序；不能为测试增加生产可用的任意云profile绕过开关。

## 矩阵设计

| 组 | 注入点 | 必需断言 |
| --- | --- | --- |
| A1 无工具/有正文 | SDK首delta与message_end间隔 | 首SDK正文、完整正文、持久化、页面可见分别记录 |
| A2 只读/纯工具/多轮 | 合成工具耗时、第二轮 | tool_call_id/turn关联；空工具消息不熄灭等待，不漏计模型轮 |
| A3 压缩 | 实际SDK阈值/overflow恢复 | manager历史源保持，摘要请求与回答分开，无recent-N截断 |
| A4 排队 | 两个隔离run、worker忙闲 | 实际lease等待与context分开；一次仅一个run，轮询等待可识别 |
| A5 错误 | 400/401/403/429/502、连接/stream断开 | E分类、分层请求数、失败尾段与D分母一致 |
| A6 中断 | queued/生成/工具前取消，heartbeat拒绝 | 新请求与工具停止，终态权威，无旧attempt回写 |
| B1 总截止 | headers/body/chunks/connect/write | C总预算与清理容差，未发送和unknown区分 |
| B2 结果保全 | 第一笔成功第二笔未知，重复恢复 | 独立应用/失败状态/幂等费用，四kind和空结果 |
| UI1 生命周期 | 页面初次打开、提交、完成/失败、刷新 | 真实DOM初帧和过渡，无空泡/重复消息/无限等待 |
| UI2 重连/权限 | SSE断开、续传、撤权、空间切换 | 无越权/旧内容复活，最终正文与引用一致 |

每项记录baseline版本、修复版本、相同输入和注入计划；不强行给所有场景增加同样的sleep。时间判断优先事件屏障/可控时钟，真实socket截止保留窄量化测量与显式调度容差。

## 浏览器与代理证明

SDK→公共事件与gateway→sidecar是两条不同流。假上游按时发body chunk，测gateway是否及时转发；公共SSE只在后端持久化之后发获准事件，另测客户端到达和Vue渲染。记录每跳时间来源，不能把无delta的公共合同当网络缓冲。

跨机器探针可先对合成自建流测试传输，禁止借正常用户会话制造模型调用。时钟用多次RTT范围给误差，不报告虚假的毫秒级跨机精度。代理配置只读，任何改动须回归所属实现设计。

## 产物与失败处理

复用测试目录与脚本，仅补缺格；`research/evidence/` 保存安全JSON与测量说明、必要的合成截图。截图不含真实家庭信息，日志不存token/prompt/thinking。

发现缺陷记录task归属与可复现断言，串行修复后只重跑受影响格加必要累计检查。机器争用flake不能通过弱化业务边界解决；环境阻塞保持blocked。测试服务退出与资源清理证实完成后才移除临时DATA_DIR。
