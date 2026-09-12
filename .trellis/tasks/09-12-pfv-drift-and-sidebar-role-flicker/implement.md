# 执行计划

1. 补充 PFV 版本漂移筛选与入队原因，新增后端回归测试覆盖 current 旧版本在无事件时被周期重建。
2. 修改 spaces store：成员请求 stale-while-revalidate、generation 校验和错误状态；提供不切换当前空间的成员刷新入口。
3. 更新空间切换与路由守卫调用，保持失败 fail-closed、消除静默吞错和 currentSpaceId 副作用。
4. 增补前端 store/composable/router 回归测试，覆盖入口保持、失败态和守卫上下文不变。
5. 运行后端相关 pytest/ruff/mypy 与前端 lint/type-check/test/build；最后执行全量质量检查并记录部署重启提示。
