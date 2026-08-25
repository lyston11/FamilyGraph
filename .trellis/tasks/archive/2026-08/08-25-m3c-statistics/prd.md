# m3c 统计

> 父任务：[08-25-m3-media-lunar-stats-search](../08-25-m3-media-lunar-stats-search/prd.md)｜依赖：m2a（聚合范围受授权约束）

## Goal

可见范围内的家族统计页。

## Requirements

- `GET /stats`（服务端按当前用户可见性范围过滤后聚合）：总人数、世代分布柱状图、男女比例、近 30 天生日列表。
- 生日提醒口径：公历当月近似策略；农历生日的闰月生日按"当月有则列出"处理并在代码注释说明。
- 前端统计页：Element Plus 图表（或轻量自绘），数字与 fixture 对比测试。

## Acceptance Criteria

- [ ] fixture 数据下四项统计数字与服务端计算完全一致（自动化对比测试）。
- [ ] 不可见成员不计入任何统计项（构造 D 家族验证）。
- [ ] 闰月生日在对应月份出现一次，不重复不遗漏。

## Non-goals

- 自定义报表/导出；跨年趋势等 v2 图表。
