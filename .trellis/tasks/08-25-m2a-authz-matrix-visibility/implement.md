# m2a 实施计划

## Checklist

1. [ ] services/visibility.py：classify/user_payload_for/MASKED —— 单测
2. [ ] GET /users/{id} 接入 classify（full/summary/invisible→404）
3. [ ] graph.py family/clan scope 接入节点级过滤 + summary 节点裁剪
4. [ ] tests/test_authz_matrix.py：三家庭 fixture（A—elder→B、B—peer→C、D 独立）逐行断言矩阵
5. [ ] 门禁全绿

## 验证命令

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app/ && .venv/bin/python -m pytest -q
```

## 回滚点

- visibility.py 新文件 + 两处路由接入，revert 即回滚
