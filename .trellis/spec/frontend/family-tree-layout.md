# 家族树分支布局合同

## 适用范围

修改 `familyTreeLayout.ts` 的几何布局或 `FamilyTreeView.vue` 的坐标更新时读取。

## 必须行为

- `computeFamilyTreeLayout(input)` 只接收已确认结构边。parent 约束子女比父母低一代，spouse/partner/sibling 同代；矛盾返回 `null`，页面保留所有人物并提示自由画布回退。
- 横向按后代区间分配宽度，父母几何组居中于其分配的后代区域，同代卡片不重叠。配偶/伴侣先组成相邻块，共同父母可以合成几何组，但不生成关系事实；sibling 只约束同代，不把多对夫妻合成一个不可拆分的横排块。
- 共享后代不得重复渲染、重复计宽。跨支系婚姻连接多个父代组时，按不同亲子连接数、最小成员 id 选择唯一几何区间归属，保留全部真实连线；不承诺任意亲缘图均无交叉。
- 同一输入乱序后逐点坐标一致，孤立成员不丢失，分量纵向分离。
- 页面树模式直接采用完整布局结果。拓扑变化重算分支；称谓/进度更新不重排；同一安全骨架经重算空窗恢复后位置一致。自由画布保留拖动位置和视口，身份/权限切换沿现有逻辑清理。

## 禁止行为

- 每行从零紧缩排布，或在页面逐节点保留旧 x/向右避让而拆散新子树。
- 为绘图删事实、复制人物，或将推测边/个人称谓摘要用于 confirmed 分代。

## 验证

`npm test -- src/composables/__tests__/familyTreeLayout.spec.ts src/composables/__tests__/useFamilyTreeCanvas.spec.ts src/views/__tests__/family-tree.spec.ts`

覆盖三代多分支的居中与区间分离、兄弟姐妹连接多对夫妻、共同父母、跨支系婚姻、唯一节点、确定性，以及拓扑变化/空窗/权限切换/自由画布交互。布局视觉需另用真实 Vue Flow 和 MemberNode 在浏览器复核。

## 关联

- [Composables](hook-guidelines.md)
- [前端质量](quality-guidelines.md)
