# m0b 实施计划

## Checklist

1. [ ] Alembic 迁移：accounts、audit_log、auth_challenges、refresh_sessions（users 已在 m0a 建）
2. [ ] utils/security.py：pin_gen(secrets)/hash(bcrypt 或 argon2)/jwt —— 单测先行
3. [ ] services/auth_guard.py：失败计数+锁定 —— 分支覆盖测试
4. [ ] services/challenge.py + refresh_session.py：落库、单次使用原子置 used_at、轮换链与重用检测 —— 重放/重用用例先行
5. [ ] api/auth.py：login/select/refresh/logout 全流程 + 统一错误结构
5. [ ] require_pin_changed 全局依赖 + 白名单
6. [ ] services/bootstrap.py + 首启引导接口
7. [ ] 前端：Login/Onboarding/Settings 页 + 409 候选弹窗 + 强制改PIN守卫
8. [ ] audit_log 接线（login_failed≥3/pin 变更）
9. [ ] 安全复核：日志脱敏断言测试（构造含 PIN 的请求遍历日志断言不存在）
10. [ ] 攻击面用例：challenge 重放、refresh 重用检测触发全会话撤销、pin_must_change 白名单外全部 403

## 验证命令

```bash
cd backend && pytest tests/test_auth*.py tests/test_security*.py -v
cd frontend && npm run test -- auth
# 手工旅程: 首启→管理员登录→建测试账号→错误5次锁定→同名同PIN消歧(含重放拒绝)→改PIN旧token失效→refresh重用触发全会话撤销
```

## 审查门禁

trellis-check + 安全清单：①数据库无明文 PIN ②日志无凭据 ③challenge 重放被拒 ④token_version 生效 ⑤限流参数可配置。

## 回滚点

feature 分支合入；迁移可 downgrade；限流可经 env 临时关闭（留审计痕迹）。
