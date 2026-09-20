# 技术设计：启动期空间选择单点决策

## 1. 根因（已核对）

启动期有**两个**写入者竞争 `currentSpaceId`：

- `stores/spaces.ts` 的 `load()`：`currentSpaceId === null` 时取 `this.spaces[0].id`。服务端 `GET /spaces` 按 `created_at DESC` 排序，所以「最新加入的 household」被当成默认空间——绕过了 `useSpaceContext.selectDefaultSpaceId` 的优先级规则。
- `composables/useSpaceContext.ts` 的 `ensureDefaultSpace()`：按优先级选默认空间（household），并按需导航。

再叠加 `AppShell` 的 `syncRouteSpace`：它监听 `[route.name, spaces.spaces.length, spaces.currentSpaceId]`，**每个中间态都触发一次切换**。

朱元璋的实际序列（生产核实）：`load()` → 19 李家（`created_at` 最新，选择器先显示「李家」）→ `ensureDefaultSpace` → 1 明皇室 → `syncRouteSpace`（路由是家族树，取配对 lineage）→ 2 朱氏皇族。共 3 次写入、2 次可见切换。

（李家未继续切到「李氏家族」是因为朱元璋不是该 lineage 成员，`lineageForSpace(19)` 为 null，孤立 household 在家族树页无 lineage 落点。）

## 2. 设计

### 2.1 把优先级与落点抽成纯模块

新增 `frontend/src/composables/spaceSelection.ts`，导出两个纯函数（无 store、无 router 依赖）：

- `selectDefaultSpaceId(spaces, options)`：沿用既有优先级（最近 household > own/managed household > 第一个 household > 第一个 lineage）。从 `useSpaceContext.ts` 迁出，原处 re-export 以保持既有 import 与测试可用。
- `resolveStartupSpaceId(spaces, options, routeName)`：
  1. 先按优先级定「当前家族」（家族 = lineage 及其配对 household 归为一组）；
  2. 再按 `routeName` 选该组内的空间：`family-space` → lineage，`home` → household；
  3. 该组缺少所需类型时退回该组可用空间（例如未配对的孤立 household 停在家族树页 → 仍用该 household，由既有提示面板说明）；
  4. 其它路由（settings/stats/memory 等）→ 按优先级选出的 household（保持既有「非空间页不跳页」语义）。

「家族分组」复用 `spaces` store 已有的 `lineageForSpace` / `householdForLineage` 语义；纯函数以 `(spaces, routeName)` 为输入，分组推断写成同一模块内的纯函数，store getter 与它共用同一份判定，避免两套规则漂移。

### 2.2 `load()` 不再自行挑默认空间

`stores/spaces.ts` 的 `load()` 去掉 `spaces[0]` 兜底：

- `currentSpaceId` 为 null 时**保持为 null**（由 `ensureDefaultSpace` 决定），只设置 `spaces` 列表；
- `currentSpaceId` 已有值时维持不变并刷新该空间成员。

影响面：`SettingsView`/`MemoryView`/`PersonProfileView` 等直接调 `spaces.load()` 的地方，都发生在已有上下文之后（或随后显式调 `ensureDefaultSpace`），不再依赖 `load()` 选空间。既有 `stores/__tests__/spaces.spec.ts` 中「加载后默认选中第一个空间」的断言按新合同改为「加载后不自行选择」。

### 2.3 `ensureDefaultSpace` 一次到位

`ensureDefaultSpace` 改为：

1. 需要时 `spaces.load()`（只填列表）；
2. 用 `resolveStartupSpaceId(spaces, options, route.name)` 求**最终**目标 id；
3. 只调用一次 `switchSpace(targetId, { navigate: false })`；
4. 导航由调用方与既有 `defaultRouteFallback` 语义决定（登录默认页 → 家族树时改入家族树，行为不变）。

这样启动期只有一次 `currentSpaceId` 写入，选择器不再出现中间态。

### 2.4 `syncRouteSpace` 不再与启动抢

`AppShell` 的 `syncRouteSpace` 改为只在**显式导航**时对齐：

- 触发源去掉 `spaces.currentSpaceId`（那是启动决策的产物，不是用户意图）；
- 仅在 `route.name` 变化且当前家族已解析、且目标类型与当前空间类型不一致时切换。

理由：`currentSpaceId` 参与触发是当年为「硬刷新时默认空间恢复晚于路由解析」打的补丁；本次由 `ensureDefaultSpace` 直接按路由落点解决该问题，补丁可以去掉，避免启动期二次切换。

### 2.5 不动的东西

- `switchSpace` 的事务顺序、epoch 清理、各 store 代际丢弃、`ui.recentHouseholdId` 仅内存、未登录不发请求——全部保持。
- 服务端不改：本次是纯前端时序问题。

## 3. 兼容与回退

- 纯前端改动，无迁移、无数据修复。
- 回退：恢复 `load()` 的 `spaces[0]` 兜底与 `syncRouteSpace` 的 `currentSpaceId` 触发即可。
