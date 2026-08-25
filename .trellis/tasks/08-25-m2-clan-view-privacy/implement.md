# M2 实施计划（父级编排）

## 顺序

1. [ ] m2a 授权矩阵（先 fixture 后实现后测试）
2. [ ] m2b 家族视图 ∥ m2c 申请流（均依赖 m2a，可并行）

## 审查门禁

- m2a 后：**安全审查门禁**——矩阵每行有对应测试才允许 m2b/m2c 开工
- 收口：trellis-check + 越权手工渗透清单执行

## 回滚点

- visibility.py 引入采用"先并行运行对比再切换"策略：新旧返回 diff 日志观察一轮后切正式路径

## 验证命令

```bash
cd backend && pytest tests/test_authz_matrix.py -v
```
