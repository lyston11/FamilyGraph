# 记忆候选提取器：执行计划

## 前置

- [x] prd.md / design.md 完成
- [ ] implement.jsonl / check.jsonl 配置 spec 上下文

## 实施步骤

1. [ ] 新建 `backend/app/services/memory_extractor.py`：EXTRACTOR_VERSION、类别规则、rule_detector 纯函数、extract_after_settle 容错包装
2. [ ] `backend/app/services/memory_rag.py`：`MemoryCandidateExtractor._default_detector` 延迟 import 切换到 rule_detector
3. [ ] `backend/app/services/agent_queue.py` `_settle`：succeeded 路径接入 extract_after_settle（try/except 双保险）
4. [ ] 新建 `backend/tests/test_memory_extractor.py`（纯函数 + 集成）
5. [ ] 在既有 settle 测试中追加：MEMORY 关闭时 settle 不受影响、幂等不重复

## 验证命令

```bash
cd backend && ruff check app/services/memory_extractor.py app/services/memory_rag.py app/services/agent_queue.py
cd backend && ruff format --check .
cd backend && mypy app
cd backend && pytest tests/test_memory_extractor.py tests/test_memory_rag_service.py -x -q
# 回归面（agent 队列）：
cd backend && pytest tests/test_agent_queue*.py -x -q 2>/dev/null || pytest -k "settle or agent_queue" -q
```

## 部署（服务器）

```bash
git push origin main
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph && git pull'
ssh ubuntu@lyston 'systemctl --user restart familygraph-api'
# 验证：远端发起一次含「我妈妈生日是3月5号」的助手会话 → memory_candidates 出现 pending
```

## 回滚点

- 接入点单独 commit（agent_queue 改动可独立 revert）
- 提取器模块单独 commit
