# 第四类模型辅助的接入证据

基线：origin/main@4649e48。只读独立代理核验，主线程抽查 register_batch、fence、ModelCall 和 platform effective 分支。下表用于 B 的 implement/check 上下文。

| 接入面 | 现有位置 | 必须完成 |
| --- | --- | --- |
| kind/输出预算/prompt | services/steward_assist.py:63,66,104 | 新 terminology，candidate 原子边界保留 |
| batch 注册 | steward_assist.py:447–519 | core 已在 :1025 重建 PFV、:1091 注册；新 viewer 工作进入同批 |
| reserve | steward_assist.py:597,657–782 | 新分支、目标上限、原总预算、公平预留 |
| network | steward_assist.py:887 | 沿事务分段、Provider/出站、token 与错误收敛 |
| prompt 重建 | steward_assist.py:1089 | 目前默认落 explanation；terminology 显式读取服务端 viewer/targets 并重验 input_hash |
| validate/apply/recover | steward_assist.py:841,1116,1228 | 专用 schema/语义/版本；恢复不重发 unknown，只重放获准产物 |
| 授权输入 | steward_assist.py:321–335 | 现有 _visible_context 是 space-wide，必须独立 viewer 路径输入 |
| fence | steward_assist.py:525–578 | 目前只有 candidate 校验 facts，不能视为现成个人版本栅栏 |
| SQL 模型 | models/steward.py:348；models/agent_provider.py:102；models/platform_features.py:23 | 新 migration 扩 CHECK/配置/个人投影；历史迁移不改 |
| 平台治理 | services/platform_features.py:23–125；schemas/platform_features.py；api/admin_platform_features.py | DB∧environment、生效来源、GET/PUT/审计、遗漏字段保持 |
| 空间设置 | schemas/agent.py:302,398；api/space_model_settings.py:76,177,202,240；api/admin_agent.py:361 | 第四字段与 steward 专属校验；不新增 agent_kind |
| 家庭前端 | types/agent.ts:127；api/spaceModelSettings.ts:27；SpaceModelSettingsPanel.vue | 类型、decoder、控件和有效状态 |
| 管理前端 | types/api.ts:395；PlatformFeaturesView.vue | 平台控件、来源和兼容 |
| 健康/统计 | api/admin_steward.py:136；api/admin_agent_latency.py:78 | assist_any 增 kind；动态 kind 统计复用，不泄露称谓正文 |

核心回归入口：test_steward_assist 的预算/运行中关开关/证据变化/恢复；test_steward_assist_platform_governance；test_steward_guard；家庭 SpaceModelSettingsPanel 和管理 platform-features.view 的测试。

默认每 job 6 calls/20,000 tokens；不能为了第四类型无条件扩大总额度。新 schema 合格不代表称谓准确，需独立语义反例和真实生产链证据。
