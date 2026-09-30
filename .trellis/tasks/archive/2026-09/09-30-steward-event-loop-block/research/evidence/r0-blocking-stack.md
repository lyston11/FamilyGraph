# R0 采样摘录与证据边界

## 2026-09-30 复核结论

**已有 SQL 执行等待线索，尚未证明完整的线程池耗尽因果链。R0/AC-0 未完成。**

此前报告“事件循环空闲、40 个 worker 被耗尽、pool_timeout=30s 解释全部长暂停”超出了保存证据。本文件保留栈摘录，撤回这些确定性归因。

## 历史记录的采样摘录

记录标注时间 2026-09-30 05:58:37，health 单次 65,830ms；采样使用 sudo py-spy dump --nonblocking。
当前工件未附原始完整采样输出及探针脚本，尚不能确认标注时间是请求开始、结束还是采样时间。

```text
Thread 3590824 (active): "MainThread"
    _read_from_self (asyncio/selector_events.py:132)
    _run (asyncio/events.py:88)
    _run_once (asyncio/base_events.py:1987)
    run_forever (asyncio/base_events.py:641)
    main (app/serve.py:210)

Thread 10106 (idle): "AnyIO worker thread"
    do_execute (sqlalchemy/engine/default.py:941)
    acquire_run_writer (app/services/agent_execution.py:121)
    fence_steward_execution (app/services/agent_execution.py:224)
    execute (app/services/agent_tools.py:493)
    execute_tool (app/api/internal_agent.py:843)

Thread 10107 (idle): "AnyIO worker thread"   [历史记录称同一栈]
Thread 10111 (idle): "AnyIO worker thread"
    load_on_pk_identity → get (session.py:3693)
    fence_steward_execution (app/services/agent_execution.py:225)

Thread 10113 (active): "AnyIO worker thread"
    set_sqlite_pragmas (app/db.py:27)
    _create_connection (pool/base.py:390)
    _authorize_steward_run (app/api/internal_agent.py:311)
```

历史汇总称 3 个线程位于 acquire_run_writer、5 个位于 do_execute。两组可能包含关系，不能相加作为占用量，更不能据此推导 40 个 AnyIO tokens 全部耗尽。

## 可以支持什么

- 采样瞬间若干工具 worker 正在数据库执行路径；写锁争用是合理待验证方向。
- health 曾被报告为显著慢请求，需要与同窗口资源采样建立时间关联。
- acquire_run_writer 执行 no-op UPDATE，承担授权与取消/reaper/lease 的串行化。工具执行随后还有准入 CAS、幂等占位和审计写入，不能归类成全程只读。

## 不能支持什么

- `_read_from_self` 是事件循环的唤醒处理；单次栈既不证明此前持续空闲，也不排除其他时刻同步阻塞。
- `do_execute` 栈本身不能区分等待写锁、实际查询或 I/O，缺少锁持有者与阶段耗时。
- 默认连接池参数不等于当时实际占用；pool_timeout=30s 是等待上限，不是请求延迟下界，不能仅凭 34s 空档倒推出连接池超时。
- `asyncio.to_thread` 的默认 executor 与 AnyIO worker 不应混为同一线程池。maintenance 仅 counters 非零时日志输出，224s 日志间隔不证明停止执行。
- run 189 在旧材料出现 24/26 两种工具数量；一轮总数也不等于同时在途峰值。不能据此精确计算线程饱和。

## 受控流探针的有限结论

历史报告 `_refresh_run_gate` 约 0.003ms/次；400 chunk 累计 lag 8.3ms，最大 0.19ms。小型探针未复现长暂停，因此不应直接把该调用当作已定位主因；但该测量不能排除生产负载下连接池等待、不同 gate 路径或其他同步操作。

## 下一步必要证据

在隔离文件 SQLite、真实 HTTP/权限路径下复现，记录 loop lag、AnyIO borrowed/total tokens、连接占用/等待、写事务等待/持有和心跳延迟。慢请求超阈值但尚未完成时采样，保存请求与采样完整时间边界。定位具体等待阶段与持有者之后，才能将 AC-0 标为完成。
