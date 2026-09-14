# 现状证据与设计约束

日期：2026-09-13。主线程与三个独立只读核验子代理汇总；主线程抽查事务、指纹、PFV 构建、结构/称谓分离及视口更新原文。未运行性能测试、修改业务代码或操作生产环境。

## 1. 持锁与重复计算

| 证据 | 已确认行为 | 对方案的约束 |
| --- | --- | --- |
| `backend/app/services/steward.py:831` / `run_steward_job` | 单个立即事务包住运行和结算，`:859` 调用 `_execute_locked` | 不能只更换线程、增大 timeout 或改成延迟取得同一个长写锁 |
| `backend/app/services/steward.py:1022`、`:1218` | 每次全量遍历可见人物有向对，30 人最多 870 对，14 人 182 对 | 当前查看者最多 29 个非自身目标，不应等待全部 870 对 |
| `backend/app/services/derived_facts.py:133`、`:167` | 先完整 resolve，再判断缓存命中 | 必须先核验完整输入指纹，再决定是否搜索路径 |
| `backend/app/services/relationship_resolver.py:333`；`personal_family_view.py:203` | 每个 pair 再次 `load_graph`，PFV 也再次解析 | 同 viewer 的授权图构造与结构解析结果需要复用 |
| `backend/app/services/relationship_resolver.py:118`、`:157` | 深度 1–12 迭代 DFS；128 上限限制已找到路径数，不限制搜索展开量 | 必须有可达性预检查、可取消计算与调度预算；预算耗尽不等于无路径 |
| `backend/app/services/steward.py:1278`、`:1543` | finding 和推荐使用 confirmed facts，不依赖完整 DerivedFact 矩阵 | 可将未被消费的矩阵缓存预热降为低优先工作；核心必需工作清单需明确定义 |

`backend/app/services/derived_facts.py`、`services/terms.py:1011`、`services/agent_query.py:623`、`api/kinship.py:149` 的检索表明公开消费者按 pair 获取派生关系，缓存缺失已有计算路径。此次没有发现必须在读取任意 pair 前完成整矩阵的 API 合同；这不是全仓库语义证明，实施时仍需回归上述消费者。

用户提供的线上样本：30 人六代，16.7 秒；本轮未复测。历史容量证据 `.trellis/tasks/archive/2026-09/09-11-steward-release-observability/release-evidence.md:59` 使用本机、稠密合成样本的 viewer 行采样；200 人 4.6 小时是线性外推，绝非完整任务实测，也不能直接作为本次稀疏六代家谱的预计耗时。

## 2. 快照与版本不能直接沿用旧 hash

- `relationship_graph.py:169` 的 `_visible_node_ids`、`:287` 的桥接授权均依赖 viewer。不能把未按权限剪枝的全族图当作所有人的共同图。
- `relationship_graph.py:433` 的 hash 只显式覆盖 facts `(id, revision, type)` 和 bridge `(id, revision, spaces)`；`derived_facts.py:42` 再加入算法版本。
- `personal_family_view.py:110`、`:132` 再加入词典行 revision、policy 和 computation。性别、可见节点集合、出生/长幼、成员/ref/披露、推测开关及状态等没有被统一覆盖；性别参与 concept 编码（`relationship_resolver.py:193`），出生信息参与称谓（`personal_family_view.py:249`）。
- `relationship_graph.py:279` 明确推测边不进入原缓存 hash；`domain_events.py:36` 的 PFV 前缀不包含 `steward.inferred_*`，`api/space_model_settings.py:246` 的推测设置更新需要纳入新版本传播。
- `relationship_graph.py:292` 的桥接到期、`visibility.py:284` 的成年边界随时间变化；新 fence 需要 `valid_until`，单靠事件水位无法捕捉。
- `backend/app/db.py:34` 为 `expire_on_commit=False`。快照要显式短读事务，脱离 Session 后只携带不可变数据；发布和续租必须用新读取/数据库 CAS，不能检查旧 ORM 实例。

## 3. 现有安全空态与渐进显示

- `personal_family_view.py:504` 先检查当前成员资格；`:644` 对非 current 或版本漂移返回安全空内容；`:667`、`:731` 再验节点权限与路径中间证据。
- `personal_family_view.py:792` 明确“撤权事务内同步失效”。这项 fail-closed 保证必须延续至预览、ETag 和轮询。
- `personal_family_view.py:695` 当前推测读取只复核 proposed/端点，未完整重验 viewer_path 中间证据及 effective enabled；渐进协议不能继续放大该缺口。
- `models/personal_family_view.py:74`、`:95` 的现有子行没有 generation 隔离；`services/personal_family_view.py:193` 先删后重建。因此直接每批覆盖当前行会混版或暴露半成品。
- `api/personal_family_view.py:61` 对最终授权载荷生成 ETag，`:79` 只有 current 可 304；未准备好时调用独立短事务登记重算，GET 不承担图计算。

## 4. 前端复用点与缺口

| 位置 | 行为/问题 |
| --- | --- |
| `frontend/src/composables/useFamilyTreeCanvas.ts:124`、`:159` | `topology_edges` 负责确认连线，summary `edges` 负责“与你的称谓”；节点没有称谓仍可存在 |
| 同文件 `:190` | 完整 confirmed topology 决定世代布局；缺 topology 时依赖摘要路径回退，不能在渐进协议中省略该字段 |
| `frontend/src/views/FamilyTreeView.vue:354` | 每次 positionedNodes 更新触发 `fitToMembers`，渐进批次会不断重置视口 |
| 同文件 `:175`、`:303`、`:390` | 坐标重建、面板持旧边引用、任意错误卸载画布，需要处理标签补齐和短暂网络错误 |
| `frontend/src/stores/personalFamilyView.ts:58` | 仅有清缓存 epoch，无同空间请求序号/版本防倒退；慢响应可覆盖新响应 |
| `frontend/src/api/personalFamilyView.ts:340` | 已有条件 GET 和运行时 decoder，可扩展版本协议 |

检查 PFV API/store、FamilyTreeView、useSpaceContext 与对应测试：当前没有 PFV 自动轮询/SSE，`next_cursor` 没有实际翻页消费。`useAgentStream.ts:14`、`:154` 有 Agent Run 专属 fetch SSE，不是通用 PFV 通道。首版可以使用条件 GET 轮询，不必为此搭建新实时消息系统。

## 5. 独立设计核验发现

1. 预览已完成 pair 与官方发布/作业成功必须分开；`pending/found/no_path/failed` 不能靠行是否存在推断。公开完成数只统计该 viewer 已获授权的目标。
2. `steward.py:593` 的续租缺 expected attempt，`:952` 的失败回队也没有重新检验完整 lease fence。长期事务外计算让迟到失败、旧 worker 续租成为真实竞争，所有状态写入都要 CAS。
3. `steward.py:587` 固定 execution cursor，`:873` 结算固定上界，`:903` 登记后继；预览批次 checkpoint 不得替代消费水位。supersede 与保留最高待处理水位必须同事务。
4. `_execute_locked` 的 finding、suggestion、inferred、卡片、辅助登记也要纳入短事务设计；不能仅优化 DerivedFact/PFV，却把全量扫描、JSON 构建或逐行写放在最终提交中。
5. 最终提交应切换已准备的 generation 指针，而不是把 staging 全部拷贝到 live 表；旧 generation 回收也要分批。通知和模型调度须等完整发布后才能生效。
6. 长 read transaction 会阻碍 WAL checkpoint；快照复制完成后关闭事务。CPU 计算采用有界执行与独立续租/维护节奏，避免只是把数据库等待变成 CPU/调度饥饿。

## 6. 相邻任务边界

- `09-11-steward-capability-followups` 已记录容量研究，但尚无安全的增量计算实现；本任务作为已发生 P1 可用性问题的具体根治，引用其历史证据，不修改其既有文件。
- `09-13-steward-kinship-presentation`、`09-13-steward-terminology-autonomy` 规划统一个人称谓、投影版本和通知语义。本任务负责调度/版本/渐进协议，不新建第二套称谓算法；两者如进入同一批 PFV/Terms/Steward 文件，必须串行集成。
- 本规划使用一个一致性主任务承载后端与前端协议；实施阶段可按有依赖的步骤交付，不能在共享模型、迁移编号或数据库上并行写入。
