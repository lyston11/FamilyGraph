# M0 工程骨架与认证基座（里程碑章程）

> 权威上下文：[HANDOFF.md](../../HANDOFF.md)、[architecture.md](../../spec/architecture.md)。本任务为**父任务**，只做编排与门禁，不直接承载需求——需求在各子任务 PRD。

## 里程碑目标（出口）

能登录、能改 PIN、且凭据安全合同完整：全新环境一条命令启动；首启管理员初始化；限流/消歧/会话失效全部生效。

## 子任务与依赖

| 子任务 | 内容 | 依赖 |
|---|---|---|
| [m0a 工程骨架与开发部署](../08-25-m0a-engineering-scaffold/prd.md) | 前后端骨架/Compose/门禁脚本 | 无 |
| [m0b 认证、首启和凭据安全](../08-25-m0b-auth-bootstrap-security/prd.md) | 登录/PIN/限流/JWT/challenge/审计 | m0a |

## 审计边界修正记录

- 原 M0 的"管理员后台界面"明确移出至 m4b（此处仅 bootstrap 初始化）。
- 认证安全合同（限流/token 失效/challenge）由审计新增，落在 m0b `[AD-2]`。

## 出口门禁

- [ ] m0a、m0b 验收标准全绿
- [ ] HANDOFF §五.1 通过（一条命令启动）
- [ ] 规范文件用真实代码完成第一轮校正（Bootstrap 任务联动）
