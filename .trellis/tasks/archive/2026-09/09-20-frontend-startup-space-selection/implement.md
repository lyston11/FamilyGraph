# 实施计划

## 规划与启动

- [x] 核对根因：`spaces.load()` 取 `spaces[0]`（服务端 `created_at DESC`）+ `ensureDefaultSpace` 优先级 + `syncRouteSpace` 监听 `currentSpaceId` 三者叠加成多次切换。
- [x] 生产核实朱元璋空间列表顺序与最终落点（19 李家 → 1 明皇室 → 2 朱氏皇族）。
- [ ] 用户批准规划后 `task.py start`，进入隔离 worktree。

## 实现顺序

1. 新增 `composables/spaceSelection.ts`：迁出 `selectDefaultSpaceId`，新增家族分组与 `resolveStartupSpaceId(spaces, options, routeName)`；`useSpaceContext.ts` re-export 保持既有 import 与测试可用。
2. `stores/spaces.ts`：`load()` 去掉 `spaces[0]` 兜底（无上下文时保持 null）；补注释说明决策权归属。
3. `composables/useSpaceContext.ts`：`ensureDefaultSpace` 改为按路由解析最终目标、只 `switchSpace` 一次；`defaultRouteFallback` 行为不变。
4. `components/shell/AppShell.vue`：`syncRouteSpace` 触发源去掉 `spaces.currentSpaceId`，只在路由变化且类型不匹配时对齐。
5. 回归测试：
   - `composables/__tests__/spaceSelection.spec.ts`：优先级、家族分组、按路由落点、缺类型回退、未配对孤立 household。
   - `stores/__tests__/spaces.spec.ts`：`load()` 不再自行选空间；已有值保持。
   - `composables/__tests__/useSpaceContext.spec.ts`：启动只调用一次 `switchSpace` 且目标为路由对应类型。
   - `components/shell/__tests__/AppShell.spec.ts`：家族树页/家庭页启动落点、显式切换与返回家庭卡不变、未登录不发请求、登录后默认选择只触发一次。
6. 更新 spec（若涉及启动时序合同）与任务工件。

## 验证

- `cd frontend && npm run lint && npm run type-check && npx vitest run src/composables/__tests__/spaceSelection.spec.ts src/composables/__tests__/useSpaceContext.spec.ts src/stores/__tests__/spaces.spec.ts src/components/shell/__tests__/AppShell.spec.ts src/views/__tests__/family-tree.spec.ts`
- `cd frontend && npm test && npm run build`
- `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`（端点未变，作回归确认）。
- 生产浏览器实测：以朱元璋硬刷新 `/family-tree`，确认选择器不再先显示「李家」。

## 回滚点

纯前端；回退 `spaceSelection.ts`、`load()` 兜底、`ensureDefaultSpace` 与 `syncRouteSpace` 即可，无数据影响。
