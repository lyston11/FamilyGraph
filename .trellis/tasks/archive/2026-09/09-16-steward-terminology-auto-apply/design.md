# Design — Steward 称谓自主优化

## 1. 依据与修正

产品真源为本任务 PRD、`.trellis/HANDOFF.md` 当前称谓合同及 09-13 称谓闭环归档。用户已明确要求自主应用，通知批准不是前置条件。

本轮纯函数探针确认：`Um-Dm-Dm → 兄弟的儿子`，`Bm-Dm → 侄子`；`Um-Um-Dm → 爷爷的儿子`，`Um-Bm → 叔伯`。`Sf → 妻子` 而现有 `Sf-Bm → 丈夫的兄弟`，为真实词表方向错误。

修正此前三个过强判断：

1. `_suppression_concept` 只折叠起始 U-D，不能原样复用为任意位置的查词算法；更不能更换旧 suppression key 使用户恢复记录失效。
2. baseline 改善本来就直接生效，无需强制创建非空投影；0 条 override 不等于全部确定性计算没生效。
3. 归档 design §2 曾明确允许 derived baseline 的“可选保留”。本任务按用户要求主动收紧这项产品行为，不能把历史实现一概称为未实现设计。

## 2. 保留架构

```text
已授权 confirmed 路径
  → Terms（原始 concept + 安全词条别名查询 + 长幼/长链降级）
  → 确定性 baseline ────────────────────────────┐
  → 当前 viewer 的合法候选 → 既有 assist → 校验 → 自动投影
                                               ↓
                       personal/space 优先的有效显示选择器
                                               ↓
                         PFV / 档案 / 个人关系 presentation
```

普通 GET 仍只读，不调用模型。Steward 沿既有发布、detached delivery、CAS、后台刷新运行，不另起 runtime。模型结果只改善显示，不形成亲属事实。

## 3. Terms 安全别名与词表

### 3.1 别名不改事实码

在 Terms 层新增小型纯 helper，枚举原码及安全归一后的查词别名。仅允许无 a/s/g 亚型的 `U|Um|Uf` 后紧邻 `D|Dm|Df` 转成 `B|Bm|Bf`（性别来自下行目标）。支持片段位于路径中段，例如 `Um-Um-Dm → Um-Bm`。不得折叠 B-D 为 B，不得折叠 D-U，不跨 S/P/X，不消除未知性别，不加入长幼断言。

展示使用的路径须来自 resolver 的无重复节点路径，或先通过现有 current_target_context 路径重验；原始 path、concept_code、DerivedFact 和用户词条主键都不变。新 helper 是查词用等价表达，不是关系图改写器。

先保留原码上的显式 personal/active space 词条；自动别名只选 locale/system 词表，不把别处个人自定义词的作用范围悄悄扩展到另一个原码。精确命中优先于同层别名命中；原码未命中时，在泛化前查询别名命名词。未知或有亚型的路径仍逐段保留其含义。长幼计算始终用原始码/原路径索引，不能把折叠后的索引套进未折叠路径。

### 3.2 全链同步

不是仅改 `resolve_term_from_snapshot` 一处：

- `resolve_term_or_structural` 的按需 snapshot 加载纳入原码、命名前缀及对应别名，否则实时 API 与后台全量 snapshot 会不一致。
- `_generalized_term` 可利用安全等价命名前缀，使长链尽量用已有自然词组合，仍保留 64 字限制与无词降级。
- `steward_terminology_snapshot.allowed_terms/candidate_terms` 使用同一查词别名规则，模型能选出的词必须与确定性语义一致。
- 词条版本哈希覆盖实际读取的别名词条；显示规则版本和 terminology 规则版本按影响升级，旧 PFV/投影经原失效机制重算。
- 现有 suppression key 保持兼容；新增规则不能绕过曾恢复的同语义同词。若需要兼容别名查抑制，保留旧键查询并补等价匹配，不直接重写所有历史键。

### 3.3 四条错误种子与覆盖盘点

修正 `Sm-Bm/Sm-Bf` 为丈夫的兄弟/姐妹，`Sf-Bm/Sf-Bf` 为妻子的兄弟/姐妹。未知年龄时不擅自改成“大伯子/小叔子/大舅子/小舅子”。

`seed_builtin_packs` 只补缺失行，故只改 Python 常量不能修存量。新增当前 migration head 的后继迁移，匹配 level=locale、locale=zh-CN、无个人/空间归属、精确 concept+旧错误 text 的行，修正词并增加 revision；正确行已存在时处理重复而不删除引用历史，不碰 personal/space/usage。遵循现有迁移和输入版本触发器模式，隔离新库、旧库及重复状态验证后部署；不改历史迁移。

原注释指向的 `scripts/gen_term_pack.py` 当前不存在，实施时搜索实际生成器；若无源文件则修正陈旧注释，不为此新建词包生成系统。对当前剩余常见概念形成覆盖清单，先复用已存在的标准词和安全组合。需要增加新常见标准词时记录具体路径语义、词义来源和正反例，同迁移发布；新地区包仍属 09-11 后续任务。

## 4. 自动应用与建议生命周期

`_apply_target` 去掉 `baseline_source == derived` 时无条件创建 baseline 建议的分支。本人明确合法用词及模型改善仍沿既有 upsert 写投影；核心重算不清掉仍有效的模型成果。合格结果必须与 baseline 不同，模型 shorter_chain 仍满足长度和语义校验，synonym 不以“长度短”当作唯一正确标准。

`_current_term_suggestion` 不再把 status=unchanged、term=NULL 且只引用 baseline 的记录当成当前可操作建议；它们通过现有 effective_state 显示 superseded，提交/恢复同样重验。这样部署立即退出活跃消费，不依赖一次性生产删库。模型 active override 及真实本人用词建议保留；resolved、dismissed、restored 历史记录和抑制键不改。

后台重算可继续刷新已存在投影元数据，但不能重建无信息量建议、伪造“已采用”或新增 TTL。本任务不把 garbage collection 扩展成历史数据删除项目。

## 5. 前端消费

- `NotificationsView.pendingSuggestions` 排除 term_preference；真正 relation_proposal 等仍按原有效态和全通知 ID 去重，不回退上一轮去重修复。
- 既有通知分类/计数确认 term_preference 为信息性历史，不得进入待处理/待核实；需要修改时统一类型分类函数，避免单页面伪装计数。
- `KinshipTermPanel` 自动投影区用“管家已自动优化”明确当前状态；“固定为我的叫法”解释为本人显式偏好，跨空间按既有 concept 生效；“恢复默认叫法”沿原 CAS/幂等流程。
- baseline 直接改善时不显示虚构模型来源、不创建多余建议；用户已有“改口/我就这么叫”入口继续可用。
- 保留 generation/账号/空间/目标切换防迟到保护，反馈后刷新树、档案和通知呈现；不修改树的布局或实际端点语义。

## 6. 配置启用与实际模型

代码安全验证后，在服务器既有部署 env + 平台 feature + 目标空间 terminology 设置启用本能力，保留其他开关、Provider 和预算。读取当前有效状态而不是只看环境文件：有平台行时环境与平台取 AND，还受空间开关、runtime、Provider、cloud_allowed/local_required 限制。

使用现有治理 service/API 写配置并保留审计，不在任务文件记录密钥。只启用获授权部署范围的既有空间；无云许可的空间不绕过许可，继续确定性服务并报告阻断。先用当前 Provider 对合成授权关系在隔离 DATA_DIR 跑有限真实调用，再部署、通过正常后台 job 观察真实空间。不能为了制造“改善率”反复请求同输入或关闭校验。

验收分别记录：是否有效启用、调用是否成功、输出是否语义合格、是否自动写回、用户 API 是否消费。真实模型空输出是允许行为，但不足以证明真实自动改善；保留该验收项未完成直到有合格实例或明确上游阻塞。

## 7. 发布与回滚

1. 单任务 worktree 内实现与回归，独立 check 后串行合 main，先备份并验证迁移。
2. 远端同步代码、执行新迁移、重启 user-level backend；通过原规则版本/刷新机制驱动重新发布，不手写生产 PFV 或关系事实。
3. 前端主检出 Vite HMR 路径与远端服务各自确认；不额外启动本地后端或消费生产队列的 sidecar。
4. 记录部署前后少量授权目标的 raw concept / baseline / effective term / origin / job 与版本，输出不包含未授权原文。观察无新增称谓待办及模型预算合规。
5. 回退先关闭 terminology 增强，保持确定性可用；代码回退与规则版本一并验证。正确词表不因代码回滚恢复错误方向，不破坏性 downgrade 用户词条/反馈。若迁移演练失败，不上生产。

## 8. 实施边界与风险

共享 Terms 意味着必须验证按需解析和 snapshot 两种调用、模型 validator 与缓存失效，而非把 `_suppression_concept` 加到一个查找函数就结束。扩大词表匹配可能暴露其他现有错误；本期以受影响标准词的人工语义断言与反例覆盖，不承诺“所有复杂关系唯一命名”。服务器有效配置和真实模型质量为部署阶段验证项，历史计数仅提供背景。
