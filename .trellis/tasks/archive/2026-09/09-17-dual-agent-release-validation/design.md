# G 技术设计

## 发布清单与事实来源

每次发布使用一份安全manifest：目标SHA、backend依赖、agent lockfile/dist hash、迁移head、配置白名单摘要、systemd用户unit/PID/启动时间、候选回退版本。绝不导出env全文、JWT、Provider密钥或真实家庭数据。动态Git HEAD不能证明已加载代码，启动时间也只作辅助，须用实际schema/功能探针与制品对应验证。

只读核对命令以 `systemctl --user show/status`、进程cwd/命令、`lsof`、实际OpenAPI及经授权业务API为入口。服务健康端点按实际代码核对（sidecar为/healthz、另有/readyz）；健康不等于lease认证或worker运行。

## 发布流程

1. 冻结C/D/E/F累计版本，确认迁移序号与向前兼容；本地复验G只负责环境相关项，不无理由重复全部测试。
2. 生产使用受支持online backup，先在隔离副本升级；DATA_DIR在导入app之前显式设置，DATABASE_URL环境变量无效。
3. 记录在途run/batch与租约；选择有界排空/维护窗口，避免重启令已返回未保存调用退化unknown。超窗则中止发布并保留现场，不强行清队列。
4. 按兼容矩阵部署backend schema/读写能力、构建sidecar并重启对应user服务；若不具滚动兼容，则明确短维护窗口，不让新sidecar撞旧extra-forbid schema。
5. 核对运行manifest、数据库版本、listener/JWT隔离、worker lease与真实schema字段；本地Vite使用的是主检出，前端合并/HMR与远端发布分开记录。
6. 实际失败可回到已批准代码/制品；迁移仅允许安全回退，无损兼容保留审计/产物，危险downgrade拒绝。

## 合成真实验证环境

优先新建隔离DATA_DIR并创建合成账户/空间/关系/owner同意，经正常API和worker触发助手与管家；不复制生产隐私进prompt。只从既有部署配置安全加载当前Provider选择与凭据，不能在任务文档保存值，也不改CCSwitch/Pi现有来源。

按PRD上限在发起每次物理请求前计数/预留预算，重试/压缩全部计入；禁止测试服务的后台扫描额外发送未计数请求。只打开合成空间所需的同等开关。达到任何界限即停止新请求并有界收尾，已发送未知继续保守记账。

对于合法空items/无可改善候选，应记录checked/无改善，不伪造投影作为成功应用；另用合成合法可改善输入验证真实消费。事实/授权变化导致拒绝也是独立结果，不与请求失败混淆。

## 线上只读观察与报告

模型小样本在隔离环境；生产发布后的观察优先正常作业和已获授权交互，单列生产来源。管家健康用max(published_at)推进、job分布与assist状态，不用generation总行数。助手排队/失败/正文各按D精度报告，无法关联的旧样本保持未知。

小样本报告按run/call列安全ID、运行SHA、配置摘要、duration source、请求次数/usage来源、合法输出/应用结果，不保存完整prompt/thinking/回复。最终父验收把程序修复、模型等待、用户可见等待分别写清。
