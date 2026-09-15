# 记忆候选提取器：技术设计

## 模块与数据流

```
agent_queue._settle (status=succeeded, 事务内)
  └─ memory_extractor.extract_after_settle(db, run)     # 新服务函数，容错包装
       ├─ 读 run.message_id → AgentMessage(role=user, 本人 session)
       ├─ MemoryCandidateExtractor(detector=rule_detector).extract(
       │     db, author_account_id=session.account_id,
       │     conversation_text=message.content_json["text"],
       │     source_message_id=message.id)
       │   每条 → propose_candidate(..., idempotency_key=f"extract:{message_id}:{category}:{i}",
       │                             extractor_version="memory-extractor-v1")
       └─ 异常一律 logger.warning + 返回（不冒泡）
```

## 新文件：backend/app/services/memory_extractor.py

1. `EXTRACTOR_VERSION = "memory-extractor-v1"`
2. `MAX_CANDIDATES_PER_MESSAGE = 3`
3. `rule_detector(text) -> list[MemoryCandidateInput]`（纯函数，注入 `MemoryCandidateExtractor`）：
   - 类别规则（有序，命中即占一个候选名额）：
     - `birthday`：正则 `(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]` 且句内含 `生日|出生|诞辰`；农历前缀 `(农历|阴历)` 升级 summary 标注。summary：`生日：<MM月DD日（农历）>`；purpose：`家庭成员生日提醒与亲属问答`；sensitivity=normal。
     - `dietary`：句内含 `过敏|不能吃|忌口|不吃|对.*过敏`；summary：`饮食禁忌：<句内相关子句截断>`；sensitivity=sensitive。
     - `occupation`：`职业|工作|上班|就读|上学|在读` 邻近具体名词——首发保守：仅匹配 `是|在做|在.*当` + `老师|医生|工程师|学生|护士|律师|公务员|程序员|厨师|司机` 等封闭职业词表；sensitivity=normal。
     - `residence`：`住在|家在|搬到了` + 城市（封闭城市词表：北京/上海/广州/深圳/成都/杭州/重庆/武汉/西安/南京/天津/苏州/长沙/郑州/青岛/厦门/福州/合肥/昆明/贵阳/南昌/济南/太原/沈阳/长春/哈尔滨/石家庄/兰州/西宁/南宁/海口/呼和浩特/乌鲁木齐/拉萨/银川 + 「X市/X县」通用后缀模式）；sensitivity=normal。
     - `preference`：`(喜欢|最爱|讨厌|怕)` + 名词短语（≤20 字符，同行捕获）；sensitivity=normal。
   - 输出按类别顺序截断至 3；`source_quote` 一律=完整原文（调用方保证传入的就是全文）。
   - 所有正则预编译；输入截断到 8,000 字符（与 AGENT_MESSAGE_MAX_LENGTH 对齐）。
4. `extract_after_settle(db, run) -> int`（返回候选数；容错）：
   - 前置检查（任一不满足直接 return 0，不抛错）：`run.message_id` 非空、run 有效终态、`platform_features.is_memory_enabled(db)`、消息 role=user、text 是 str。
   - `MemoryCandidateExtractor(detector=rule_detector)`，`extractor_version=EXTRACTOR_VERSION`。
   - 幂等键：`f"extract:{message.id}:{input_index}"`（input_index 为本消息候选序号，0 起；类别信息已由序号+内容 fingerprint 覆盖）。
   - 捕获所有异常 → `logger.warning("memory extraction failed run=%s ...", run.id, exc_info=True)` → return。

## 修改点：backend/app/services/agent_queue.py `_settle`

在 `db.flush()` 前（run 终态已定、同事务）：

```python
if effective == "succeeded" and run.message_id is not None:
    try:
        from app.services import memory_extractor
        memory_extractor.extract_after_settle(db, run)
    except Exception:  # 双保险：extract_after_settle 内部已容错
        logger.warning(...)
```

事务语义：候选与终态同事务提交。若 propose 抛错被吞，候选不落但 settle 照常提交（部分提取可接受——幂等键下次 reaper/重试不会自动重驱已终态 run；接受该损失，下一消息仍会提取）。

## 不改的东西

- `propose_candidate` / `memory_sources` / memory API / 前端：零改动。候选自动出现在既有 `GET /api/memory-candidates` 列表，用户经既有 confirm 流程入库+索引。
- `MemoryCandidateExtractor.__init__` 签名与 seam 保留；`_default_detector` 改为 `memory_extractor.rule_detector`（import 放模块顶部会循环依赖：memory_rag ← memory_extractor？不会——memory_extractor 导入 memory_rag 的 `MemoryCandidateInput`/`propose_candidate`。因此 `memory_rag.py` 内默认 detector 通过**函数内延迟 import** 引用，避免循环。或者：把 `MemoryCandidateInput` 与 detector 接口留在 memory_rag，`rule_detector` 定义在 memory_extractor 并延迟 import propose_candidate。选择后者：memory_rag._default_detector 内 `from app.services.memory_extractor import rule_detector`（每次调用一次 import，开销可忽略，且避免模块级循环）。

## 兼容性

- 旧版本创建的候选（extractor_version="manual-v1"/"candidate-extractor-v1"）不受影响；新候选版本字符串区分来源。
- MEMORY_ENABLED 关：`extract_after_settle` 前置检查直接短路，settle 不受影响。
- 平台操作员账号：`resolve_source` 对 platform operator 403——`propose_candidate` 抛错被容错层吞掉，settle 照常。

## 测试设计

- `tests/test_memory_extractor.py`：纯函数用例（每类别正/反例、上限、确定性——同输入两次调用结果相等、8k 截断）。
- `tests/test_agent_queue_*`（现有 settle 测试文件内追加）：成功 settle 产候选；MEMORY_ENABLED=False 时 settle 成功无候选；重复 settle（模拟幂等）不重复产卡；过敏文本 → sensitive 候选。

## 部署

无迁移。`scripts/server-sync-code.sh` 同步 → 远端 `systemctl --user restart familygraph-api`（sidecar 无改动，无需重启）。验证：远端新会话含生日句 → 查 `memory_candidates`。
