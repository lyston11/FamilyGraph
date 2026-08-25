# m3c 技术设计

- GET /api/stats：{total, by_gender, generation_histogram, birthdays_this_month}
  范围 = viewer 的 clan 连通分量（visibility.reachable_ids），invisible 不计入
- 生日口径：公历当月匹配 birth.date 月份；农历生日按 mirror_date 当月近似（注释说明）
- 前端统计页：数字卡 + 简易柱状（CSS 条形，不引图表库）
