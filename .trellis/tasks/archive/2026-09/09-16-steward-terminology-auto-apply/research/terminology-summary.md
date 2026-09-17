# 称谓自主优化：实施上下文摘要

## 结论与选择

用户要求按当前用户视角自动优化，无逐条批准。复用 Terms→Steward 发布→有效显示选择器，确定性词直接作为 baseline；只有额外改善写自动 projection，不要求为每个人造非空 override。

本轮源码及纯函数探针确认 `Um-Dm-Dm` 只显示“兄弟的儿子”，已有 `Bm-Dm` 可显示“侄子”。Terms 查词增加安全的生物 U-D 等价别名，支持中段，保留原始 concept/path，不改图、不把 B-D、D-U 或继养监护折叠。同步按需 snapshot 查询、模型 allowed_terms、哈希和缓存版本。

内置 `Sm-Bm/Sm-Bf/Sf-Bm/Sf-Bf` 四词方向反了；Sm=丈夫、Sf=妻子，必须同时修种子和存量 locale 行。先前对配偶编码的口头举例及约 26% 的估计不能用作测试期望。

`_suppression_concept` 只折叠起始 U-D，不是可直接复用的全路径归一器。保留旧 suppression 键兼容，不因规则换版取消用户拒绝。

derived baseline 可选保留是 09-13 design §2 明确行为；本次按用户意图收紧：无改善无建议，旧 baseline-only 建议通过共享有效态退出活跃消费/提交，保留终态与用户反馈。称谓从通知待核实/待办移出，档案保留可选固定/恢复，真实关系确认不变。

## 启用与验收边界

用户授权本次修复与服务器部署启用 terminology。沿既有环境/平台/空间/Provider/云许可/预算准入，不开其他 assist、不换 Provider、不绕过许可。代码正确、环境有效、真实模型有合法改善是三个分别验收的事实；本轮未重新查询远端，先前 535 条/开关状态仅为历史背景。

确定性与模型结果必须用真实 job→API 的可见结果验证；不存在改善时不强制造模型输出。不能把 fixture 或健康 200 当作真实部署效果。所有写库验证用明确 DATA_DIR 隔离，加载部署 env 后再覆盖，断言实际 engine 路径。

## 需要原始证据的情况

质疑别名语义、配偶词修复范围、历史 baseline 保留合同或需要复跑诊断时，读取 [证据](evidence/terminology-investigation.md)。实施范围与验收以本任务 prd/design/implement 为准。
