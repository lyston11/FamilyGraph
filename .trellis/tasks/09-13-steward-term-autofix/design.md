# 设计：管家自动修复称谓（词典补全 + 长幼消歧 + 自愈闭环）

## 0. 核心取向

修复发生在**解析层**，不在数据写入层：词典补全后，四级解析自然命中人话称谓；
管家例行的视图重算（每 ~5 分钟 + 事件触发）把结果带上树。**不新增词条写入路径、
不新增开关、零模型调用**——自愈是解析器变聪明的自然结果，不存在 09-12 式
「开关没开所以静默不生效」的陷阱。

## 1. 词典补全（R1）

### 1.1 生成方式

概念码字母表（U/D/B/S/P + s/a/g 限定 + m/f 性别位）可确定性枚举。新增枚举脚本
`backend/scripts/gen_term_pack.py`（离线工具，不入运行时）：

- 穷举 ≤3 跳全部可达码（含 step/adoptive/guardian 限定与性别组合），按中国亲属
  关系表生成规范称谓；
- 4 跳仅收直系与高频项（曾祖辈、曾孙辈、曾叔伯等），旁系 4 跳以上不收；
- 输出人工校对后的清单，落 `BUILTIN_ZH_CN_TERMS` / `BUILTIN_SYSTEM_TERMS`
  （`models/term_registry.py`，与迁移共享来源的既有机制）。
- 命名冲突规则：一词多码合法（词典按码索引）；同码多词取规范词，口语变体
  不入内置包（用户可用 personal/space 层自定义，KI-4 机制不变）。

### 1.2 迁移

新 Alembic 迁移把新增种子行写入（`seed_builtin_packs` 同款幂等：只补缺失行，
不碰 personal/space 用户数据）。system 兜底包同步补齐（如 `U`=尊亲长 已有，
补 `U-U`/`D-D` 等无性别泛化码）。

## 2. 长幼消歧（R2）

### 2.1 数据：展示口径（PURPOSE_GRAPH）收集出生年

**实现修正（2026-09-13，实现期发现）**：原计划把 `node_birth_years` 挂在
`relationship_graph.load_graph` 上，但图装载的节点可见性用 `PURPOSE_AGENT`
（上限 lineage_summary，birth 字段对旁系成员一律 masked），照此实现会让截图
黄金用例（妹妹/妹夫）永远拿不到出生年。称谓消歧是**展示层**关注点，改跟
PFV 展示同一口径：

- `personal_family_view.rebuild_view`：`_node_display` 已逐节点算
  PURPOSE_GRAPH 决定，改为同时返回决定对象，循环内收集
  `births[uid] = birth_from_user(target) if birth FIELD_CLEAR else None`；
- `relationship_graph.load_birth_years(session, viewer, space_id, user_ids)`：
  组合层（`compose_resolution_view` 主/替代路径）用同一 PURPOSE_GRAPH 口径
  的独立助手；`birth_from_user` 解析 `users.birth` JSON（仅 solar/lunar，
  取 ISO 年份）；
- 出生年只用于服务端长幼比较，**不下发任何日期到 payload/日志/称谓文本**；
  脱敏节点（未成年人 overlay、lineage 层）视为未知 → 泛化词。

### 2.2 消歧规则（services/terms.py，表驱动）

`AGE_VARIANT_CLASSES`：兄弟/姐妹类基码（`Bm/Bf/B` 及经父母链的 `Um-Dm/Um-Df/
Uf-Dm/Uf-Df` 等）+ 其单个配偶后缀（`-Sm/-Sf`）：

- 基跳年龄可判 → 哥哥/弟弟、姐姐/妹妹；后缀组合 → 嫂子/弟妹、姐夫/妹夫；
- 不可判 → 泛化词（兄弟/姐妹/兄嫂/姐夫妹夫泛称取词典泛化条目）；
- 触发条件：解析命中层级 ∈ {locale, system}（personal/space 用户词条优先级更高，
  永不覆盖）且 code 属于变体类；
- 实现位置：`resolve_term_or_structural` 新增消歧步（签名扩展：传入可选
  `variant_context`——基码长幼判定所需的两端出生年；调用方
  `personal_family_view.rebuild_view` / `compose_resolution_view` /
  `agent_query` 等从图快照取数。缺省 None = 行为与现状一致，全部调用方逐一核对）。
- **调用方核对结果（实现收尾定案）**：① `rebuild_view` 主路径已接（展示口径决定
  随 `_node_display` 收集）；② `compose_resolution_view` 主/替代路径已接
  （`load_birth_years`，PURPOSE_GRAPH）；③ `intake_extractor._candidate_view`
  **保持缺省 None**——intake 候选只有概念码、末跳常无对应人物（supported/
  ambiguous 语义），无可比较出生数据的具体人，消歧无对象，入包泛化词兜底
  即为正确行为；kinship API（`api/kinship.py`）经 ② 覆盖。

## 3. 长链泛化（R4 兜底，替代管家落词）

词典未覆盖的超长码（>4 跳旁系等）新增第 5 级解析：**最长命名前缀 + 残链小词**——

- 例：`Um-Um-Sf` → 前缀 `Um-Um`=爷爷，残链 `Sf`=妻子 → 「爷爷的妻子」；
  `Um-Df-Sm-Um` → 前缀 `Um-Df-Sm`=姐妹的丈夫（包内泛化词），残链 `Um`=父亲 →
  「姐妹的丈夫的父亲」（泛化不嵌套长幼消歧，实现期定案）；
- 残链小词表：U→父亲/母亲/家长、D→子女系、B→兄弟/姐妹、S→丈夫/妻子、
  P→伴侣、X→跨空间亲人（含 s/a/g 亚型词，`services/terms.py::_RESIDUAL_WORDS`）；
- 命名前缀沿用四级解析优先级（用户 personal/space 词条命中前缀同样尊重）；
  仍无法命名（无任何可命名前缀或残链含不可命名 hop）→ 维持现有结构描述
  （SOURCE_LEVEL_STRUCTURAL）不变；泛化产物标记 `SOURCE_LEVEL_DERIVED`；
- 纯函数、表驱动、可单测；组合超过 64 字（`_TERM_MAX_LENGTH`）→ 回退结构描述。

## 4. 自愈传播（R3 的实现形态）

- `COMPUTATION_VERSION` `pfv-v2` → `pfv-v3`：词典/解析升级即触发全空间视图重算；
- 管家核心作业的既有 `rebuild_space_views` 步骤无需改动——升级部署后第一轮
  integrity_scan 自动把新称谓刷进 PFV，前端下次拉取（ETag 变化）即显示；
- 无新开关、无新端点、无前端改动；`term_source_level=structural` 的边在升级后
  仅剩「命名前缀也命名不了」的病态码（预期为 0）。

## 5. 兼容与回滚

- 迁移纯增量（新增种子行）；回滚 = downgrade 删除新增行 + 解析层代码回退；
- personal/space 用户词条、KI-4 晋升机制、`resolve_term` 契约不变；
- 消歧/泛化均为缺省关闭语义（上下文缺省 None 时逐字节旧行为），便于灰度回退。

## 6. 红线对照

| 红线 | 本设计 |
|---|---|
| 零模型调用产出称谓 | 全程确定性：词典 + 表驱动消歧 + 前缀泛化 |
| personal/space 用户词条优先 | 消歧/泛化仅在 locale/system 命中或未命中时生效 |
| PII 最小化 | 出生年仅用于服务端比较，不进 payload/日志/称谓文本；脱敏节点视为未知 |
| 相同输入相同输出 | 消歧/泛化均为纯函数 |

## 7. 权衡记录

- **解析层修复 vs 管家写 space 词条**：后者引入写入路径、开关治理与审计负担，
  且收益为零（解析层同样自动生效、覆盖所有空间）。选择解析层；管家角色 = 既有
  重算节奏的自然传播。「管家直接处理」的用户诉求以结果衡量：树自动变对，零人工。
- **4 跳收窄**：穷举 4 跳旁系组合爆炸且称谓地域差异大；直系 4 跳收录 + 长链泛化
  兜底在正确性与维护成本间取平衡。
