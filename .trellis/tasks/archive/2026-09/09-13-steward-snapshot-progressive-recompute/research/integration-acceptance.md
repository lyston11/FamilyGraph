# 归档前串行集成验证

状态：主线接合与最终本地 AC1–AC9 验收通过，已合入本地 main、归档并清理任务 worktree/分支。最终代码为 `dee91a14`，全部 612 个运行时文件与 `c021921` 冻结版本一致。原 `final-acceptance.md` 以及下列中间检查点保留历史结果；最终验收和收尾见本文末节。

## 授权与基点

用户明确要求“归档”，据当前 AGENTS.md 执行本地提交、串行集成、验证、task.py archive、worktree 与分支清理。未请求推送或部署。

本任务工作提交已保存：

- `f7fbd38726c835c06ac50a1b8c8bcd2dd14ab469`：快照计算、有界 staging 与原子发布。
- `6162cedecd329b6b111eea10b0b34cfed3d756e9`：稳定授权骨架与渐进称谓。
- `dddc6b77af89188a3f883fd266fa8ccfee4df1f3`：并发写与真实浏览器验收量具。

首轮主线代码接合基点为 `0db88c2d7adc14d509bda93a690f04813f9026a1`。集成期间主线另有 provider task 归档元数据、journal 和 `73b0bbd` 管理员有效开关展示修复；最终合并前再次检查差异和其他任务的未提交文件。主检出其他工作保留，业务修改仅在本任务 worktree。

## 必须保留的相邻行为

- Memory/RAG 的独立事务、仅 RAG 启动维护、永久引用失效和无损降级拒绝。
- 主线自动称谓与个人/空间偏好优先级、模型开关、稳定抑制、亚型和第三方方向授权。
- assist 的 owner/attempt/deadline、逐 HTTP 栅栏、unknown 恢复和跨作业预算。
- 本任务短写事务、真实 preview/published 区分、指针/消费水位原子性和交付独立恢复。

## 已完成的集成检查点

- 新增 `0048_steward_terminology_publication`，连接 `0047_rag_lifecycle_integrity` 和 `0045_steward_staged_publication`，不修改既有 revision ID。
- 称谓使用、抑制、有效投影和平台/空间开关进入 presentation 失效；投影调度审计字段及无覆盖词的基线行不触发重算。
- 两种先后升级次序发现：先 0045 后主线术语迁移的 SQLite batch 重建会丢失三条 inferred setting 触发器。0048 幂等恢复，完整链共 60 条输入触发器。
- 合并迁移在降到更老父版本前先检查 RAG/context/Steward 的拒绝条件，避免先拆本层触发器、后因父迁移保护而失败。
- `test_steward_terminology_publication_migration.py` + `test_rag_lifecycle_migrations.py`：15 passed / 26.37s；两父分支升降重升、实际 migration 连接 FK ON/OFF、原用户/账号/事实与 pending generation/intent 保留、RAG 拒绝前零 DDL 均通过。
- `test_steward_terminology_input_versions.py`：18 项已通过，覆盖原始 INSERT/UPDATE/DELETE、没有 DomainEvent 的失效、audit 与 baseline-only 不自失效、删除覆盖词失效。
- 前端：72 files / 740 tests，17.78s；lint、type-check、production build exit 0。新增称谓输入失效保留同一身份布局、拒绝旧请求；面板随 generation/legacy view_version 清旧称谓及证据，同代 progress revision 不重复刷新。
- 称谓交付与 assist 接合：10 项新增集成测试及 7 项纯快照/实际批次回归通过。每批最多 8 个目标，模型分组在事务外准备；独立 writer 可在分组期间提交。相同输入热代继承未完成批次，不重新建立已完成批次；缓存只在提交成功后推进版本，真实回滚 ABA 用例通过。
- 搜索配置与展示配置拆开：称谓规则、词包及环境开关只重绘展示，保留已发布结构和搜索失败预算；算法/策略仍失效结构。三种称谓配置变化及展示变更后的持久预算回归通过，独立审查确认完整输入/租约栅栏保留。
- 首轮联合后端：1499 passed / 7 failed / 3 skipped / 4 warnings（136.31s）。失败已定位并修复：4 项 RAG 测试须等待异步 Steward 结束；1 项降级测试改为验证预检拒绝后 head 和完整 schema 不变；2 项确认关系呈现暴露了同步整图搜索回退，已恢复仅消费正式 publication，并以禁止搜索的探针防止回归。未放宽两个 publication 断言；候选事实方向、第三方及亚型保持。修复关联 77 项 + 51 项通过。第二轮完整门禁为 **1506 passed / 3 既有 skipped / 4 既有 warnings，129.17s，exit 0**；日志与 JUnit 保存为 integration-backend-final。
- 后端 Ruff lint/format 385 文件、mypy 203 文件通过；两个性能脚本与 API smoke 脚本 Ruff/format、diff 检查通过。

## 首轮接合检查点（历史）

- 并发写和真实浏览器复验正在运行；API smoke **56/56 passed**，三个 listener 均为随机 loopback 端口。
- 两次集成提交为 ce085fb 和 3cb4550，已跟进 origin/main 9d41cf0；管理员前端 15 files / 111 tests、lint/type-check/build 全通过。
- 冻结代码为 3cb45508af7818da046b6347dd039ecf9d2cea1f，611 文件摘要 11ae91b112be61d9add109daa524a7e6eeec9270518e64841ab2db806c96e5e2。最终实测通过后串行合入 main、archive 并立即清理 worktree/分支。

## 首次合并后 200 人测量与整改

固定 3cb4550 / 611 文件摘要的第一次 200 人实测保留为 `benchmark-integration-200-first.json`。冷 393.7451s / 热 4.2743s；200 个账号，33976 个授权目标。热算零路径搜索与视图重建。接口探针首骨架 443.907ms，本人称谓完成 58.745s（均从重算启动计，不是浏览器 p95）。

全部请求零失败，冷显式写锁 p99 10.475ms / max 290.663ms，热显式 p99 35.162ms / max 103.235ms；但热算一次登录隐式写窗口 **561.596ms**，验收判定失败。该事务首条 INSERT 561.371ms、线程 CPU 1.439ms、commit 0.046ms，不能据此断言存在 562ms 的实际持锁或 fsync，也不能删除这条含取锁等待的超限证据。

独立定位发现：2299 次 cold PFV 请求中 2112 次在本人已 ready 后仍因整个空间尚未 published 而重新登记 demand；6 个 >100ms 显式窗口来自此写入。读取全部 StewardViewTarget 还会解码从未使用的 resolution_json；热复用在 writer 中解/编码骨架。全局交付领取的同形查询计划使用 due 索引后还要排序 pending；新增 `(status,id)` 可避免这个扫描/排序，但带 generation_id 的热领取本来就有索引，不能把其慢 SELECT 唯一归因于队列扫描。

整改保持 busy_timeout、扫描间隔、目标和测量口径不变：读取窄列、有效重复 demand 的只读合并、热骨架在 SQLite 内单行复制、全局领取索引。上述修改后需要新源码冻结及完整后端/迁移、性能复验；此前的 1506 通过仅代表整改前代码。


## 整改后冻结与联合门禁

- 修正提交：`84660c88b32e24bedef11581efbda674daa21d5a`，任务 worktree 干净。
- 611 个运行时/构建/量具文件摘要：`9327eba9ee7451bdbf1e66be83205cd9cb36ad89df1b2e2a709f914bbdb4b2e3`；文件表为 `runtime-integration-remediated-manifest.json`。相对首次集成实测只有 model、demand、pipeline、views 和 0048 五个运行时文件变化，两个前端构建与测量脚本保持一致。
- 完整后端：**1528 passed / 3 既有 skipped / 4 既有 warnings，144.14s，exit 0**，保留 `integration-backend-remediated.log/.xml`。Ruff lint、format 386 文件和 mypy 203 文件全部通过；独立审查见 integration-review.md。
- 200 人冷/热并发实测已启动；该节不预先声明性能门禁通过。随后按同一冻结代码检查 30/50 人、两次真实扫描、Chrome 与 API smoke。


## 第二次集成测量与共享写入预算

`84660c8` 的完整实测保存为 `benchmark-integration-200-second.json/.log`。冷 337.6317s / 热 4.1052s，热算继续零路径搜索、零视图重建，请求零失败。冷显式持锁 p99 9.165ms / max 252.374ms，热 p99 21.337ms / max 168.496ms；但两次登录隐式写窗口 678.340 / 882.660ms，且热 BEGIN 等待 max 2221.465ms，严格门禁再次失败。原窄列/缓存修正减少重复工作，但不能解决持续短事务之间的取锁公平性。

对应 SQLite 3.51.1 的 `sqliteDefaultBusyCallback` 源码后段使用 100ms 重试间隔，原文来自 https://raw.githubusercontent.com/sqlite/sqlite/version-3.51.1/src/main.c ，节选保存在 research。26 轮隔离真实 WAL 对照、提交 ledger 与时间序列证明此机制可复现：纯 SQL、锁内没有 sleep 时，一次在线 INSERT 等待 885.201ms（线程 CPU 0.3985ms、commit 0.0051ms），期间 84,766 笔后来开始的后台事务先成功，后台单笔持锁 max 5.314ms；另一纯 SQL 轮次 max 366.684ms，负面结果保留。

独立两后台仅串行化仍有 1064–1079ms 等待；共享累计持锁 50ms 后空档 100ms，对照在线 max 63.99–64.19ms、后台吞吐 263–268 次/秒。100ms 写预算对应 120.58–124.29ms 和 378–380 次/秒。受控测试中的部分用例以约 1ms 锁内工作模拟应用步骤，不能冒充实际家族；纯 SQL 对照单列。脚本、两个摘要和共同空档完整数据入档，首组约 169MB 原始逐事务 trace 留在 /private/tmp，摘要保留各轮结果、真实顺序校验和测量边界。

实际实现采用保守的 50ms writer 步骤预算与 100ms统一空档；步骤还计入取锁等待、提交/回滚和 Session 清理。预算在 Session 创建前等待，跨协调器 FIFO，共同空档不能被另一空间填补；已有自然空档不重复等待。原输入/授权/租约校验保留，心跳改为入事务后采样时间。范围为同进程/Engine；当前 app.serve 三个 listener 共用这一 Engine，其他进程或未受预算管理的写入仍需部署环境验收。它不抢占单笔事务、不修改 busy_timeout，也不保证所有调度环境的绝对最大值；真实负载仍按原 AC1 检验。


## 共享预算后的冻结验证

提交 `c0219217446609a7e9d71675ee29cce8954732fb`；612 个运行时/构建/量具文件摘要 `fcfce00197a670412114968376836b50662b567d86aaeeb2a60e0782c373c5fd`，文件表为 `runtime-integration-admission-manifest.json`。共享预算/时间栅栏聚焦 20 项通过，之后完整后端 **1535 passed / 3 既有 skipped / 4 既有 warnings，133.67s，exit 0**，日志/JUnit 为 `integration-backend-admission.log/.xml`；Ruff lint/format 388 文件、mypy 204 文件全部通过。任务 worktree 干净，200 人实测已开始，尚未提前判定性能通过。


### 200 人冻结代码结果：通过

`benchmark-integration-200-final.json/.log`：200 人/200 账号，33976 个授权目标。冷 416.1283s、热 7.3182s，热算路径搜索/视图重建均为0。显式持锁冷 78802 个样本，p95/p99/max 为 2.474/6.955/125.586ms；热 773 个样本为 12.198/15.822/115.513ms。隐式写入窗口包含取锁等待：冷1142个样本 p95/p99/max=18.492/134.998/337.852ms，热21个样本=23.137/49.249/49.249ms。全部请求零失败，原量具各检查通过。

接口探针首骨架281.999ms、首个称谓855.328ms、本人ready53977.130ms；不是浏览器首屏p95。真实写锁p99低于100ms；冷登录的隐式窗口p99为134.998ms，包含竞争等待，单列保留，不冒充实际持锁。登录端到端冷p99/max451.766/649.736ms、热661.172/661.172ms，包含bcrypt与请求调度，也不冒充写锁时间。

此结果保留共享空档的吞吐取舍，不承诺大图完整计算瞬间完成。首骨架和本人进度独立于全空间发布。30/50人及两次真实扫描已启动；此前两份失败实测仍作为独立证据保留。

主线新提交 `b5ea10a` / `be54c8d` 仅调整Trellis文档和上下文治理，已串行接入任务分支为 `dee91a14e85ca69d4c39044c1da359ea46700612`。全部612个运行时字节摘要与c021921冻结版本相同，校验原件 `integration-documentation-merge.json`；这些文档更新未触发重复运行已经通过的代码门禁。本任务早于治理adoption marker，原context清单按该合同历史边界保留，归档时只修复移动后的自引用路径。

## 最终验收汇总（冻结运行时代码）

以下为最后一轮完整结果；两次集成性能失败报告继续保留。全部测量对应 `c021921` 的 612 文件冻结摘要，`dee91a14` 仅接入主线 Trellis 文档，逐文件一致。后端 **1535 passed / 3 既有 skipped / 4 既有 warnings**、Ruff lint/format（388 文件）、mypy（204 文件）通过；家庭前端 72 files / 740 tests、管理员前端 15 files / 111 tests 及各自 lint/type-check/build 通过。三项 skip 和四项 sqlite datetime adapter warning 为既有项目行为。

环境：Apple M4 / 16GiB、macOS 26.2 arm64、Python 3.12.12、SQLite 3.51.1，WAL、busy_timeout=5000ms、synchronous=NORMAL、wal_autocheckpoint=1000、bcrypt rounds=12。全部样本使用隔离数据库，人数与树内账号数一致；浏览器、性能与 smoke 串行执行。

### 冷热重算与写入

| 人数 | 冷算 s | 热算 s | 热算路径搜索/视图重建 | 接口首骨架 ms | 首称谓 ms |
| --- | ---: | ---: | --- | ---: | ---: |
| 30 | 17.7736 | 1.6526 | 0 / 0 | 177.126 | 549.468 |
| 50 | 27.3105 | 2.4009 | 0 / 0 | 196.573 | 592.000 |
| 200 | 416.1283 | 7.3182 | 0 / 0 | 281.999 | 855.328 |

上述首骨架/首称谓来自接口探针，不是浏览器 p95。本人 ready 为 30 人 2.485s、50 人 5.701s、200 人 53.977s，均早于全空间完成。

| 人数 / 阶段 | 显式持锁样本数 | 显式持锁 p95 / p99 / max ms | 含取锁等待的隐式窗口 p95 / p99 / max ms |
| --- | ---: | --- | --- |
| 30 / cold | 2386 | 2.030 / 7.970 / 25.207 | 11.777 / 27.191 / 27.191 |
| 30 / hot | 205 | 8.870 / 14.354 / 19.402 | 65.014 / 65.014 / 65.014 |
| 50 / cold | 5871 | 2.176 / 4.998 / 60.318 | 20.356 / 28.818 / 28.818 |
| 50 / hot | 261 | 13.168 / 25.585 / 40.660 | 8.223 / 8.223 / 8.223 |
| 200 / cold | 78802 | 2.474 / 6.955 / 125.586 | 18.492 / 134.998 / 337.852 |
| 200 / hot | 773 | 12.198 / 15.822 / 115.513 | 23.137 / 49.249 / 49.249 |

全部请求零失败，原脚本检查全部通过。200 人冷算隐式窗口 p99 为 134.998ms，含取锁等待，不冒充实际持锁；所有最终显式持锁 p99 ≤100ms，显式持锁和隐式上界均未出现 >500ms。热算样本较少，p99 可能等于 max；不作超出样本的长期保证。

| 人数 / 阶段 | 登录客户端 p95 / p99 / max ms | lease 客户端 p95 / p99 / max ms | maintenance reaper p95 / p99 / max ms |
| --- | --- | --- | --- |
| 30 / cold | 217.309 / 240.321 / 240.321 | 20.845 / 50.151 / 55.655 | 4.789 / 49.839 / 49.839 |
| 30 / hot | 281.980 / 281.980 / 281.980 | 37.579 / 37.579 / 37.579 | 47.566 / 47.566 / 47.566 |
| 30 / scans | 206.474 / 267.876 / 656.580 | 10.672 / 17.533 / 191.611 | 2.089 / 6.940 / 144.054 |
| 50 / cold | 252.495 / 456.952 / 456.952 | 44.905 / 106.978 / 142.240 | 13.031 / 38.636 / 75.084 |
| 50 / hot | 215.116 / 215.116 / 215.116 | 71.308 / 71.308 / 71.308 | 21.149 / 21.149 / 21.149 |
| 200 / cold | 348.613 / 451.766 / 649.736 | 21.002 / 147.887 / 417.398 | 25.831 / 74.420 / 239.198 |
| 200 / hot | 230.921 / 661.172 / 661.172 | 10.247 / 473.510 / 473.510 | 70.734 / 94.317 / 94.317 |

登录端到端包括 bcrypt、排队和调度，reaper/tick 包含维护工作，均不是写锁测量。原始请求样本数、PFV 服务端/客户端和取锁等待分布见 [30/50 人与扫描报告](benchmark-integration-scans-final.json) 及 [200 人报告](benchmark-integration-200-final.json)。

### 真实扫描窗口

30 人样本持续观测 **610.006s**，执行 **121** 次真实 maintenance tick。扫描触发时间 **0.315 / 300.928 / 602.958s**，间隔 **300.613 / 302.030s**；作业 3、4、5 均 succeeded/published，全部并发请求零失败。扫描显式持锁 15701 个样本，p95/p99/max=**2.917/6.872/241.481ms**；隐式窗口 p95/p99/max=**0.867/7.698/65.504ms**。完整 maintenance tick max=1144.730ms，包含调度和有界维护工作，不是写锁持续时间。50/200 人报告未各自重复 610 秒观测；两次真实 300 秒扫描验收由 30 人样本完成。

### 真实浏览器与 API

Chrome/152.0.7977.83、production build、30 人/30 账号/45 条事实。桌面 1365×900 共 5 次，移动端 375×812 两主题各 1 次：

| 场景 | 请求至骨架可交互 ms | 首响应服务端 ms | 结果 |
| --- | --- | --- | --- |
| desktop | 350.0–389.3，p95 389.3 | 34.984–40.577 | 全部通过 |
| mobile-modern | 359.9（单次） | 40.799 | 全部通过 |
| mobile-paper | 365.1（单次） | 37.671 | 全部通过 |

所有试次在 **29 个称谓仍 pending** 时已显示 30 人确认骨架；实际平移、缩放、节点拖动、关系选择以及称谓更新后的状态保持均通过。三张截图已人工查看，页面无横向溢出；大树可平移缩放，不宣称全部人物在移动端同屏可读。桌面与移动结果和截图分别为 `browser-integration-*-final.json/.png/.log`。

最终 API smoke 在同一运行时代码上 **56/56 passed、0 failed、exit 0**，三个 listener 均使用隔离 loopback 端口，见 [完整报告](integration-api-smoke-final.json)。既有 SSH 隧道与生产服务未改动。

### 适用范围与后续部署

- 本地 AC1–AC9 验收及主线称谓/Memory/RAG 接合完成，0048 为唯一 Alembic head；隔离迁移和独立代码复核见前文及 [审查记录](integration-review.md)。
- 共享 admission 仅覆盖同进程/Engine 的 `write_transaction`；它为在线写入留空档，以后台吞吐换取写入机会，不跨进程协调、不抢占单笔事务、不保证任意系统调度下的绝对最大延迟。
- 200 人全空间冷算仍约 416 秒；骨架和本人称谓进度与其分离。容量数值属于本机样本，稠密图只验证资源/语义上界，没有同等容量跑分。未采连续 CPU/RSS、完整 SQL 或独立交付排队分布；故障恢复为受控进程丢失注入，不是操作系统真实重启。
- 目标服务器备份、发布前健康检查、迁移和部署环境两次扫描观测属于独立后续部署范围。本次未 push、部署或修改生产配置。

### 本地合并归档收尾

本地 main 已由 `be54c8df` fast-forward 至 `dee91a14`，511 个受 Git 管理的运行时文件与冻结清单逐字节一致，Alembic heads 确认为唯一 `0048_steward_terminology_publication`。101 个忽略的构建产物在任务 worktree 验收，不作为源码提交或替换主检出现有构建目录。

`task.py archive --no-commit` 已将任务移至本目录、标记 completed 并清除指向本任务的会话记录；旧 JSONL 只修复归档后的自引用，不迁移 adoption marker 前的上下文选择。随后 `git worktree remove` 和 `git branch -d` 均成功，任务 worktree/分支已不存在，共享 backend/.venv 和 frontend/node_modules 完整保留。归档文件在主检出按本任务新旧路径单独提交，journal 记录工作提交及实际清理结果。

主检出另外 13 个未提交文件的 SHA256 与收尾前一致，未纳入本次归档。最终证据绑定见 [integration-final-evidence.json](integration-final-evidence.json)。未执行推送或部署。

归档文本空白检查通过；首轮失败的原始 pytest 日志 `integration-backend-first.log` 自带行尾空格，按原样保留，仅从归档空白检查中排除，不修改历史输出。
