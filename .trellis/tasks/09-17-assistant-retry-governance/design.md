# E 技术设计

## 网关与 SDK 边界

调用链：sidecar session → pi-ai request retry → internal ProviderGateway → 当前绑定上游。网关仍是唯一 egress；不能让 sidecar 得到真正 api_key/base_url，也不能直接透传上游原始错误体。

优先利用 SDK 已支持的安全重试提示（例如受验证的 retry header），或站内稳定机器码映射，保持 public API 兼容。不能盲目透传上游任意 headers；尤其不能将上游401混同为 run-token认证失败而触发错误的 lease loss。实际方案须以锁定 SDK 的消费行为测试选择，在本设计补上精确状态/header映射后再改协议。

## 分类提案

| 来源 | 安全类别 | 推荐行为（待合同冻结） |
| --- | --- | --- |
| 网关前 run取消/失租/权限失败 | local_rejected | 零上游请求，停止执行，不当上游重试 |
| 上游400/401/403、确定性参数或模型错误 | upstream_non_retryable | 泛化安全报错，不重试；保留内部状态码审计 |
| 上游408/429/5xx、可证明暂时的409 | upstream_transient | 依批准的有限策略；409不能无条件等同所有业务冲突 |
| connect/DNS失败 | transport_failure | 审计已尝试及发送确定性；不声称上游必然未处理 |
| 已开始流后中断/超时 | stream_interrupted | 标可能已处理，遵循既有助手恢复与预算；不能借此给管家unknown重放 |
| 正常EOF且SDK合法回答 | completed | HTTP成功与答案合法分别记录，禁止将空答案伪装成功 |

`maxRetryDelayMs=20000` 在当前依赖中主要限制上游要求的 Retry-After 等待，不是所有指数退避的通用上限；不要把调小该值当成已证明提速。

## 两层预算

先通过现有 `session.ts` 注入点读取实际 request retry 参数与 SettingsManager session retry 默认。测试分别触发请求前失败、流中失败、session重试、overflow自动压缩，记录请求层attempt和session重试，不把压缩请求当生成轮。

选项：维持两层但共享可解释预算；或让一层拥有同类网络重试而另一层只做语义恢复。优先避免相同错误无意重复消耗，但暂不选降低总次数的数值。必须给出最坏请求数和等待总量，取消能中断各层等待；“5+3”或“6×4”只能用于满足对应触发前提的推导，不能当线上事实。

## 审计生命周期

以 D 定义的 request identity 关联。进入真实出站时登记安全开始，所有正常/异常/finally分支以一次终态结算；流结束审计不得仍靠只有成功消费才进入的分支。记录 status、安全错误码、上游码、字节数、时长与发送确定性，不记录 error原文、response/prompt或Authorization。若为取消后审计使用新Session，应遵循既有内部安全审计边界，不允许旧执行者写业务结果。

## 兼容与回退

后端 schema 与 sidecar 识别同批验证，G 按兼容顺序发布。旧客户端不理解新分类时必须保持fail-closed，不能导致无限retry。审计新增字段nullable，不给旧数据回填。回退不删除审计、未知计费或改已有 run终态；分类与消费者的配套变化一起回退。
