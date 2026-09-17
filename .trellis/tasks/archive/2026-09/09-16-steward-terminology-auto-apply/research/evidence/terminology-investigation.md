# 本轮核查证据

源码基线 main@621284d；只读源码检查与纯函数探针，无生产数据库访问/修改。

## 诊断方法

在 backend/.venv/bin/python 运行，先建立 TemporaryDirectory 并设 DATA_DIR，再导入 Terms；以 BUILTIN_TERM_SEEDS 构建 TermSnapshot，调用 resolve_term_from_snapshot 和 _suppression_concept。没有建立 Session 或连接数据库。

实际输出：

```text
Um-Dm-Dm 兄弟的儿子 derived suppression= Bm-Dm
Bm-Dm 侄子 locale suppression= Bm-Dm
Um-Um-Dm 爷爷的儿子 derived suppression= Um-Um-Dm
Um-Bm 叔伯 locale suppression= Um-Bm
Sf-Um-Dm 岳父的儿子 derived suppression= Sf-Um-Dm
Sf-Bm 丈夫的兄弟 locale suppression= Sf-Bm
Sm 丈夫 locale suppression= Sm
Sf 妻子 locale suppression= Sf
Uam-Dm-Dm 养父的儿子的儿子 derived suppression= Uam-Dm-Dm
Bm-Df 侄女 locale suppression= Bm-Df
```

此结果证明当前行为及局限，不是修复后的回归检查结果。尤其 `Bm-Df` 是侄女，不能照抄先前答复里的错误 Bf 归一例子。

## 源码依据

- `backend/app/services/relationship_resolver.py:379-405`：_step_token 的 S/B 与目标性别编码，保留原始 concept；搜索过程 frame.visited 防重复节点。
- `backend/app/models/term_registry.py:134-137`：四条配偶旁系方向错误；同文件 Sm/Sf 与 Sm-Um/Sf-Um 给出直接矛盾证据。
- `backend/app/services/terms.py:179`：seed_builtin_packs 只补缺失行；新常量不足以修存量。
- 同文件 `resolve_term_or_structural`（1044）、`resolve_term_from_snapshot`（1077）、`_generalized_term`（974）：按需加载前缀、精确命中、长链降级。
- `backend/app/services/steward_terminology_snapshot.py:180-241`：allowed_terms 与 candidate_terms 目前用精确码，_registry_hash 按 code 筛选；新别名必须同步三个入口。
- 同文件 `current_target_context`（248 起）：验证获权路径、不可重复节点、建立 baseline、候选和 semantic/request hash。
- `backend/app/services/steward_suggestions.py:666-744`：unchanged projection 允许 derived baseline 建议保持可选保留；effective_state（921）统一无效化，其结果供读/动作消费。
- `frontend/src/views/NotificationsView.vue:60-85`：activeForSpace 投影行不分 kind 纳入待核实；全通知引用 ID 去重是应保留的修复。
- `frontend/src/components/kinship/KinshipTermPanel.vue:405-450`：可选保留/恢复的既有前端入口。
- `backend/tests/test_steward_terminology.py:177`：baseline 已正确时无投影、无建议但 PFV 仍显示“外婆”，证明不能要求非空 projection 才验收改善。

## 合同依据与旧判断修正

- `.trellis/HANDOFF.md` 当前合同要求称谓改善自动生效、个人/空间优先、真实关系仍确认。
- 09-13-steward-terminology-autonomy/design.md §2 明确保留 derived baseline 的可选建议，本轮将其收紧属于用户授权的 UX 行为修订。
- `.trellis/spec/backend/relationship-intelligence.md` 规定 concept/路径与个人偏好优先；steward-action-card.md 保留 no TTL、发布隔离及自动称谓与 Memory 的不同治理。
- 旧会话给出的生产计数及启用状态尚未在本轮重新测量，不作为当前实测结论。
- `scripts/gen_term_pack.py` 在当前工作区不存在，旧词包注释不能当作现存生成器。
