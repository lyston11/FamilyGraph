# 隔离浏览器验收记录

任务：`09-19-steward-recommendation-correctness`
日期：2026-09-19
环境：本机隔离栈（**未使用** 8000/8001/8002，那些端口是 SSH 隧道指向生产）

| 组件 | 隔离配置 |
| --- | --- |
| 后端三 listener | 18000 / 18001 / 18002（`PUBLIC_API_PORT` 等） |
| 前端 | Vite dev server `localhost:15173`，`/api` 代理到 `127.0.0.1:18000` |
| 数据库 | `DATA_DIR=/tmp/fg-browser-*`（临时目录，`alembic upgrade head`） |
| 数据 | 合成账号（验收甲/乙/丙/丁/戊/己，PIN 123456），无真实个人信息 |

验证隔离性：`/api/health` 经 Vite 返回 200，且隔离后端访问日志出现该请求；`lsof` 确认 15173 由本地 vite 监听、18000 由本地 uvicorn 监听。

## 场景与结果

### 场景 1：亲子被误判为兄弟姐妹（lineage 空间「验收宗族」id=1）

`验收甲`（父）与 `验收乙`（子）已有 confirmed `biological_parent`。预置两条模型建议：

| 建议 | fact_type | 预期 | 实测 |
| --- | --- | --- | --- |
| id=1 | `direct_sibling`（与已确认亲子冲突） | 不可行动 | `state=superseded`，`allowed_actions=['open_details']` |
| id=2 | `adoptive_parent`（无冲突对照） | 保留待核实 | `state=proposed`，`allowed_actions=['open_details','submit','dismiss']` |

浏览器实测（`验收甲` 登录 → 通知与待办）：

- 「待核实」分区**只出现对照项**：`关系线索：验收乙可能是你的养子女`，带「查看详情」。
- 冲突项（子女被当作兄弟姐妹）**不再出现在待核实分区**。
- 打开对照项详情：显示「提交关系提案 / 忽略 / 关闭」，确认资格说明正常。

冲突项直接提交（真实 hash）：

```text
POST /api/steward-suggestions/1/submit?space_id=1
→ HTTP 409 {"code":"SUGGESTION_STATE_CONFLICT","message":"建议已失效"}
```

事后核对：`SourceFact` 总数未因提交尝试变化；未创建任何提案。

推测层读取：

```text
GET /api/personal-family-view?space_id=1
→ inferred_edges: []
```

（预置的冲突 `direct_sibling` 推测边未被展示。）

### 场景 2：双方已共享家庭仍推荐共建（lineage 空间「验收家族」id=2）

`验收丙` 与 `验收丁` 已在 household 空间「验收家庭」（id=3）同为 active 成员，且在 lineage「验收家族」中有 confirmed spouse。

| 卡片 | 场景 | 预期 | 实测 |
| --- | --- | --- | --- |
| id=1 | 双方已共享 household | 不可行动 | 列表返回 0 张；通知显示「已撤销」 |
| id=2 | 另一对（`验收戊`/`验收己`）无共同 household | 保留待处理 | 列表返回 1 张 `household_link state=pending` |

浏览器实测（`验收丙` 登录）：

- 「待我处理」分区：`没有等待你处理的待办`（共建卡未出现在可处理区）。
- 「通知」分区仍保留该通知行，领域状态显示 **已撤销**，未删除历史、未重置已读状态。
- 侧栏与顶栏空间选择器正常，页面无 JS 报错导致的空白。

`验收戊`（对照）登录后：

- 「待我处理」出现 1 条 `共同家庭空间推荐待确认`，状态 `待处理`，带「去处理」按钮。

### 场景 3：命令侧拦截（隔离 API 直测）

```text
POST /api/action-cards/1/accept   → HTTP 409 CARD_EXECUTE_REJECTED
                                    detail.reason = household_already_shared
POST /api/action-cards/1/execute  → HTTP 410 CARD_EXPIRED (state=pending)
```

事后数据库核对：

```text
card id=1 space=2 kind=household_link state=pending   ← 未被 accept/execute 改动
card id=2 space=2 kind=household_link state=pending
household 空间数: 1                                    ← 未创建重复家庭
```

## 结论

| AC | 浏览器/隔离实测结论 |
| --- | --- |
| AC2 | 冲突建议在「待核实」分区消失；详情为 `superseded` 且无 submit；提交 409 且零副作用 |
| AC4 | 已共享家庭的共建卡不出现在待处理列表；通知显示已撤销 |
| AC5 | 对照项（无冲突建议、无共同 household 的共建卡）完整保留 |
| AC6 | 旧卡 accept/execute 均被拒；未创建第二个家庭空间 |
| AC9 | 隔离栈真实浏览器验收通过；截图见 `acceptance-*.png` |

## 未验证与限制

- 前端**无代码改动**（`git status` 确认），验收证明的是既有前端正确消费已有 `superseded`/`revoked` 状态。
- 未做移动视口截图；页面在桌面视口下布局与分区正常。
- 未跑真实模型：本任务屏障是确定性代码，不依赖模型行为。
- 未部署；线上行为未验证。
