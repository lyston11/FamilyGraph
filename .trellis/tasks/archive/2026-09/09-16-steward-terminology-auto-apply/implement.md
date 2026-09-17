# Implement — Steward 称谓自主优化

## 阶段与顺序

PRD/design 已定稿；代码已在 `feat/09-16-steward-terminology-auto-apply` worktree 内实现、检查并合入 `main`
（`b4f1283` → `f98e824` → `7480367`），迁移 `0050` 已在服务器生产库应用。
本文件记录执行结果与验收证据；AC7 见第 5 节的阻塞结论。

### 0. 完整加载上下文

- [x] `steward-action-card.md` 超过自动注入的 32768 字节上限；实现/核验 agent 已分段 read 全文。

### 1. 称谓正确性（AC1–3）

- [x] 写有独立期望值的回归：父/母展开的兄弟子女、父母兄弟姐妹、堂表亲、配偶方向、已知与未知性别/长幼（`tests/test_terms.py`）。
- [x] 覆盖反例：Uam/Usm/Ug、D-U、B-D 不误折叠、重复节点/不可见中间节点、伴侣和桥、保留原路径与 concept。
- [x] 实现 Terms 小型查词别名 helper；同步精确/前缀查词、DB 按需加载、snapshot、model allowed_terms、哈希及版本。
- [x] 修正四条配偶旁系内置词；新增存量修复迁移 `0050_term_alias_spouse_fix`（不改旧迁移）。
- [x] 盘点真实样本剩余常见长链（隔离库 870 目标 / 124 概念），保存覆盖矩阵；见第 5 节。

### 2. 自动应用与无效建议（AC4/5）

- [x] derived baseline 不再生成同值建议；真实本人用词与模型 override 继续自动生效。
- [x] 旧 baseline-only 建议在 list/detail/submit/restore 共同失效；不删反馈，不加 TTL，不伪造 resolved。
- [x] 用实际 job 回归：模型写回→发布→连续两次 core→真实 API 保留结果；个人/空间优先、恢复抑制、旧版本冲突、撤权和两 viewer 隔离。

### 3. 非待办消费（AC1/5）

- [x] 通知待核实列表排除 term_preference，后端把该 kind 的 `pending` 投影为 `done`，保留关系待办及全通知去重。
- [x] 档案称谓区明确自动生效，可选“固定为我的叫法/恢复默认叫法”；直接 baseline 改善不虚构投影或来源。
- [x] 前端回归：无需任何点击已有新称谓、可选操作、历史称谓不占待办、真实关系仍需处理、账号/空间/目标切换不串线。

### 4. 质量门与独立核验（AC6/8）

- [x] 隔离 DATA_DIR 升级到 head，验证旧错误 locale 行、已有正确行、用户词条/usage/反馈的保留；`tests/test_term_spouse_direction_migration.py` 精确回归。
- [x] fake transport 走真实 job 到 API 的全链；取消/权限漂移、unknown 不重发、预算和开关关闭回退用原回归验证。
- [x] 独立核验发现并修复“别名码接受了其他原码上的 space 自定义词”的越界缺陷（`7480367`），补模型词表层级回归。
- [x] 更新相关精确 Spec 叶（`steward-action-card.md` 模型候选词表层级、`state-management.md` 非待办消费）。

### 5. 服务器部署、启用与实际验收（AC6/7/9）

- [x] 核对部署 env、平台 feature 行、空间设置、Provider 和预算的有效值（仅布尔值/安全原因码）。
- [x] 当前 Provider 的合成关系真实模型验证在服务器隔离库运行，加载部署 env 后显式覆盖 DATA_DIR，断言 engine 路径。
- [x] 分支提交、主检出串行 merge；无 migration 序号冲突。
- [x] 生产在线备份，隔离副本演练迁移/刷新，再正式 upgrade head（`0050`）。
- [x] 远端同步、重启 `systemctl --user familygraph-api`；健康、业务 API、规则版本与发布状态共同验证。
- [ ] **沿治理路径启用 terminology：本任务判定不启用**（见下）。启用前提“存在可改善目标”当前不成立。
- [x] 真实模型结果自动应用：已证明调用链与校验器可用（合法同义 3/3 通过校验），真实模型对全部候选弃权。
- [x] 真实模型无合格结果时明确阻塞 AC7，未降低校验或伪造通过。
- [x] 无变化再次调度不重复请求、核心重算不抹掉模型成果；观察期间服务器服务保持 active。

**AC7 阻塞结论**：隔离库 870 条已发布目标 / 124 个概念中，不存在“层级更高或长度更短”的合法改善候选；
真实模型在 4 个 viewer 上均返回空 `items`（等长同义替换按提示词要求弃权）。因此无法产生
“至少一个合法真实模型改善被自动消费”的实例。按 AC7 要求标明阻塞，不以健康 200 或 fake 成功替代。
证据见 [model-acceptance-2026-09-17.md](research/model-acceptance-2026-09-17.md)。

**不启用生产开关的理由**：开启后只会对等长同义候选发起真实调用并一律被模型弃权——产生 token 消耗与云数据外发、
零用户可见改善；且新建 steward 行需写 `cloud_allowed=True`，属于空间云同意范围扩大；还需重启服务。
综合“零收益 + 云同意扩大”，不擅自开启，交由用户决定。

### 6. 收尾

- [x] PRD 每项验收绑定执行证据，记录未运行的高成本验证及原因。
- [x] 主检出运行 `task.py validate` / `archive`；提交并推送工件。
- [x] 确认已合并、worktree 干净后删除任务 worktree 和分支；清理本任务隔离库/探针。

## 回滚点

- 词义/别名测试不通过：不进入后续生产配置启用。
- 存量迁移演练失败：保留生产原样，修复后重试隔离验证。
- 模型失败：保持 terminology 开关关闭，确定性称谓继续可用；不恢复错误的内置词、不删除用户偏好。
- 本期关键文件：terms.py、term_registry.py、steward_terminology_snapshot.py、steward_terminology.py、steward_suggestions.py、migration 0050、NotificationsView.vue、KinshipTermPanel.vue。
