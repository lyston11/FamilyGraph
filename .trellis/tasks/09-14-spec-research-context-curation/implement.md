# 实施计划

1. 检查根 `AGENTS.md`、`.trellis/spec/**/index.md` 和叶文档，记录混合职责与重复正文。
2. 写入根级 Context Curation 摘要；新增完整 `.trellis/spec/guides/context-curation.md` 并从 guides index 建立链接。
3. 仅在有明确独立适用合同时拆分 Spec 叶文档，更新对应索引；保留历史资料的非权威兼容边界。
4. 校验现有 Research 目录与任务 JSONL 引用，必要时建立最小 summary/router 结构；不迁移 adoption marker 前的任务档案。
5. 为当前任务填写 `implement.jsonl` 与 `check.jsonl`，只引用独立 Spec 叶或 Research summary。
6. 运行文档结构检查、任务 validate，以及与上下文构建相关的定向测试；检查 git diff，确认没有触碰其他任务和 workspace 内容。
7. 在任务 worktree 中提交改动；集成与归档由主检出按项目流程完成。

## 验证命令

```bash
python3 ./.trellis/scripts/task.py validate .trellis/tasks/09-14-spec-research-context-curation
python3 -m compileall -q .trellis/scripts
# 若存在相关测试，运行上下文/任务 manifest 的定向测试
```

## 回滚点

- 先提交治理文档与索引，再提交拆分结果，便于单独回退拆分而保留合同。
- 若索引拆分导致现有 discovery 失败，恢复对应索引路由，保留新合同文档并重新缩小拆分范围。
