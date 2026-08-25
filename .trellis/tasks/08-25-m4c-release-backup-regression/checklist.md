# v1 全量回归清单（2026-08-25 复跑记录）

## M0
- [x] m0a: ruff/mypy strict/pytest 全绿；compose 双链路 health 200；alembic 往返
- [x] m0b: 登录/锁定/challenge 防重放/refresh 轮换+重用检测/PIN 白名单/bootstrap 一次性

## M1
- [x] m1a: 建档一次性 PIN；handover 认领后创建者写 403、perpetual 不受限；删除级联+audit 快照；重名并存；disclosure 默认全 false
- [x] m1b: 四分类 CRUD；成环/同代矛盾拒绝；同对唯一非终态边；revoke 后可重连；合并请求原子激活
- [x] m1c: 多空间并存；邀请幂等；30d 过期；首登无空间引导；connection+space 同事务
- [x] m1d: 树状世代正确/多根并列；失败回退画布；位置持久化；农历闰月往返

## M2（本轮重点复验）
- [x] m2a: A 见 B full（直系互见）；A 见 C summary+披露开关两态；D invisible（档案 404+图不返回）；pending 两端互见摘要不传递 — test_authz_matrix.py 5 用例 ✓
- [x] m2b: clan scope 连通分量；summary 节点裁剪；申请入口按钮
- [x] m2c: join-by-user 幂等+可见性门禁；owner 审批接受→完整互见；revoke 即时降级 404 — test_m2c_flows.py 3 用例 ✓

## M3
- [x] m3a: exe/SVG/文本伪装全拒；EXIF strip 断言；javascript: 外链拒；删除清文件 — test_m3a_attachments.py 5 用例 ✓
- [x] m3b: mirror 端点往返一致 — test_m3bcd.py ✓
- [x] m3c: 统计范围排除 D 家族（total=3 vs 丁 total=1）✓
- [x] m3d: 名字+称谓命中；D 不出现；peer 结果 summary 级 ✓

## M4
- [x] m4a: 响应式断点样式；focus-visible/reduced-motion；settings label 修复
- [x] m4b: 非 admin 全端点 403；重置 PIN 后旧 access 401、新 PIN 登录强制改；审计留痕 — test_m4b_admin.py 3 用例 ✓
- [x] m4c: online backup → restore → integrity ok 且行数一致 — test_m4c_backup.py ✓

## 门禁终态
- 后端：ruff check ✓ / format --check ✓ / mypy strict(51 文件) ✓ / pytest 118 passed ✓
- 前端：lint ✓ / type-check ✓ / vitest 9 文件 ✓ / build ✓
