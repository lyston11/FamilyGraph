# Steward 根治：主线接合前本地验收（历史）

历史检查点：2026-09-14，主线接合前实现及 AC1–AC9 本地验收完成。下文保留该版本的结果与当时边界；后续提交、主线接合、性能整改及本地归档以 [集成验收](integration-acceptance.md) 为准，本文不代表最终集成代码。

## 实现与证据范围

- 一致读快照在显式 SQLite `BEGIN` 中取得；纯图搜索在有界 spawn 执行器中推进，不占写事务。单目标结果在短事务里重验输入、权限、owner/attempt/deadline，再保存完整结果与进度。
- 家谱骨架来自同一查看者的已授权确认图；每个人的称谓、路径和解释完整后才补齐。普通消费者只读已发布结果，渐进协议可读经过重新验证的同代预览。
- 全部必需目标和交付计划就绪后，原子切发布指针，结算作业、固定事件水位、已捕获 demand 和交付激活。失败/旧执行者/重复回执不能提前结算或覆盖新代。
- 无变化图不搜索；只有展示输入变化时复用结构结果。持久重试预算不因扫描、换代或重启清零；通知、动作卡与模型辅助在发布后独立恢复。
- 前端以账号、空间、generation/revision 隔离数据；有效期、撤权与乱序处理优先。相同骨架补标签时保留坐标、视口、拖动和选中面板。

性能与 Chrome 验收冻结 523 个运行时/构建/脚本文件，SHA256 为 `6180be62d335b8d46f8da9dd5ec247a70a0693a18257dad564f1d3c43fc5d1b2`，见 [验收源码清单](runtime-final-v3-manifest.json)。最后修正 smoke 的 internal 隔离端口后，[最终源码清单](runtime-final-manifest.json) 为 `98be2abbe218db544e5923485abf39176ff1e05c8ecdd5b0b776573c34789989`；[逐文件复核](runtime-final-source-delta.json) 确认只有 `scripts/smoke/run_api_smoke.py` 变化，后端、前端及两个性能脚本均未变。前端 276 个源文件/配置/构建产物也与已通过的前端门禁时点一致。

## 质量与一致性

| 检查 | 当前证据 |
| --- | --- |
| 后端 Ruff lint / format | 通过；351 文件 |
| mypy | 通过；195 源文件 |
| 后端 pytest | 1153 passed、3 skipped、4 warnings，66.90s，exit 0 |
| 家庭前端 lint / type-check / test / build | 通过；69 文件 / 656 tests，12.74s，源文件和构建产物未变 |
| 性能脚本 Ruff lint / format | 两脚本通过 |
| API smoke | 30 passed / 0 failed，exit 0；smoke 脚本 Ruff 与 diff 检查通过 |
| 真实 Chrome | 桌面 5 次和移动端两主题各 1 次全部通过 |
| 0045 隔离迁移 | 升级→受限降级→重升级通过，见 [迁移报告](migration-0045-final.json) |
| Trellis context validate | implement/check 各 4 条引用，通过 |
| 独立代码复审 | 全任务、最终 save_target/量具/smoke 窄改动及原始证据核对均无阻塞，见 final-review.md |

三项 skip 是既有延期的 break-glass 家庭数据能力，四项 warning 是既有 SQLAlchemy/Python 3.12 datetime adapter 弃用提示。前端门禁开始于 2026-09-14 07:44:51 UTC；既有 notifications XHR stderr 未屏蔽，最终 gate 退出码为 0。此处采用执行上下文保存的结果，不伪造未留存的完整终端日志。

恢复证据使用真实单 spawn CPU 调度，以及受控 `BaseException` 模拟进程丢失后经独立 Session、reaper 和新 owner 接管；覆盖已保存目标复用、半目标重做、预算保留、旧回执拒绝和单次发布。该证据不等于真实操作系统重启实验。

迁移文件 SHA256：`d4be1434ac4a18cd2c95e2bb76c42406af492b9c0ada0dffb5ba1d79638e69c0`。回退拒绝 running/failed preview、未满足 demand、pending/failed delivery；保留调用方 FK ON/OFF、3 用户和 2 条真源事实，验证 45 个输入触发器。0041/0042 仅格式整理，AST 与 HEAD 一致，未改变迁移业务逻辑。

## 容量与计时口径

环境为 Apple M4 / 16GiB、macOS 26.2 arm64、Python 3.12.12、SQLite 3.51.1、10 CPU，bcrypt rounds=12；WAL、`busy_timeout=5000`、`synchronous=NORMAL`、`wal_autocheckpoint=1000`。每个样本从新迁移的隔离数据库开始，全部人物有树内账号，并发访问真实登录、内部 Agent lease、PFV 路由和独立维护 Session。

量具使用 `dbapi-return-before-reporting-v3`。显式持锁从 `BEGIN IMMEDIATE` 返回到实际 commit/rollback 返回；隐式上界包含取锁等待，另列排除首次 DML 的持锁下界。DB-API 返回后先固定终点，再统计。7 项确定性回归验证 COMMIT 内 250ms 延迟计入、提交后 600ms 统计延迟排除、失败 COMMIT 继续计时、自动 rollback 立即结束和诊断脱敏。请求总耗时、取锁等待和持锁不可混称。

| 样本 | 冷 / 热重算 | 骨架 / 首批称谓观测 | 冷显式写锁 p99 / max | 冷隐式上界 p99 / max | 请求错误 |
| --- | --- | --- | --- | --- | --- |
| 朱氏 30 人 / 30 账号 / 45 事实 | 14.8350s / 0.2157s | 180.548ms / 538.341ms | 4.432ms / 33.393ms | 20.722ms / 20.722ms | 0 |
| 50 人 / 50 账号 / 49 事实 | 13.4756s / 0.2284s | 175.452ms / 675.163ms | 3.720ms / 58.428ms | 41.810ms / 41.810ms | 0 |
| 200 人 / 200 账号 / 199 事实 | 327.4285s / 0.9224s | 454.797ms / 954.495ms | 8.455ms / 123.143ms | 97.420ms / 237.128ms | 0 |

30/50 人数据见 [容量和扫描报告](benchmark-final-v3-ming30-50-scans.json)。以上骨架/首批称谓是从并发负载开始到 API 观察到结果的时刻，不是浏览器渲染耗时；各阶段的在线请求、取锁、commit 和持锁 p95/p99/max 及样本数均保留在 JSON。三个样本所有冷/热阶段的并发错误为零，无显式持锁或隐式上界超过 500ms；三个样本热算均零结构搜索/零视图重建。30/50 人当前查看者分别在 2.363s / 4.201s 齐全。

200 人原始结果见 [最终 v3 容量报告](benchmark-final-v3-200.json)。冷算有 78,414 个显式写事务样本、837 个隐式写窗口、837 次登录、1,742 次 lease、1,559 次 reaper 和 1,981 次 PFV 读取。热算结构搜索和视图重建计数均为 0。当前查看者 59.132 秒全部齐全，早于全空间发布。

200 人热算的单次 maintenance 请求为 667.559ms，对应取锁等待最大值 666.331ms；热算显式持锁最长 10.903ms。该等待如实保留，未伪装成持锁指标，也未造成失败。热阶段样本较少，不能把其分位数作为长期分布。

此前 200 人首轮出现 524.496ms，见 [保留的失败报告](benchmark-final-first-attempt.json)。校准证明旧量具会把提交后的统计等待误计为持锁，但不能反推这个历史样本的成因。修正量具后 v2 仍观察到 save_target 488.387ms，见 [诊断报告](benchmark-measurement-v2-200.json)。随后移除锁内整骨架 JSON 解码和结果重复编码，再取得本次 v3 结果；未更改超时、阈值、fixture 或账号数量，也未把长尾直接归因 WAL/fsync。

## 真实扫描、Chrome 与 API

30 人样本持续运行 610.006 秒，共 122 次实际 5 秒 maintenance tick；扫描时刻为 0.019、301.010、602.024 秒，间隔为 300.991 / 301.014 秒，新增 job 3/4/5 全部 succeeded。扫描阶段登录 1,741 次、lease 3,743 次、独立 reaper 2,982 次、PFV 5,330 次，全部零错误。显式持锁 15,570 个样本，p95 2.656ms / p99 4.539ms / max 109.732ms；隐式上界 p95 0.569ms / p99 2.876ms / max 43.437ms；完整 maintenance tick p99 89.198ms / max 97.525ms。没有推进假时钟或缩短 300 秒扫描间隔。

Chrome 152.0.7977.83 headless 使用生产前端构建、真实 Uvicorn 和每次独立的浏览器 profile/迁移数据库；维护周期仍为 5 秒，扫描为 300 秒。骨架出现时 30 个节点已渲染、29 个称谓仍 pending。随后实际切换自由画布、平移、缩放、拖动节点和选择结构关系，下一批称谓更新后视口、拖动位置和面板选择全部保持。

| 视口 / 主题 | 次数 | 请求到可交互骨架 | 首次 PFV 服务端响应 | 交互和页面横向溢出 |
| --- | --- | --- | --- | --- |
| 1365×900 / paper | 5 | 349.4–378.9ms，p95 378.9ms | 27.296–32.710ms | 全通过 / 无溢出 |
| 375×812 / modern | 1 | 361.3ms | 37.722ms | 通过 / 无溢出 |
| 375×812 / paper | 1 | 346.7ms | 28.734ms | 通过 / 无溢出 |

浏览器报告：[桌面](browser-final-desktop.json)、[移动 modern](browser-final-mobile-modern.json)、[移动 paper](browser-final-mobile-paper.json)。主线程已查看 [桌面截图](browser-final-desktop.png)、[modern 截图](browser-final-mobile-modern.png)、[paper 截图](browser-final-mobile-paper.png)：确认骨架与真实进度同时出现，移动控件及底部导航位于页面内。手机数据是两主题各一次的视口验证，不冒称移动设备总体 p95。

[API smoke](api-smoke-final.json) 30/30 通过：家庭登录/刷新、家谱、家庭卡及 304、统计、通知、记忆、Agent 会话/SSE 终态、附件，以及管理员登录/刷新/治理和双向 token/listener 拒绝。第一次被沙箱拒绝绑定端口；获准后发现脚本未随机分配 internal 端口，固定 8001 被现有 SSH 隧道占用而返回 blocked。仅为 smoke 补上 `INTERNAL_AGENT_API_PORT`/`HOST` 后通过，没有改现有隧道、业务代码或数据库。此 smoke 的 worker 关闭配置用于接口回归；AC1 的证明来自前述实际运行 worker 的容量和扫描报告。

## AC1–AC9 对照

| AC | 本地判断 | 决定性证据 |
| --- | --- | --- |
| AC1 | 通过 | 三规模冷/热并发、原 300 秒扫描两周期；零错误，持锁 p99≤100ms、无窗口>500ms |
| AC2 | 通过 | `test_steward_snapshot_fences.py`、`test_steward_input_versions.py`、`test_steward_runtime_recovery.py`：真实双连接、输入/权限/时间、旧 attempt、发布回滚及恢复 |
| AC3 | 通过（本机） | 5 次真实 Chrome p95 378.9ms；骨架先于称谓、7 次真实交互保持 |
| AC4 | 通过 | staged pipeline、delivery recovery 和前端 progress/polling 测试：真实分母、幂等、终态、跨代预算与有限重试 |
| AC5 | 通过 | 三个热算均零搜索/零视图重建；仅词典变化复用、持久 demand、真实 spawn 跨空间让出回归 |
| AC6 | 通过 | 后端授权/ETag/有效期及前端请求排序、账号切换、安全空态、失权清理回归；Chrome 真实响应头 |
| AC7 | 通过 | publication 原子回滚、分批通知回执、交付跨代预算/租约/单项重试、assist unknown 不自动重发回归 |
| AC8 | 通过（任务分支） | 最终 0045 隔离升降重升与拒绝破坏性回退；main 的迁移分支尚待集成 |
| AC9 | 通过 | 后端 1153、家庭前端 656、相关 lint/type/build、30 个真实 API smoke 及原始性能报告 |

稠密图与断开图属于资源/语义回归，不混入上述稀疏树时延：`test_relationship_snapshot_compute.py` 覆盖密集图前 128 条路径、pickle 后续算等价、深度 12 边界及断开目标零展开证明；展开/状态预算耗尽抛 `SearchBudgetExceeded`，不产生 no_path。默认单 slice 2,048 次展开、单 target 2,000,000 次/4MiB、待算快照 64MiB。此次记录写事务样本与慢事务 SQL 计数，不包含全量 SQL 明细或连续 CPU/RSS 采样。

## 集成和发布边界

main 已有 `0044_steward_terminology`，本分支为 `0044_steward_generations` → `0045_steward_staged_publication`。后续串行集成必须处理迁移分支并重新验证，不能直接宣称可部署。未合并 worktree 保留，不强制删除。

本地测量不代替目标服务器的容量和真实网络验收。若需要临时止血，可停 `STEWARD_WORKER_ENABLED`，保持登录、Agent reaper 和 maintenance；这只能暂停预计算，不作为根治通过证据。正式回滚先停 worker、保留已发布结果/待办及真源事实，不自动恢复旧长写事务执行器。
