# D 最终验收

2026-09-14，主线程验收 D 在累计 A/C/B/E 与 main `461d691` 上的修复。D-I01～10 全部闭合，D-AC1～8 通过。本轮独立审查发现的合法 `index_superseded` 恢复回归也已修复；没有剩余实质阻断项。业务修复已提交 `aebee83`；main 合并及清理结果由父任务的最终集成记录跟踪，本文不将通过检查当作已经合并。

实现见 [实施记录](acceptance-implementation.md)，独立红绿证据见 [复核报告](acceptance-check.md)。源码合同见 [生命周期实现合同](../../../../../spec/backend/rag-index-lifecycle-contract.md)。原 bd899b9 的失败探针和历史结果保持原字节。

## 实际检查

| 检查 | 结果与范围 |
|---|---|
| 后端全量 | 1352 passed、3 skipped，71.46s；Ruff、format（357 文件）、mypy（193 文件）通过。运行前后核心源码 hash 相同，`app.__file__` 指向 D checkout |
| 独立 D 语义探针 | 15 passed / 1 deselected；排除的是首轮额外探索的 stage 恢复断言，旧 stage 原本无该合同；新的 stage 保持跳过正对照通过 |
| index_superseded 持久回归 | 24 passed；直接 ensure/真实 batch、可信/旧完整证据、缺块、撤销竞争、未知状态与最终拒绝零残留 |
| 独立迁移 oracle v2 | 8/8；FK OFF/ON × 合法非空、重复来源、镜像冲突、旧 0045 downgrade，使用 Git bc76e95 的真实保存依赖 seed；最终迁移源码 hash 匹配 |
| 真 listener/Pi/维护 smoke | 95/95，exit 0；恢复补修后重跑，只有模型 stream 为合成，外部推理请求 0 |
| 家庭/管理员 API smoke | 56/56，exit 0；在恢复补修前的同一累计候选执行，补修后的 95 项链与全量后端继续覆盖实际受影响路径 |
| 累计 frontend | lint/type-check/build 通过，660 tests / 69 files；本轮包含 main 的额外前端改动，因此未直接沿用 B 的 625 条计数 |
| Agent | agent/shared 对 B `bc76e95` 无代码差异，沿用 B 的 lint/type/tests（118）证据；D checkout 重新 build 和运行真实 Pi smoke |
| 冻结检索 | 核心中文 16/16、英文 2/2；扩展 7/10、MRR 0.65。补修后再测，未修改 fixture 或将扩展集用作调参目标 |

后端跳过的三项均为既有管理员 break-glass 家庭数据能力的延期测试。四条 datetime adapter 警告来自 Python 3.12/SQLite。前端全套出现一次 jsdom ResizeObserver 清理 stderr，进程 exit 0、无失败；随后受影响 memory/agent 8 文件 80 项及 main 基线 618 项均通过且未复现该输出。保留原始日志，不将此诊断说成已定位修复的产品缺陷，也未为此改动无关前端。

主线程报告存于复查任务 `research/final/`：backend 检查、XML/日志、迁移、真实 smoke、检索及独立探针；最终制品清单将源码 hash 与本次 D 业务提交关联。测试期间的 report `candidate_commit=c253cb6` 表示修复前 HEAD，实际受验代码由各报告的 source hash 固定。

## D-AC 回填

| AC | 证据与判定 |
|---|---|
| D-AC1 | 通过；真实 RAG-only 关闭保存→平台晚开启→循环补齐，默认/最大批次100，固定有限水位完成后重开下一轮 |
| D-AC2 | 通过；两个真实 Session 争用规范来源唯一键；index/ensure 幂等、已有块不可变，摘要与缺块组合冲突保留旧定位 |
| D-AC3 | 通过；source tombstone、deleted/revoked/expired、根依赖失效、legacy/unverified 不恢复；只有证据充分的已知 Memory index_superseded 恢复 |
| D-AC4 | 通过；取得 writer 前另一 Session 撤销，及最终开关/来源拒绝测试保持边界；读者会员资格变化仅影响该读者 |
| D-AC5 | 通过；不可变 lease 全字段 CAS、真实 tick 独立 RAG 事务、整批最终拒绝零残留、失败退避与持续新到达不饿死低 ID |
| D-AC6 | 通过；真实 v1/v2 换版和显式回滚、原活动指针检索、下一维护不降级；真实 B context→stage→repair→maintenance→重放→引用详情保持原片段 |
| D-AC7 | 通过；四开关组合与部署 hard-off 保持，RAG-only 不依赖 Steward/模型；每批100条和2秒软预算（非硬实时承诺） |
| D-AC8 | 通过；真实 FK OFF/ON 迁移与保存依赖保全、重复/镜像冲突首项 DDL 前拒绝；安全治理报告不输出正文/source_id 标签 |

## 边界

已验证隔离合成数据、实际 HTTP/SDK/SQL 及当前实现合同。未操作生产库、线上开关或真实 Provider，未测生产规模延迟。旧来源缺少完整可信输入时保留并报告，未执行不可逆去重/删除。E 的可选能力及 MR-23/26 后续 P2 仍由既有规划负责。
