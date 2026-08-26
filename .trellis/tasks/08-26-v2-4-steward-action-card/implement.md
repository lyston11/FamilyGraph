# V2.4 Steward 与 ActionCard 实施计划

- [ ] 定义 DomainEvent taxonomy、BehaviorProjection、Steward trigger/checkpoint 和 ActionCard schema/FSM。
- [ ] 实现按 space 租赁的 Steward Job 与 dirty cursor 幂等消费。
- [ ] 接 V2.3 DerivedFact/TermRegistry 重算、冲突/缺失 detector。
- [ ] 实现推荐资格矩阵、dedupe/evidence version/cooldown/supersede。
- [ ] 实现 card list/view/dismiss/accept/execute API；执行统一调用 Foundation domain command。
- [ ] 明确“接受卡片”和“发送申请”分步 UX，加入隐私影响确认。
- [ ] 在 Assistant message 与空间 Inbox 接同一 ActionCard component/store。
- [ ] 用跨空间对抗 fixture 验证 Steward 上下文与推荐矩阵。

## 验证

```bash
cd backend && pytest
cd backend && mypy app
cd agent && npm run type-check && npm run lint && npm test
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

故障测试：重复事件、worker crash after card insert、并发 accept、证据变更、membership revoke、expired card、同卡两个 UI 入口操作。

## 回滚

Steward scheduler、每类卡片和 UI Inbox 独立 feature flag；关闭后保留 DomainEvent 真源，DerivedFact 可按需计算，未执行卡片置 superseded 而非删除历史。
