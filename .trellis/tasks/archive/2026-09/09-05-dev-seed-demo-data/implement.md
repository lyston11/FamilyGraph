# 开发种子模块与空库自动播种机制 Implementation Plan

## 执行清单

- [ ] 1. 后端实现
  - [ ] 1.1 config.py 新增 DEV_SEED_DEMO_DATA（默认 "0"）
  - [ ] 1.2 app/dev_seed.py：maybe_seed_demo_data + _seed_demo_family + reset_database + __main__
  - [ ] 1.3 app/main.py preflight 挂点（bootstrap 之后调用，防重入）
  - [ ] 1.4 演示数据集：6 用户(123456)+1 空间+成员行+spouse/elder 关系+confirmed SourceFact
  - [ ] 1.5 backend/tests/test_dev_seed.py 四类用例
- [ ] 2. 部署接线
  - [ ] 2.1 docker-compose.yml api 环境透传 DEV_SEED_DEMO_DATA（默认 0）
  - [ ] 2.2 本机 .env 开启 DEV_SEED_DEMO_DATA=1（主会话部署阶段执行）
- [ ] 3. 门禁
  - [ ] 3.1 backend lint / type-check / pytest 全绿（quality-guidelines 命令）
- [ ] 4. 清库与端到端验证（主会话）
  - [ ] 4.1 重建 api 镜像并部署
  - [ ] 4.2 清库（带时间戳备份）→ 重启 → admin-credentials 生成
  - [ ] 4.3 家庭端 王德海/123456 登录 200；后台概览出现王德海家 6 成员

## 验证指令

```bash
cd backend && <lint/type-check/pytest 命令见 backend quality-guidelines>
docker compose build api && docker compose up -d api
docker compose exec api python -m app.dev_seed --reset   # 清库（备份后）
docker compose restart api
docker compose exec api python -c "…验证 users/spaces 行数与 admin-credentials…"
curl -s -X POST localhost:8000/api/auth/login -d '{"name":"王德海","pin":"123456"}' …
```
