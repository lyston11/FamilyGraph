# 子任务 2 研究：现有模型与后台读取边界

- `backend/app/models/user.py`：现有基础档案字段为 name/gender/birth/death/bio/avatar_path/profile_status；没有电话、邮箱、地址、学校/单位、健康字段。
- `backend/app/models/space.py`：`SpaceMember.role` 为 `space_admin|member`，active 唯一索引按 space；`SpaceProfileRef` 是 provisional 最小引用；`FamilySpace.owner_id` 不能作为运行时授权。
- `backend/app/models/relation.py` 与 `relationship_facts.py`：Relation 是关系边，SourceFact 是结构化事实，RawRelationInput.text 是 append-only 原文，必须禁止进入后台 response。
- `backend/app/models/attachment.py`：url_or_path 和 description 属于内容/存储敏感字段，后台只能安全元数据。
- `backend/app/models/agent.py`：AgentRun/Job/ToolCall 含 error_json/content/result 等字段，后台需单独投影和错误脱敏。
- `backend/app/models/audit_log.py`：actor_id FK 指向家庭 users，无法直接表达 system_admin；需要独立 admin access session/audit 表。
- 现有 `admin_metadata.py`/`system_admin.py` 只有最小治理查询，旧 `admin.py` 混有家庭 break-glass，不能直接复用。
