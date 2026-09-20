# 实施计划：线上发布脚本

## 检查清单

### 阶段 A：实现（任务 worktree）

- [ ] A1 `task.py start`（建分支 + worktree）
- [ ] A2 新增 `scripts/deploy-prod.sh`
- [ ] A3 本地 `bash -n` 语法检查 + `shellcheck`（若可用）
- [ ] A4 提交

### 阶段 B：真实发布验收（服务器 `/home/ubuntu/fg-prod`）

- [ ] B1 记录基线：当前 sha、`alembic_version`、容器状态、公网 health
- [ ] B2 AC1：无参数发布 `origin/main`，观察完整流程与摘要
- [ ] B3 AC6：同一 commit 再执行一次，确认 `no-op release` 且无副作用
- [ ] B4 AC2：在开发 checkout（`~/projects/FamilyGraph`）执行，确认立即拒绝且无改动
- [ ] B5 AC3：指定不存在的 sha 与非 origin/main 祖先的 sha，确认拒绝
- [ ] B6 AC4：构造「镜像内 head ≠ 库版本」的失败场景，确认判定为失败
      （用临时假迁移文件，验证后 `git checkout` 清理，不得提交）
- [ ] B7 AC5：构造「新版本起不来」场景，确认自动回滚 + 容器恢复 healthy
      + 公网 200 + **未执行任何 downgrade**
- [ ] B8 AC7：全流程日志 grep 密钥值，确认零泄漏
- [ ] B9 清理所有验证残留（临时迁移文件、临时脚本、detached HEAD 对齐回 main）

### 阶段 C：文档与收尾

- [ ] C1 更新 `deploy/production/README.md` 的发布章节（AC8）
- [ ] C2 写 `verification.md`（逐条 AC 证据）
- [ ] C3 规范更新（若产生可复用约定）
- [ ] C4 archive + 合并 main + push + 清理 worktree
- [ ] C5 报告未运行的高成本检查

## 关键命令

```bash
# 基线
ssh lyston 'cd ~/fg-prod && git log --oneline -1; \
  docker exec familygraph-prod-api-1 python -c "import sqlite3; \
  print(sqlite3.connect(\"/data/db/app.db\").execute(\"select version_num from alembic_version\").fetchone())"'
curl -s -o /dev/null -w '%{http_code}\n' https://fg.lyston.qzz.io/api/health

# AC1 真实发布
ssh lyston 'cd ~/fg-prod && bash scripts/deploy-prod.sh'

# AC2 错误目录
ssh lyston 'cd ~/projects/FamilyGraph && bash scripts/deploy-prod.sh'; echo "exit=$?"

# AC5 失败回滚：注入必然失败的迁移
ssh lyston 'cd ~/fg-prod && git checkout -q origin/main && \
  printf "%s\n" "def upgrade():" "    raise RuntimeError(\"injected failure\")" \
    "def downgrade():" "    pass" > backend/migrations/versions/zzz_injected.py'
# 注意：这需要 revision 元数据才合法；更简单的注入方式见 B7 执行记录
```

## 风险点与回滚锚

| 节点 | 风险 | 回滚动作 |
|---|---|---|
| B6/B7 注入故障 | 污染线上库或残留文件 | 只注入**迁移文件**（不改库），验证后 `git checkout -- .` + 删文件；注入前先做一次备份 |
| B7 自动回滚 | 误判导致反复重建 | `--no-rollback` 逃生；脚本在任何 downgrade 前停下 |
| 发布本身 | 线上短暂不可用 | 脚本内置回滚；一级回滚 `compose stop` |
| 开发环境 | 误发到开发栈 | B4 专门验收；脚本硬校验目录 |

## 验证边界说明

- B6/B7 的故障注入必须**只影响线上栈**，且验证后线上必须回到可用的 `origin/main`。
- 若故障注入可能导致线上数据风险，则仅做**只读推演**（例如用 `--no-rollback`
  配合人为构造的校验失败），并在 verification.md 中如实标注「未做真实注入」及原因。
- 所有注入产物必须在 C4 之前清理干净，且不得提交进仓库。
