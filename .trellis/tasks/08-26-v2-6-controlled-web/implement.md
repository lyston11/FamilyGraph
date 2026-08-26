# V2.6 Controlled Web 与发布实施计划

- [ ] 实现平台/空间双层 feature config、配额和工具动态披露。
- [ ] 实现 search adapter、approved URL token、fetch gateway 与完整 SSRF/redirect/DNS/size/MIME 保护。
- [ ] 接 V2.5 Policy Guard/ContextBuilder，完成 query PII/secret 检测和 external trust/citation。
- [ ] 实现 Web tool audit、usage/budget、安全指标与 kill switch。
- [ ] 扩展 Assistant UI 显示联网状态、引用、失败/拒绝、用量；Steward tool manifest 明确无 Web。
- [ ] 加固 Compose/nginx/internal network/health/graceful shutdown/secret rotation。
- [ ] 更新 backup/restore 与投影重建脚本、运行手册。
- [ ] 运行 SSRF、注入、配额、Provider outage、SSE/worker crash 和全量 E2E。

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

任何 Web 安全问题先全局 kill switch；移除工具披露即可，不影响本地 Assistant/Steward。部署故障停止 agent 容器，api/web 继续提供家谱功能。
