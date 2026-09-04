# 子任务 2 决策与实现笔记

- “全业务只读”不是无限制数据库浏览：后台以 `space_admin → spaces` 为聚合主线，读模型字段显式分级。
- system_admin 与家庭 `space_admin` 是两个身份；system_admin 不进入家庭 visibility 链。
- 普通列表可直读但仍审计；敏感详情用绑定单个 user/space 的 30 分钟工作会话，票据只存内存，响应 no-store。
- 基础档案允许姓名、头像缩略图、生卒、性别、简介；地址/健康/未成年人字段禁止。当前电话/邮箱/地址字段不存在，不在本任务凭空新增。
- 关系只读结构边和 confirmed 事实；RawRelationInput.text、证据原文、private note、原始消息禁止。
- Agent 原始错误可留在服务端受限诊断区，但浏览器 API 必须二次脱敏；审计不保存响应正文。
- 无管理员/双管理员/锁定管理员空间进入异常队列，后台不自动修复。
- approve/reject 是唯一业务写例外：approve 可无理由，reject 必须理由；均二次确认、单事务、审计、终态不可改判。
- 现有 audit_log 的家庭 actor 外键不能承载独立管理员读取审计，新增直接引用 system_admin 的表。
