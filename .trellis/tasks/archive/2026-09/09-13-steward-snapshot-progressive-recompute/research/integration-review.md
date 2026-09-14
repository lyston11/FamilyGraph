# 串行集成独立复核

2026-09-14。针对主线自动称谓、Memory/RAG 与本任务 staged publication 的接合；与主线接合前的 `final-review.md` 分开记录。以下为代码与回归合同审查，完整门禁和实测另见 `integration-acceptance.md`。

## 迁移与责任保留

- 模型合并保留 generation/publication/delivery 和 TermProjection/TermSuppression 两侧字段。
- 0048 连接两个既有父迁移，不改旧 revision。两种升级次序保留真实用户、账号、事实及待交付责任；SQLite batch 重建丢失的三个父级 inferred triggers 幂等恢复。
- 深层降级在任何 DDL 前检查 Memory provenance、RAG digest/context/历史 chunks/失效原因和 Steward 未完成待办。直接降级被拒绝后 schema 和版本均保持；单纯 unmerge 可无损退回两个父版本。

## 输入、批次与恢复

- `valid_delivery_generation` 要求当前 publication、sealed/published 状态、结构/配置和时间仍有效；准备读取一致授权快照，写回再次核验完整版本、账号/root、active membership 和租约。
- 仅术语交付可承接自身 presentation 增量，普通交付仍受完整栅栏约束。GC 在发现和退休事务中共用 `valid_source`，避免自身术语写回后误删剩余责任。
- 每批最多 8 个目标，缓存最多 16 个 viewer；缓存仅在 receipt 成功提交后确认。真实回滚版本被另一次 suppression 更新复用的 ABA 回归要求重新读快照、不得恢复被拒绝称谓。
- 无效/无改善的 baseline 不预建空自动投影；旧测试允许 projection 不存在，但输入变更的 superseded、失租旧执行器不得写回、恢复成功的真实称谓及幂等断言保留。

## 结构复用与读取

- `search_config_fingerprint` 只包含 snapshot/policy/algorithm/depth/path limit；完整配置继续包含称谓规则及模型开关。称谓配置变化保留路径和搜索预算，同时重绘全部受影响展示。
- 结构配置变化拒绝复用并更换搜索预算。批次保存和发布仍校验完整 generation/input/owner/attempt/deadline。
- 快速整视图复用依赖完整 config，而结构路径复用要求 search_config。此次完整配置 hash 格式一起更新，实际旧格式必然失效；不扩大为“任意手工删掉 search_config 字段的记录都必然冷算”。
- 显式按 pair 关系 API 仍使用统一计算原语。普通 `build_relation_presentation` 缺少确认称谓时只读取 current publication，防止呈现组合器重新同步搜索整图；发布前和失效后的中性返回断言未放宽。
- 候选建议携带 fact_type，使用自身有向单步关系。确认缺省回退修复未修改此分支，第三方方向、个人用词隔离、继养和监护亚型的主线用例继续保留。

## 前端

- 输入失效清除过期内容、ETag、显示有效期和在途请求；同一身份保留非敏感位置与视口，身份变化完整清理。
- 称谓面板随 generation 或旧协议 view_version 更新，不随同代进度 revision 反复请求；骨架更新后的对象引用、选中和偏好入口保持。

独立复核：`generation_design_review`（模型/迁移/栅栏/GC/配置拆分）、`pure_compute`（称谓语义及发布消费者修复）、`progressive_frontend_audit`（前端协议与布局）；主线程沿关键原文抽查、处理取舍并统一运行验证。所审范围无未处理阻塞；生产环境尚未部署或验收。


## 合并后性能整改复核

`generation_design_review` 独立审查相对 `3cb4550` 的热视图复制和领取索引增量，未发现阻塞：`_latest_view` 的骨架列 raiseload 后所有调用只读元数据；`_reuse_view` 保留完整 generation/lease/input/time 以及旧 viewer/root/ready 校验，INSERT…SELECT 提供全部必要非空字段，单行插入返回 ID 后才推进完成计数，`coalesce(result_view_id,id)` 保持原目标来源。连续两次热代回归在整个 writer 到 commit 期间禁止 JSON 编解码。新增 `(status,id)` 的模型/0048 迁移一致，降级仍先做父层拒绝预检，再撤销本层索引。

领取索引诊断原件为 `steward-delivery-query-plan.py` / `.json`：隔离内存中的同形合成队列，非真实家族计算。5401 项全局查询中位数 8.951 → 0.0028ms，消除临时排序；带 generation_id 查询原本约 0.0032ms。因此只把它作为索引作用的证据，不据此归因真实 hot 的 561.596ms 隐式窗口。窗口包含取锁等待；既有两次交付短写、64 项 drain 也不能在没有同事务证据时被指定为唯一原因。


随后独立复核 `steward_demand.py`、`steward_views.py` 与 `test_steward_demand_coalescing.py`，亦无阻塞。授权先于缓存、真实读快照和 input/time 校验保留；running 覆盖要求未消费需求 revision、viewer/root、job 状态、owner/attempt/deadline 与最高水位全部满足。published 按正式指针与有效 ready 视图保留原 satisfied 语义，不被自身完成事件再次触发；未覆盖和显式 focus/retry 均回到原 writer/CAS 与 launch_due。窄列保留所有 target 展示字段，主替路径仍与授权骨架证据逐步比对。新增 21 项及既有精选 11 项已通过；包括另一 WAL 连接真实持写锁时只读完成、15 类非短路情形的 durable revision/cursor 与唤醒、跨连接撤权，以及不可解码搜索缓存与篡改路径证据的双向断言。主线程直接审读关键代码和测试，独立审查不替代完整门禁或性能实测。


## 连续短写公平性与时间栅栏

独立复核 `steward_write_budget` 和接入点：23 个 writer 块及 181 个可解析下游函数中未找到合法嵌套写入或反向等待 runtime 锁；delivery/assist 复用当前 Session，失败记录在原事务退出后进入，heartbeat join 发生在发布或失败结算退出后。FIFO 在 Session/BEGIN 前排队，提交、回滚与 Session 关闭后释放，排队异常删除票据，同线程嵌套立即拒绝，Engine 注册锁不跨越等待。初审发现 heartbeat 在排队前采样 now，现已移到实际入事务之后；新增受控时钟回归分别覆盖 lease 和 generation 到期且期限不被改写。其他生产 fence 未发现同类排队前时间比较。该预算只约束同进程/Engine 的本入口，不能保证心跳必在 TTL 内获准或把单笔事务限制为 50ms；跨过期限按原 fence 拒绝。
