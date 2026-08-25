# Journal - lyston (Part 1)

> AI development session journal
> Started: 2026-08-25

---



## Session 1: v1 全量交付：M0-M4 十六子任务完成+全项目复审

**Date**: 2026-08-26
**Task**: v1 全量交付：M0-M4 十六子任务完成+全项目复审
**Branch**: `main`

### Summary

FamilyGraph v1 功能开发完毕：16 子任务全部实现归档；全项目复审修复分层/异步阻塞等 5 项；M2/M3 专项重验 ALL PASS；终态门禁后端 118 测试+mypy strict 全绿、前端四门禁全绿、docker e2e 通过。

### Main Changes

## 会话成果

**v1 全量交付**：M0-M4 共 16 个业务子任务全部实现、验证并归档，另完成全项目代码复审。

### 里程碑
- **M0**：FastAPI+Vue3 骨架与部署（m0a）；名字+PIN 认证安全基座——限流锁定、challenge 落库防重放、refresh 轮换+重用检测、首启管理员引导（m0b）
- **M1**：档案建档向导+一次性 PIN+ClaimState 认领+custody 代管权矩阵（m1a）；四分类关系 FSM+世代一致性校验+合并请求（m1b）；家庭空间成员 FSM+幂等邀请（m1c）；Vue Flow 三布局画布+lunar 公农历互补（m1d）
- **M2**：visibility.py 授权单点+IDOR 矩阵测试（m2a）；家族连通视图+摘要卡（m2b）；join-by-user 申请流+断连即时降级（m2c）
- **M3**：附件安全校验链+相册+孤儿清扫（m3a）；历别切换自动互填（m3b）；可见性范围统计页（m3c）；全局搜索+画布筛选（m3d）
- **M4**：响应式+a11y 基线（m4a）；管理员后台+审计时间线（m4b）；online backup 演练+README 运维指南+回归清单（m4c）

### 全项目复审（用户要求专项）
修复 5 项：前端视图直连 axios 分层违规（8 处下沉 api 层）、async 路由阻塞 PIL 重编码、conftest _TABLES 缺 attachments、architecture 限流措辞对齐实现、目录文档刷新。登记 HANDOFF Q8（删除空间 owner 级联策略，v2 引导流）。

### M2/M3 复审验收（用户要求专项）
IDOR 矩阵 5 用例 + 申请流 3 用例 + 附件链 5 用例 + 端到端旅程 9 步 ALL PASS。

### 终态门禁
后端 ruff/format/mypy strict(52 文件) + pytest **118 passed**；前端 lint/type-check/vitest/build 全绿；docker compose e2e 复验通过。共 40 commits。

### 遗留
手机视口人工走查；Element Plus 按需引入；HANDOFF Q8（v2）。


### Git Commits

| Hash | Message |
|------|---------|
| `4444f7d` | (see git log) |
| `eb506c3` | (see git log) |
| `a0b8c61` | (see git log) |
| `7e28a9d` | (see git log) |
| `e0bb9ff` | (see git log) |
| `28be7a9` | (see git log) |

### Testing

- [OK] backend: pytest 118 passed (ruff/mypy strict clean); frontend: vitest 9 files + build clean; docker compose e2e verified

### Status

[OK] **Completed**

### Next Steps

- v1 已可发布使用；待办：手机视口人工走查、迁云按 README 清单执行；v2 计划见 HANDOFF（agent 推荐/互反称谓/Q8 等）
