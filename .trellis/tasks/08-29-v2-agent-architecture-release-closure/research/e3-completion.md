# E3 完成证据：compose 模型回路 / 第二卷恢复演练 / UI 走查 / embedding 结论

> 任务：`08-29-v2-agent-architecture-release-closure`；commit 基线 `f596ead`
> 日期：2026-08-29；环境：macOS + OrbStack compose 栈（本仓库 `docker-compose.yml`）+
> ZCode 内置浏览器（IAB）。管理员账号由用户提供（运维员）。

## 1. compose 栈上的完整模型回路 ✅

- 前置：`.env` 增加 `AGENT_RUNTIME_ENABLED=1` 并重建 api/agent 容器（内部探测
  `POST api:8001/internal/agent/jobs/lease` → 422 应用层响应，路由可达）。
- 流程：login（运维员）→ 建空间「E3 Compose 验证空间」→ 管理后台注册
  `abrdns`（https://new-api.abrdns.com/v1，secret 服务端加密）→
  空间 Provider 设置 `GLM-5.2`（cloud_allowed）→ 建 session → 发消息
  （Idempotency-Key: compose-e3-1）。
- **run 8 `succeeded`（~16s，attempt=1）**；事件 6 条（user/started/turn/
  assistant_added/turn.completed/settled）。
- 助手正文（真实模型输出）：
  > 家庭图谱（Family Graph）是一个用于记录和管理家庭成员之间亲属关系结构的系统，
  > 它能够确定性地解析人物之间的结构路径、查询可见成员资料、解析亲属称谓，并帮助
  > 用户了解家庭成员之间的关联方式与称呼规范。
- egress 审计：`{"provider_id":2,"status":"succeeded","upstream_status":200,
  "bytes_read":12939}` —— 代理唯一 egress 在 compose 拓扑下实测成立。

## 2. 第二卷恢复演练（06 §8 六步）✅（一步部分豁免，见 2.1）

备份：`docker compose exec api python -m app.backup` →
`familygraph-20260829-113533.tar.gz`（online backup API + 归档 uploads），
经 `docker compose cp` 取出到宿主，解包快照装入第二卷 `/tmp/fg-restore-volume`，
以独立 DATA_DIR 起第二 api 实例（:18002）。

| 步骤 | 结果 |
|---|---|
| 1. integrity_check | `ok` |
| 2. 外键/约束 + 关键表计数 | `foreign_key_check` 0 违规；verify_restore 计数：users=1, accounts=1, space_members=3, agent_sessions=2, agent_runs=8, agent_run_events=132, agent_messages=8, domain_events=3, memories/rag/action_cards/source_facts=0（本卷无此类数据，属正常） |
| 3. 事件序列 + SourceFact revision | run 事件 seq 无重复、无缺口；source_facts 0 行（无数据卷，revision 检查不适用） |
| 4. FTS rebuild/一致性 | 快照 FTS 与 active chunk 投影一致（0=0）；向**恢复副本**插入合成 RAG 文档后 `MATCH '家谱笔记'/'蓝色封皮'/'曾祖父'` 各 1 命中；删除 chunk 后 FTS 同步为 0（tombstone 语义）。注意：trigram 分词要求查询词 ≥3 字符 |
| 5. 投影重建（DerivedFact/BehaviorProjection/Context） | 表结构完整、当前 0 行（可从真源重建；本卷无真源数据） |
| 6. SSE 历史重放 | 第二实例登录后重放 run 8：**6/6 事件 seq/类型/payload 与 live 完全一致** |

**2.1 豁免项**：第 6 步的"带引用的受控联网 E2E"未执行——恢复卷未配置
Controlled Web 搜索 Provider（无凭据），受控联网链路本身由 test_controlled_web
51 例覆盖。列为遗留（P2，需搜索 Provider 凭据后补）。

演练后第二实例已下线，合成数据仅存在于临时副本（宿主 /tmp），源卷无污染。

## 3. UI 走查（375×812 + 桌面 1280×800）✅（浏览器实测 + 截图）

截图：本目录 `01`–`06` PNG（登录页/主界面/流式面板/Esc 后/移动主界面/移动面板）。

| 项 | 桌面 1280 | 375×812 |
|---|---|---|
| 登录页渲染 + 屏幕阅读标签（label/placeholder/PIN 可见性切换） | ✅ 01 | ✅（同组件自适应） |
| 登录提交 | ✅ 点击与回车路径均到达处理逻辑（见 3.1） | ✅ 同 |
| 主界面（主题切换/空间选择/成员卡/操作按钮） | ✅ 02 | ✅ 05 单列自适应 |
| 悬浮助手打开/关闭按钮 aria-label | ✅ `打开/关闭家庭助手` | ✅ 同 |
| Enter 发送 | ✅ 真实键盘事件触发（见 3.1） | 同组件 |
| 流式回答 + 工具徽标 | ✅ 03：`familygraph.get_self_context 成功`（本例另见 list_visible_people 成功），真实模型正文（回答正确指出空间内仅本人可见） | 同 |
| Esc 关闭 + 焦点 | ✅ 04（关闭后 launcher 恢复"打开家庭助手"；焦点圈闭实现于 PanelContent，单测覆盖） | 移动分支共用同一 handler |
| 布局 | 桌面抽屉式面板 | 移动 Teleport 全屏面板（与单测合同一致）06 |

### 3.1 走查过程记录（如实）

- Playwright 的 click/press 在该 IAB 环境对 naive-ui 组件超时（动作合成怪癖），
  改用页面内原生事件派发（与真实用户输入同一 handler 路径）完成全流程验证；
  登录、发送、Esc 均以该方式确认生效并有截图。
- 助手每次打开默认进入新会话（上一会话需从"选择会话"下拉找回）——UX 观察，
  属 redesign 任务 P1 范畴，不阻断本任务。
- reduced-motion：样式层存在 `prefers-reduced-motion` 适配空间，本轮未做 OS 级
  开关切走查（需真实浏览器环境设置），列为 redesign 任务后续人工项。
- 流中断/错误恢复：产品侧由 agent store 代际校验 + 取消回答按钮 + settle 终态
  覆盖（本轮"取消回答"按钮出现于流式期间，截图 03 底部）；API 中断场景由
  reaper/failed 终态单测覆盖。

## 4. embedding adapter（05 §4 "可选"）——接受缓项，不实现

- 现状：schema 已预留 `rag_chunks.embedding_status` 生命周期位
  （`disabled/not_configured/pending/ready/failed`，CHECK 约束），检索为 FTS5-only，
  无 embedding 适配器代码。
- 文档 05 §4 明确"embedding 是可选 adapter，不引入独立向量数据库"——当前状态
  **与设计一致**。实现适配器（模型选型、API 凭据、向量存储、检索合并、回填）
  是独立特性，应单独立项规划，不属于本收口任务的 E3 尾项。
- 结论：**接受缓项（P2）**，需要时另立任务。
