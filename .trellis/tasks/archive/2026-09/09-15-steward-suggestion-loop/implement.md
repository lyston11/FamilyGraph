# 管家建议闭环：执行计划

> 设计见 `design.md`。**R1（TTL）已按核查结论放弃**，实际范围 = R2/R3 的可见性修复，纯前端。

## 前置

- [x] 核查完成（`design.md` §0 记录了推翻 R1/R2 前提的实测证据）
- [ ] implement.jsonl / check.jsonl 配置 spec 上下文

## 实施步骤

1. [ ] `frontend/src/stores/stewardSuggestions.ts`：`activeForSpace` 兑现其文档承诺
   （`proposed`/`submitted` 才返回）——当前实现返回全部 items，与注释和唯一使用意图都不符
2. [ ] `frontend/src/views/NotificationsView.vue`：
   - 抽出 `openSuggestionById(id)`（从 `openSuggestion(item)` 提取公共体），通知行与建议行共用
   - 新增 `pendingSuggestions` 派生：`activeForSpace(spaceId)` 去掉已在 `sections.verify`
     通知行中出现过的 id（去重）
   - 「待核实」分区渲染建议投影行；空态条件改为「两者皆空」
   - 建议行不标记已读（无通知载体），只提供「查看详情」
3. [ ] 测试：`frontend/src/views/__tests__/notifications.spec.ts` 新增 4 例（见 design §4）；
   `frontend/src/stores/__tests__/stewardSuggestions.spec.ts` 新增 `activeForSpace` 过滤例

## 验证命令

```bash
cd frontend && npm run lint && npm run type-check
cd frontend && npx vitest run src/views/__tests__/notifications.spec.ts \
  src/stores/__tests__/stewardSuggestions.spec.ts src/components/kinship/__tests__/KinshipTermPanel.spec.ts
cd frontend && npm test && npm run build
```

后端零改动，无需跑 pytest；但部署前跑一次 backend 受影响面确认无意外耦合：

```bash
cd backend && pytest tests/test_steward_suggestions*.py -q
```

## 部署（服务器）

```bash
git push origin main
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph && git pull'
# 前端为静态构建产物，按仓库既有部署路径同步 dist（见 scripts/server-sync-code.sh 与既有前端发布流程）
ssh ubuntu@lyston 'systemctl --user restart familygraph-api'   # 仅为保险；后端无改动
```

验证：以生产账号打开 `/notifications`，`section-verify` 出现称谓偏好行（远端 535 条投影对应 1–26 条/人）；
点击「查看详情」→ 弹层显示「称谓偏好 / 无需逐条处理」与「保留为我的叫法 / 忽略」。

## 回滚点

- store 过滤单独 commit（可独立 revert，回退为返回全部 items）
- 视图渲染单独 commit（revert 后回到 09-14 的「只在人物资料可见」状态）

## 明确不做（design.md §5）

不加 TTL、不改历史去重语义、不给 `term_preference` 打开 notify、不做聚合推送、不做建议分页。
