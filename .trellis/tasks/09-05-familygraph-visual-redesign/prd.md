# FamilyGraph 双前端高级视觉重设计

## Goal

TBD.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
# PRD: FamilyGraph 双前端高级视觉重设计

## Goal

让 FamilyGraph 家庭端和系统管理后台从当前偏传统的浅色卡片界面，升级为有明确品牌识别的高级透明玻璃视觉：家庭端突出家庭空间大卡片和星空家族树画布，后台使用克制的深色星空运营台。视觉改造不改变现有数据、权限、导航和 API 行为。

## Background

- 仓库包含两个独立 Vue 3 + Vite + TypeScript 前端：`frontend/` 家庭端、`system-admin-frontend/` 系统后台。
- 两端已有玻璃 token 和星点背景基础，但家庭首页仍是普通双栏小卡片，家族树画布与页面氛围割裂，后台仍以浅色为主，品牌层次不足。
- 家庭端 token 单一来源在 `frontend/src/styles/tokens.ts`；后台 token 单一来源在 `system-admin-frontend/src/styles/main.css` 的 `:root`。

## Requirements

1. 家庭端全局背景具备层次化星空氛围，玻璃壳和内容区域保持可读性、响应式和无障碍焦点态。
2. 家庭首页首屏以家庭空间大卡片为主视觉，展示空间名、成员数、版本和管理员状态，并保留现有家庭成员投影、空态、加载态、错误态和操作入口。
3. 家族树使用更明确的深色星空底画布，节点、关系线、控制器和关系说明面板保持现有交互与权限语义。
4. 家庭端导航壳和顶部动作使用更精致的透明层次，不增加后台入口或新业务操作。
5. 系统后台升级为深色星空玻璃运营台，概览指标、表格、认证页、导航和状态标签统一消费 `--ag-*` token；后台仍不依赖家庭端代码。
6. 所有玻璃材质提供 `backdrop-filter` 不支持时的不透明降级；支持 `prefers-reduced-motion`。
7. 视觉改造不引入外部字体、图片、第三方依赖，不修改 API、store、路由守卫和敏感数据边界。

## Acceptance Criteria

- 家庭端首页有可识别的 `family-space-hero` 大卡片，首屏能看到空间名、成员计数和现有操作入口。
- 家庭端家族树画布使用独立的星空色板，节点和关系说明在深色背景上保持可读，375px 视口无页面级横向滚动。
- 家庭端纸墨/清雅主题切换、现有家庭卡测试、壳测试和路由行为继续通过。
- 后台概览、登录页和任一列表页均呈现深色星空玻璃材质，异常/健康语义仍可区分，表格内部横向滚动规则不变。
- 后台模块边界、敏感票据存储、审批二次确认和零家庭依赖测试继续通过。
- `frontend` 与 `system-admin-frontend` 的 lint、type-check、test、build 均通过。

## Out Of Scope

- 不改 API、数据库、Pinia store、路由结构、权限判定、敏感字段脱敏和后台封闭策略。
- 不重写所有业务视图；除壳、家庭首页、家族树和后台共享样式外，其余页面通过 token 自动获得一致材质。
- 不制作营销落地页、不添加图片素材、不引入动画库。

## Key Decisions

- 家庭端保留纸墨/清雅主题契约，在 token 中补充画布专用色；星空画布作为家族树主视觉，家庭首页用大卡片承载空间身份。
- 后台采用单一深色主题，减少运营场景中的高亮干扰，继续以 `main.css :root` 作为颜色唯一来源。
- 以现有 class 和 token 扩展为主，模板只增加家庭空间 hero 展示结构，降低行为回归风险。

## Risks / Deferred

- 浏览器对 `backdrop-filter` 的支持不同，降级背景必须保持对比度。
- 深色后台可能暴露组件内未显式消费 token 的边界，需通过全量构建和源级搜索复核。
