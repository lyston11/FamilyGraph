# 双 Agent 延迟研究路由

- 已确认：管家把整批调用结果留在内存、循环结束才统一审计，失租即返回，恢复器只能把 in_flight 记为 unknown（结果可能已返回却未保存）；含 unknown 的批次会优先标 failed，已成功产物不应用；terminology 同语义 unknown 禁止重发且判断先于 request_hash；助手公共事件只转发 message_end 正文、忽略 delta。
- 已否决（作为根因或做法）：把“/models 401 但可达”当作模型慢的证据；用 admin 延迟接口的 run 总时长或续期后的 lease_expires_at 当作首字/首次租赁；把首心跳、控制事件、工具 turn 当正文首字；用关闭自动压缩 / recent-N 截断 / 投影层再摘要来提速；把历史 09-17 两笔 unknown 判为“上游必然没返回”。
- 决策：先做分段基线与归因，再只对已证实的程序性等待做最小修复；管家优先修“逐调用保全 + 混合批次安全消费 + 总截止/收尾预算”。unknown 不自动重发保持不变。
- 阻塞产品问题：无（助手若确需逐字显示/换模型/降推理档位/未知重试，属另行评审的选项，不阻塞本轮规划）。
- [结论与可复用约束](latency-summary.md)
- [本轮源码与只读基线证据](evidence/latency-baseline-2026-09-17.md)：含 steward_assist/events/config/admin 延迟接口定位与 09-17 归档记录纠正。
