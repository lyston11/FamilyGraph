<script setup lang="ts">
import type { SpaceProfileRefInfo } from '@/types/api'

/**
 * 待确档引用列表（AC-F2 可观测性，v2 Gap1）：
 * provisional 人物以 space_profile_refs 最小节点出现在空间中，不是正式
 * SpaceMember —— 本组件仅展示名字投影，视觉上与正式成员明确区分。
 */
defineProps<{
  refs: SpaceProfileRefInfo[]
}>()
</script>

<template>
  <section v-if="refs.length > 0" class="pending-refs" data-test="profile-ref-section">
    <span class="label">待确档引用</span>
    <el-tag
      v-for="ref in refs"
      :key="ref.profile_id"
      size="small"
      type="info"
      effect="plain"
      class="ref-tag"
      :title="'此人尚未完成身份确认，仅以最小节点引用出现'"
      :data-test="`profile-ref-item-${ref.profile_id}`"
    >
      <span class="ref-name" data-test="profile-ref-name">{{ ref.name }}</span>
    </el-tag>
  </section>
</template>

<style scoped>
.pending-refs {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.ref-tag {
  border-style: dashed;
}
</style>
