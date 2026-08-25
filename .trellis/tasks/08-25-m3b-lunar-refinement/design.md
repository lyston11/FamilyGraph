# m3b 技术设计

m1d 已交付 lunar-python 互转（mirror_date 自动互补、闰月往返）。本任务补齐：
- 历别切换时前端自动换算预填（调后端 mirror 或本地 lunar-typescript——统一走后端 enrich，避免双实现）
- 超范围日期：后端已归一 None；前端提示「该日期超出历法支持范围，仅保存原文」
- 排序/统计消费口径回归（与 m1d 一致，无新规则）
