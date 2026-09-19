# 修复家族空间越权访问与关系树断链

## Goal

修复家族空间准入与家族树读取的安全边界：家庭空间（household）成员资格不等于家族空间（lineage）成员资格——只加入别人的家庭空间不会获得其家族树读取权，查看家族树需另行申请并由目标本人审批。双方已同属一个 household 时复用现有空间，不再为两人创建新家庭。同步修复演示数据中朱佛女的空间名册与姐弟关系展示，使关系投影和结构连线一致。

## Requirements

### R1 家庭空间与家族空间完全分离

- 加入 `household` 只产生该 household 的 pending/active membership；不得因为 `lineage_space_id` 配对、空间列表、家族事实或 household 卡片而自动插入或激活对应 `lineage` membership。
- household 加入申请由被申请空间的管理员（即被加入空间的那个人）审核；申请人不能自批。审核通过后，申请人只能读取 household 范围内容。
- 申请进入目标 `lineage` 是独立的第二个 pending membership 申请；由该家族空间的管理员（即被加入空间的那个人）审核。未获批前不得读取 lineage 家族树。
- 目标不可见保持既有防枚举 404；申请目标必须是对应 kind，不能因空间解析顺序落入另一种空间。

### R1a 已有共同家庭不重复创建

- 双方已经是同一个 household 的有效 active 成员时，复用已有家庭，不再为两人新建 household；该家庭包含其他成员时同样适用。
- 仅共享 lineage、仅 pending 或已退出的 household membership，不能视为已有共同家庭。
- 建议生成、旧建议执行和实际创建命令都不得绕过该规则；重复或并发执行不得产生重复家庭。
- 不删除、合并或重命名既有家庭，也不擅自改变其成员与角色。

### R2 家族树读取必须以 lineage membership 为边界

- lineage 的 PersonalFamilyView、渐进 demand、Steward 读取与旧 `/api/graph/me?space_id=` 必须要求 viewer 在该 lineage 有 active membership；household membership、lineage_space_id 配对、关系可见性和关系路径均不能替代 lineage membership。
- household 成员即使与 lineage 成员存在 confirmed 亲属关系，也只能看到 household 范围；必须先由该家族空间管理员批准独立 lineage membership 申请，才能读取家族树。
- lineage owner/active space_admin 的治理入口不被普通树读取门禁破坏；家族空间申请的审批人是该空间 active `space_admin`（owner 即被加入空间的那个人），不新建审批系统。
- 读取拒绝采用既有安全 404 形状；读取请求保持只读，不在 GET 中重建或写入数据库。

### R3 关系树数据一致性

- 演示种子 `朱氏皇族` 名册包含 `朱佛女`；`李氏家族` 保留朱佛女及李氏成员，朱元璋只作为 `李家` household 成员，不作为 `李氏家族` lineage 成员。
- 演示种子补充朱元璋与朱佛女的 confirmed `direct_sibling` SourceFact，使两人均获批成为相应 lineage 节点时能产生 sibling topology edge。
- 既有非空间中间人路径规则、节点集合安全边界和 PFV 版本失效合同保持不变。

### R4 回归范围

- 至少覆盖：加入 household 后仍无 lineage membership；household/lineage 申请均由该空间管理员审批且申请人不得自批；无 lineage membership 的 household 成员读取 lineage PFV/graph 被拒；已有共同 household 不重复建家庭；朱氏/李氏场景的朱佛女节点与姐弟边存在。
- 只运行受影响的后端定向测试与必要静态检查，不运行无关的全量高成本验收。

## Acceptance Criteria

- AC1：双方同属至少一个 active `lineage` 时，申请人可提交 household 加入申请；申请只产生 pending，审批人是该 household 的 active `space_admin`（即被加入空间的那个人）；申请人不得自批，也不自动获得 lineage 读取权。不同族的可见用户不能使用该入口。
- AC2：双方已是同一 household 的 active 成员时，不再新建家庭、不新增重复成员；旧建议、重复或并发执行均不得绕过。仅共享 lineage 或 pending household 不算已有共同家庭。
- AC3：仅 household 成员（无 lineage active membership）读取目标 lineage 家族树时，PFV、demand 与 `/graph/me` 均返回安全 404；该家族空间管理员批准独立 lineage 申请后才可读取。
- AC4：空间 owner/active space_admin 的治理入口不被普通树读取门禁破坏；lineage 申请由该空间 active `space_admin` 审批。
- AC5：种子数据中朱佛女同时出现在朱氏皇族与李氏家族，朱元璋只在李家 household（不在李氏家族）；朱元璋—朱佛女存在 confirmed direct sibling 结构事实，两人均获批成为 lineage 节点时返回 sibling topology edge，且不把路径中间人扩为节点。
- AC6：受影响 backend 定向检查通过；未运行的高成本检查如实记录。
