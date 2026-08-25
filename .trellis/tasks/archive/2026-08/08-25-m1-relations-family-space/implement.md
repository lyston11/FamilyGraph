# M1 实施计划（父级编排）

## 顺序

1. [ ] m1a 档案与认领（含删除 API）
2. [ ] m1b 关系 FSM ∥ m1c 空间 FSM（可并行，均依赖各自前置）
3. [ ] m1d 三布局收口

## 审查门禁

- m1a 后：身份模型字段与 architecture §1 逐项核对
- m1b/m1c 后：FSM 转换表测试覆盖全部分支
- m1d 后：trellis-check 全量 + 手工旅程（建四类亲人→三布局查看）

## 回滚点

- 各子任务独立分支；m1b 与 m1c 并行时以接口契约为冻结点，任一方延期不阻塞另一方合入（mock 联调）

## 验证命令

```bash
cd backend && pytest tests/test_relations*.py tests/test_spaces*.py tests/test_profile*.py
cd frontend && npm run test -- layout
```
