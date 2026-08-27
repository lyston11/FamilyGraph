# V2.6 Controlled Web 与发布实施计划

- [x] 实现平台/空间双层 feature config、配额和工具动态披露。
- [x] 实现 search adapter、approved URL token、fetch gateway 与完整 SSRF/redirect/DNS/size/MIME 保护。
- [x] 接 V2.5 Policy Guard/ContextBuilder，完成 query PII/secret 检测和 external trust/citation。
- [x] 实现 Web tool audit、usage/budget、安全指标与 kill switch。
- [x] 扩展 Assistant UI 显示联网状态、引用、失败/拒绝、用量；Steward tool manifest 明确无 Web。
- [x] 加固 Compose/nginx/internal network/health/graceful shutdown/secret rotation。
- [x] 更新 backup/restore 与投影重建脚本、运行手册。
- [x] 运行 SSRF、注入、配额、Provider outage、SSE/worker crash 和全量 E2E。

## 验证

```bash
cd backend && pytest
cd backend && mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd frontend && npm run type-check && npm run lint && npm test && npm run build
docker compose config
docker compose up --build
docker compose exec api python -m app.backup
```

恢复演练须在全新数据卷完成，并记录 integrity_check、关键表计数、FTS rebuild、SSE 历史和一条带引用的受控联网 E2E。

## 回滚

任何 Web 安全问题先全局 kill switch；移除工具披露即可，不影响本地 Assistant/Steward。

## E2E 验收记录（2026-08-27）

空库 Compose E2E 全链路通过：
- 三镜像构建（api/web/agent），api 依赖 httpx 移入主 dependencies
- 迁移 0015 应用，web_* 表全部创建
- bootstrap admin → 注册 guga AgentProvider → 创建空间 → 双层联网配置
- 真实公网 fetch（httpbin.org/html）→ DNS/IP 校验通过 → content-type 接受 → HTML 清洗 → citation 记录
- token 一次性 CAS claim 验证（重复使用被拒）
- SSRF 真实拒绝：私网 10.0.0.1 / metadata 169.254.169.254 / loopback 127.0.0.1
- content-type 真实拒绝：application/pdf
- PII 真实拒绝：电话号、secret token；安全 query 通过
- backup → verify_restore：integrity_check ok + V2 表计数 + FTS 自洽
- agent 重启恢复 healthy；/internal/* 经 nginx 404；web admin 401 未认证
- 默认 CONTROLLED_WEB_ENABLED=False
