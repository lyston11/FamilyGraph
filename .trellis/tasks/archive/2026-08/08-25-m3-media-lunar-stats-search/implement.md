# M3 实施计划（父级编排）

## 顺序

1. [ ] 矩阵补三行测试（附件/统计/搜索）——m2a 的增量 PR
2. [ ] m3a ∥ m3b ∥ m3c ∥ m3d（互不依赖，可并行开发串行合入）

## 审查门禁

- 每个子任务合入前其对应矩阵行测试必须已存在且通过

## 回滚点

- 四子任务相互独立，任一回滚不影响其余；Pillow 引入独立 commit 便于剥离

## 验证命令

```bash
cd backend && pytest tests/test_attachments*.py tests/test_stats*.py tests/test_search*.py tests/test_lunar*.py
```
