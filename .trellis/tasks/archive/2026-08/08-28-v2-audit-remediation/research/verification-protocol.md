# V2 整改验证协议

## 1. 运行前固定条件

每次验证记录以下元数据：

```text
git rev-parse HEAD
git status --short --branch
python3 --version
node --version
docker compose version
```

测试临时数据必须使用隔离 `DATA_DIR`/Docker volume；不得覆盖用户目录、工作区主库或 uploads。所有命令、退出码、测试数量、失败摘要和生成产物路径写入任务 notes 或 CI artifact。

## 2. 静态与单元门禁

```bash
cd backend
ruff check .
ruff format --check .
mypy app
pytest -q

cd ../agent
npm run type-check
npm run lint
npm test
npm run build

cd ../frontend
npm run type-check
npm run lint
npm test
npm run build
```

若项目脚本要求特定工作目录或 `PYTHONPATH`，把实际可重跑命令和环境变量完整记录，不能只写“通过”。

## 3. 必测安全矩阵

### 3.1 可见性

主体 × 目标状态 × 空间关系 × purpose 至少覆盖：本人、同 household active、lineage、直系跨空间、peer、guest、pending、provisional、minor、disputed/revoked、platform operator、无关用户；出口覆盖 profile、graph、search、statistics、agent、rag、export、attachment。

断言不仅是 `visible`，还要逐字段断言：精确生日、联系方式、地址、健康、私人描述、凭据、原始 masked 值不能通过任何出口出现。

### 3.2 Agent Runtime

验证篡改 service/run token、过期 token、错误 typ/audience、错误 scope、未知工具/版本/字段、嵌套 schema 边界、重复 idempotency key、SSE Last-Event-ID、reaper terminal event、sidecar crash、provider timeout 和 local-required 无本地拒绝。

### 3.3 跨空间与删除传播

两个用户、两个空间、两个 session，分别写入 private/household/lineage memory；切换空间、撤权、删除、争议和离开成员资格后，检查 SQL、FTS、embedding、DerivedFact、BehaviorProjection、SSE/cache、导出投影均不再泄露旧内容。

### 3.4 受控 Web

验证平台/空间开关、工具披露、loopback/RFC1918/link-local/metadata、DNS rebinding、redirect、凭据 URL、非文本、大小/速率/预算、PII/secret query、approved token CAS、真实 citation、外部 prompt injection 和 Steward 无 Web 工具。

## 4. 空库 Compose 与恢复

必须在新 volume 上执行：

1. 构建 api/web/agent 镜像并运行健康检查；
2. 从空库应用完整 Alembic 链；
3. 创建最小用户/空间/事实/Session/Memory/RAG/ActionCard/Web citation 数据；
4. 执行 online backup（禁止运行期 cp 主库）；
5. 在第二个新 volume restore；
6. 运行 `integrity_check`、关键表计数、事件序列、SourceFact revision、FTS 自洽和一条带引用受控联网 E2E；
7. 重启 api/agent，验证 lease、SSE 历史和投影重建。

恢复演练产物必须包括命令、退出码、时间戳、表计数前后对比和失败时的回滚说明。

## 5. UI 人工验收

在 375×812 和桌面视口分别记录：全局悬浮入口、抽屉/全屏切换、空间 banner、键盘焦点、Esc/返回、屏幕阅读标签、reduced motion、流式中断、错误文案、登出/切换后历史回退不可恢复。截图或录屏只允许使用合成数据。

## 6. 证据格式

每条 AC 至少关联：

```yaml
ac: AC-...
status: passed | partial | blocked
commit: <sha>
command: <完整命令>
exit_code: 0
tests: <数量/关键断言>
artifact: <日志、截图、报告或 CI URL>
notes: <限制、环境差异、残余风险>
```
