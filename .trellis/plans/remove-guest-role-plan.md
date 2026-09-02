# 移除 guest 空间角色实施计划

## 背景

当前系统支持三种空间角色：`space_admin`、`member`、`guest`。

guest 角色的设计目的是提供受限的 household 空间访问：
- guest 不获得 `household_detail` 可见性（只有 baseline）
- guest 不能邀请成员
- guest 不能提交管理员申请
- guest 不能使用 controlled-web 功能

用户确认暂时不需要访客功能，决定移除 guest 角色。

## 影响范围分析

### 后端代码（31 处引用）

#### 1. 数据模型层
- `backend/app/models/space.py`
  - `SPACE_MEMBER_ROLES` 常量
  - `SpaceMember` 的 `CheckConstraint`
  - 文档注释

#### 2. Schema 层
- `backend/app/schemas/space.py`
  - `SpaceMemberDisplay.role` 类型定义

#### 3. API 层
- `backend/app/api/spaces.py` - 文档注释
- `backend/app/api/controlled_web.py` - guest 权限检查

#### 4. 命令/服务层
- `backend/app/commands/spaces.py` - 邀请授权检查
- `backend/app/commands/manager_applications.py` - `_reject_guest_only` 函数
- `backend/app/services/visibility.py` - household_detail 判定逻辑
- `backend/app/services/steward.py` - `_active_member_ids` 的 `include_guest` 参数
- `backend/app/services/controlled_web.py` - guest 权限检查
- `backend/app/services/recommendation_matrix.py` - 注释

#### 5. 数据库迁移
- `backend/migrations/versions/0008_v2_foundation.py`
- `backend/migrations/versions/0022_system_admin_space_manager.py`
- 需要新增迁移移除 CHECK 约束中的 guest

### 前端代码（26 处引用）

#### 1. 类型定义
- `frontend/src/types/api.ts` - `SpaceRole` 类型

#### 2. Store 层
- `frontend/src/stores/spaces.ts` - `canInvite` getter

#### 3. 组件层
- `frontend/src/components/member/SpaceGovernancePanel.vue` - 角色标签和提示
- `frontend/src/components/member/InviteMemberDialog.vue` - 注释
- `frontend/src/views/SystemAdminView.vue` - 角色显示文本

#### 4. 路由守卫
- `frontend/src/router/__tests__/guard.spec.ts`

### 测试代码（23+ 处引用）

#### 后端测试
- `test_authz_matrix.py` - 可见性测试
- `test_controlled_web.py` - 权限测试
- `test_manager_applications.py` - 管理员申请测试
- `test_space_profile_refs_api.py` - 引用查看测试
- `test_space_invite_authz.py` - 邀请授权测试

#### 前端测试
- 多个组件测试中的 guest 角色测试用例

### 架构文档
- `.trellis/spec/architecture.md` - 多处提及 guest 角色语义

## 实施方案

### 阶段 1：数据库迁移（新建迁移）

创建 `0026_remove_guest_role.py`：

1. **数据清理**（如果有 guest 数据）
   - 检查是否存在 `role='guest'` 的记录
   - 选项 A：升级为 `member`（保留访问权）
   - 选项 B：删除 guest 成员关系（移除访问权）
   - **推荐**：升级为 `member`，因为已经授予的访问权不应在系统变更时被撤销

2. **约束更新**
   - 删除旧的 `ck_sm_role` 约束
   - 创建新的约束：`CHECK(role IN ('space_admin','member'))`

3. **回滚支持**
   - downgrade 恢复 guest 支持（约束改回三值）
   - 不恢复已升级的数据（单向升级）

### 阶段 2：后端代码更新

#### 2.1 模型和常量
```python
# backend/app/models/space.py
SPACE_MEMBER_ROLES = ("space_admin", "member")  # 移除 "guest"
# 更新 CheckConstraint
# 更新文档注释
```

#### 2.2 Schema
```python
# backend/app/schemas/space.py
role: Literal["space_admin", "member"]  # 移除 "guest"
```

#### 2.3 简化逻辑

**权限检查简化**：
- `backend/app/api/controlled_web.py` - 移除 `or member.role == "guest"` 检查
- `backend/app/services/controlled_web.py` - 同上

**邀请授权简化**：
- `backend/app/commands/spaces.py` - 移除 guest 排除逻辑，简化为"active 成员均可邀请"

**管理员申请简化**：
- `backend/app/commands/manager_applications.py` - 删除 `_reject_guest_only` 函数及其调用

**可见性判定简化**：
- `backend/app/services/visibility.py` - `_shared_space_levels` 函数简化
  - 移除 `guest_involved` 检查
  - household 空间双方 active → 直接授予 household_detail

**Steward 简化**：
- `backend/app/services/steward.py` - `_active_member_ids` 移除 `include_guest` 参数
  - 所有调用点移除此参数（当前只有一处调用，传 `False`）

### 阶段 3：前端代码更新

#### 3.1 类型定义
```typescript
// frontend/src/types/api.ts
export type SpaceRole = 'space_admin' | 'member'  // 移除 'guest'
```

#### 3.2 Store 简化
```typescript
// frontend/src/stores/spaces.ts
get canInvite() {
  return this.currentMembership !== null  // 移除 && this.currentRole !== 'guest'
}
```

#### 3.3 组件更新
- `SpaceGovernancePanel.vue` - 移除 guest 标签、提示和样式
- `SystemAdminView.vue` - 移除 guest 角色显示分支
- 移除相关注释

### 阶段 4：测试更新

#### 4.1 后端测试
- `test_authz_matrix.py` - 移除丙（guest）相关测试，简化为甲乙两人场景
- `test_controlled_web.py` - 删除 `test_search_denied_for_guest_member`
- `test_manager_applications.py` - 删除 `test_unconfirmed_and_guest_rejected` 中的 guest 部分
- `test_space_profile_refs_api.py` - 移除 guest 丙，调整为两人或三人（都是 member）
- `test_space_invite_authz.py` - 删除 `test_guest_cannot_invite_member`

#### 4.2 前端测试
- 移除所有 guest 相关测试用例
- 更新测试描述（如 "除 guest" → "所有成员"）
- `guard.spec.ts` - 移除 `roles = ['member', 'guest']` 循环中的 guest

### 阶段 5：文档更新

#### 5.1 架构文档
- `.trellis/spec/architecture.md`
  - §0.1：移除 "guest" 提及
  - §0.2：更新为 "空间角色为 `space_admin`、`member`"
  - §0.7：移除 "除 guest" 的限定

#### 5.2 代码注释
- 搜索所有 "guest" / "访客" 注释并更新或删除

## 实施顺序

1. **创建并运行数据库迁移**（将现有 guest 升级为 member）
2. **更新后端代码**（模型、schema、服务层）
3. **更新后端测试**（移除 guest 测试用例）
4. **运行后端测试验证**（排除 ownership-transfer deadlock）
5. **更新前端代码**（类型、store、组件）
6. **更新前端测试**（移除 guest 测试用例）
7. **运行前端测试验证**
8. **更新架构文档**
9. **提交并归档任务**

## 验证清单

- [ ] 数据库迁移 upgrade/downgrade 成功
- [ ] 后端 ruff/mypy 通过
- [ ] 后端测试全部通过（排除已知 deadlock）
- [ ] 前端 type-check/lint 通过
- [ ] 前端测试全部通过
- [ ] 前端构建成功
- [ ] 架构文档已更新
- [ ] 无遗留 "guest" 引用（除归档代码和 git 历史）

## 风险评估

### 低风险
- guest 角色在当前系统中没有独特的产品价值
- 所有 guest 限制都可以通过移除获得更简单的实现
- 迁移将现有 guest 升级为 member，不会导致权限丢失

### 注意事项
- 如果生产环境有 guest 数据，迁移会自动升级为 member
- 升级是单向的（downgrade 不恢复数据）
- 确保没有业务流程依赖 guest 角色的特殊限制

## 预估工作量

- 数据库迁移：30 分钟
- 后端代码更新：1 小时
- 后端测试更新：1 小时
- 前端代码更新：30 分钟
- 前端测试更新：30 分钟
- 文档更新：30 分钟
- 验证和调试：1 小时

**总计：约 5 小时**
