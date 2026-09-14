# V-I01 执行探针交接

本文件记录集成验证的实现边界；尚未执行，不替代验证结果。

## 既有验证

- A 的 `scripts/smoke/run_api_smoke.py` 已扩展真实 listener 的创建、确认、检索、RAG 保存、来源撤销和副本失效链。主线程拥有该脚本。
- A 前端使用真实 Axios serializer 的受控 transport 检查请求形状与错误；不把这称为真实后端联调。
- C 需用锁定 Pi SDK 分别验证实际自动与手动压缩，网络模型由 fake stream 替代。

## B/D 完成后的集成探针

在完全独立的 DATA_DIR / 动态端口 / 合成身份和资料中运行真实 FastAPI 与实际 `SidecarWorker`：

1. 通过 API 新建并确认中文手工记忆；RAG 关闭时不索引，开启后由 D 的真实维护入口补齐。
2. 建立真实会话/持久消息及 queued run，sidecar 通过真实内部 HTTP 客户端领取 attempt、读取 context。
3. 使用实际 `buildRunSession` / Pi / worker / EventBuffer，仅 provider stream 为可控假流；本轮来源取自服务端 context，生成带实际句柄的合成回答。
4. 校验内部 attempt/build 绑定、事件持久化、SSE、历史与按 run/seq 补取引用一致。假流只证明协议，不证明真实回答忠实度。
5. 在第二轮已有历史上可额外执行实际手动 compact，确认旧合成事实进入摘要并被后续 prompt 看见；自动路径证据仍来自 C 独立 SDK 回归，不能混称。
6. 撤销记忆后读取历史/SSE/补取：正文按既有会话保留合同，受限 citation metadata 隐藏并计 unavailable；再次搜索不命中。旧已提交事件同请求重试不能重写持久结果。

报告仅保留检查结果、计数、版本与稳定错误码；不记录 token、完整 prompt、真实家庭内容或外部凭据。所有服务、数据库和测试生成的临时凭据随探针退出清理。若实现需要构建 sidecar，使用现有 agent build，不修改依赖包。

## 所有权

主线程负责新增/扩展集成 smoke，B 执行者负责后端/sidecar/frontend 单包与跨协议回归，D 负责维护执行入口与迁移回归。B/D 执行者不要改主线程的 smoke 文件，交付稳定接口和运行命令即可。
