# A 前端独立核验与自修

日期：2026-09-13。检查基于 `20d0308` 的任务 worktree 当前实施内容；检查者为 `memory_frontend_check`。本记录描述前端检查，不代表后端迁移、真实 HTTP smoke 或整个父任务已通过。

范围：`frontend/src/{api,types,stores}/memory.ts`，MemoryEditorDialog、MemoryCandidateConfirmDialog、MemoryManager、MemoryCardItem、MemoryRagPanel 及对应测试。只读追踪了 `api/client.ts`、`stores/auth.ts`、`composables/useSpaceContext.ts`、`stores/agent.ts` 和 CitationList 的调用边界。自修仅写 store、MemoryManager、两者的现有测试及本记录；未编辑后端、smoke 或其他 agent 的文件，未提交。

## 发现并修复

| 发现 | 可复現路径 / 原结果 | 当前修复位置与结果 |
|---|---|---|
| 组合刷新与独立刷新没有共用请求顺序 | 延迟 `loadForSpace` 的候选响应，先让新的 `loadCandidates` 返回 `unavailable`；旧响应随后把正文恢复为 `available` | `frontend/src/stores/memory.ts:184` 组合刷新复用相同的候选与记忆读取路径，候选序号统一；新投影保持 `unavailable`、`raw_quote=null` |
| 私有列表与共享列表旧响应可覆盖权限新状态 | 旧私有读取晚于新遮蔽结果返回；或 `ensureMemories` 晚于撤销后的组合刷新返回 | `frontend/src/stores/memory.ts:139` 统一 `loadMemoryProjection`，私有序号与各空间序号分别校验；旧正文不能恢复 |
| 清理空间后迟到组合响应会重建分区 | `loadForSpace(5)` 未完成时 `resetForSpace(5)`，旧响应返回后再次 `partitionOf(5)` | 同 helper 在请求开始捕获分区对象，回写前校验 `Map.get` 身份；空间 5 不重新出现，空间 6 原对象和缓存保持 |
| 旧 mutation 在账号/空间清理后继续 GET | 撤销 POST 未返回时 `clear()` 或 `resetForSpace(5)`，旧 POST 成功后重新拉取 | `frontend/src/stores/memory.ts:199` 在写后、刷新后核验发起账号 generation 和分区身份；清理后不发任何候选/记忆 GET，不回写新会话错误 |
| 失败读取仍展示旧正文；旧异常污染已清理账号 | 列表已有可读正文，刷新返回 403；或私有读取在 `clear()` 后才拒绝 | `frontend/src/stores/memory.ts:156` 只处理当前请求错误，清除相应正文/loaded 缓存；旧账号异常不进入新 store |
| RAG 开关刷新后旧结果重新出现 | 原结果与旧 search 请求保留，RAG 关闭后再开启，旧 search 最后返回 | `frontend/src/stores/memory.ts:93` 能力刷新与成功 mutation 共用检索缓存失效；清除结果并推进查询序号，旧请求无权回写 |
| 已卸载页面继续读取新会话 | MemoryManager 等待能力响应时卸载并清空会话；新会话恢复 enabled 后旧能力请求返回 | `frontend/src/components/memory/MemoryManager.vue:103` 页面卸载/新 load 推进加载序号；旧页面不继续请求记忆或候选 |

`partitionOf` 同时保证首次创建返回 Map 中的响应式对象，而非未经代理的原始对象；这是分区身份比较和首轮响应式更新成立的必要条件。缓存清除不把状态伪造为已确认/已撤销，最终数据仍来自服务端。

## 独立验证证据

第一批新增 8 项受控异步测试，修复前运行真实 memory store：**8 failed / 原 18 passed**。失败明确观测到 `unavailable → available`、撤销后恢复 active 列表、清理后分区重建、清理后额外 GET、旧正文/旧错误残留。修复后这些断言通过。

第二批覆盖 RAG 状态切换与已卸载页面的 2 项测试，加入时 **2 failed / 48 passed**；对应修复后通过。并发顺序由可手动 resolve/reject 的 Promise 固定，没有随机等待碰撞。

最终验证在本 worktree `frontend/` 执行：

- `npm test -- src/stores/__tests__/memory.spec.ts src/components/memory/__tests__/MemoryManager.spec.ts src/components/memory/__tests__/MemoryEditorDialog.spec.ts`：**56 passed**，其中 store 27、Manager/CitationList 23、Editor 6。
- `npm run lint`：通过。
- `npm run type-check`：第一批修复后独立通过；最终修改又经 build 内的 `vue-tsc --noEmit` 通过。
- `npm run build`：通过，Vite 6.4.3、4660 modules。

新增用例位于 `frontend/src/stores/__tests__/memory.spec.ts:402` 起和 `frontend/src/components/memory/__tests__/MemoryManager.spec.ts:208`。既有 Editor 用例保留真实 component → store → API → Axios 序列化，仅替换 transport；确认了显式 manual/rag source、body 幂等 key、同内容重试保留 key、内容改变产生新 key、创建/读取失败不伪成功。

## 合同检查结论

- `MemoryRagPanel.vue:70` 保存时从 `currentSpace.id` 构造 `source.space_id`，不读 nullable 的结果 `space_id`；document/chunk/revision/index_version 保留。
- `MemoryEditorDialog.vue:108` 非 manual 请求不发送客户端原文；原话展示只读，敏感度选择也锁定。手工请求显式 `source.kind=manual`，没有借缺来源自动降级。
- 确认选项取服务端 `allowed_scopes` 与当前真实空间 kind 的交集；来源不可用/未验证正文被列表、卡片、确认弹层共同遮蔽。确认弹层按 ID 从 store 取最新对象。
- Memory/RAG 四种开关组合、能力读取失败、Memory off 时 RAG 仍能读且无法保存，都有现有组件/store 回归。
- `RagSearchResult` 的严格字段只用于检索结果；旧消息继续使用字段可选的 `MemoryCitation`。已检查 `stores/agent.ts` 的 parser 和 CitationList，无新增旧引用兼容阻塞。

## 未修复项与限制

本次限定前端范围内没有遗留阻塞发现。未运行全 frontend suite、浏览器手动交互或后端/迁移测试；相关 56 项、完整 lint/type/build 已覆盖本次变更，真实 listener smoke 与后端事务/授权验收由主线程负责。前端不能通过缓存规则推断服务端尚未通知的撤权；当前结果是否可保存仍由每次 API 来源回读最终判定。

建议主线程在现行合同/规范中记录：所有写同一投影的读取应共享请求顺序；`clear`/空间 reset 除了阻止回写，还必须阻止旧异步链继续发起新读取；能力状态重新确认时清除旧检索正文。历史 spec 的现有敏感缓存边界可作为背景，不把它重新提升为执行门禁。
