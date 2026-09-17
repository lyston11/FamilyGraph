# D 技术设计

## 数据所有权

| 数据 | 权威采样点 | 限制 |
| --- | --- | --- |
| 入队/首次 lease | backend 同一执行 attempt 的状态转换 | 不用可续期 lease_expires_at，不用 SDK agent_start |
| context/session/工具/压缩/轮次 | sidecar 进程内 monotonic duration | producer 标记、attempt+turn 关联，不跨进程相减 |
| 上游请求开始/结束/失败 | ProviderGateway 的请求生命周期 | E 保证各结果路径产审计；响应头到达不等于首正文 |
| 首 SDK 正文/完整消息 | SDK 安全文本事件的时间元数据 | 仅长度/时刻，不保存正文或 thinking；不改变公开消息发布 |
| 入库/结算 | backend UTC | 仅描述持久化；不作为源事件时刻 |
| SSE 到达/渲染 | F 中的浏览器 performance 时钟 | 与服务端关联需标 RTT/偏差；不混减不同 monotonic 时钟 |

优先复用已注册内部 schema 与审计；确实缺失的计时可以增最小可选字段，不再受错误的“禁止新增任何阶段字段”结论约束。安全/授权条款继续有效。后端 schema 是合同真源，不能把 SDK 原始事件直接传给前端。

## 最小合同与迁移技术门

实施前制作字段表：名称、单位、source/version、nullable、run_id、attempt、turn_id、request_id/重试层、上下限、保存点、历史读取规则。第一 lease 与后续 attempt 必须分开，不能一个时间覆盖整 run 重试。

首选为既有受保护观测存储增加有界元数据，禁止把内部计时塞进公开正文 payload。选择扩展审计或 nullable 字段，要先比对查询成本和重复写入；不同时建立两套真源。若新增 endpoint/type，注册表与 extra-forbid schema 同批演进，旧 sidecar 缺字段允许未知，新 sidecar 对旧后端需协商/按 G 顺序发布。

源 duration 不改变 created_at 的意义。未来新数据可构成版本化精确指标；旧 `assistant_phases` 保留兼容解释或安全弃用提示，不静默改变同名字段为另一种统计口径。现有后台无消费的结论也应在修改时再次检索确认。

## 聚合规则

从 run 总集合 LEFT JOIN 事件/审计，不能从 event 集合反推 run 分母。窗口明确以入队/终态/请求时间中哪一项选取；跨窗口运行和仍活跃运行单列，不混合不同分母。

新请求按 run/attempt/turn/request 与实际重试调度关联。仅一次终端失败不是“重试”；失败后新请求需要区分同轮 retry 与新轮正常调用。历史缺轮次的数据给失败数和持久失败间隔下界，归属不能确定就标 unknown；不能从两个失败相邻推断真实重试次数。

关闭尾部失败段以保留可知下界；retry existence/count 不依赖非零 duration。单失败后成功可以有 retry count 而 duration unknown。失败但无审计的历史行不能当零失败；D 暴露覆盖率，E 修生产路径。

## 验证与回退

真实 SDK+fake provider 验证工具轮/压缩；真实 backend append 验证批量时差；跨端联调验证 schema/权限，不能只 mock 两端同一错误形状。常规 GET 不发模型、不改业务状态。

新增 nullable 字段先在隔离库 upgrade，旧行保持 null；降级前 refusal guards 先于 DDL。回退时禁用新采样/回到兼容读，不能销毁历史审计。F/G 分别负责浏览器与真实版本，不用 D 的单元测试替代。
