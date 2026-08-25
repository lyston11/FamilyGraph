# m3d 技术设计

- GET /api/search?q=：名字/称谓标签 LIKE 前缀匹配；范围=reachable_ids；
  返回摘要卡 {id,name,level}（full→可点开完整档案；summary→遮罩态详情）
- 家庭空间页内筛选：纯前端过滤画布节点
- invisible 永不返回（D 家族不可命中）
