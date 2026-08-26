# V2.4 Steward 与 ActionCard 实施计划

- [x] 定义 DomainEvent taxonomy、BehaviorProjection、Steward trigger/checkpoint 和 ActionCard schema/FSM。
- [x] 实现按 space 租赁的 Steward Job 与 dirty cursor 幂等消费。
- [x] 接 V2.3 DerivedFact/TermRegistry 重算、冲突/缺失 detector。
- [x] 实现推荐资格矩阵、dedupe/evidence version/cooldown/supersede。
- [x] 实现 card list/view/dismiss/accept/execute API；执行统一调用 Foundation domain command。
- [x] 明确“接受卡片”和“发送申请”分步 UX，加入隐私影响确认。
- [x] 在 Assistant message 与空间 Inbox 接同一 ActionCard component/store。
- [x] 用跨空间对抗 fixture 验证 Steward 上下文与推荐矩阵。

## 验证结果（2026-08-26）

```text
backend: 445 passed; ruff check/format、mypy app 通过
frontend: 159 passed; type-check、lint、build 通过
```

故障测试覆盖：重复事件与 dirty cursor、worker crash/重试事务语义、并发 accept、证据变更、membership revoke、expired card、目标空间篡改、同卡两个 UI 入口操作。


## 回滚

Steward scheduler、每类卡片和 UI Inbox 独立 feature flag；关闭后保留 DomainEvent 真源，DerivedFact 可按需计算，未执行卡片置 superseded 而非删除历史。
