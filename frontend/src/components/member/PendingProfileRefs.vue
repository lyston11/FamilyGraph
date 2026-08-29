<script setup lang="ts">
import type { SpaceProfileRefInfo } from '@/types/api'

/**
 * 待确档引用列表（AC-F2 可观测性，v2 Gap1）：
 * provisional 人物以 space_profile_refs 最小节点出现在空间中，不是正式
 * SpaceMember —— 本组件仅展示名字投影，视觉上与正式成员明确区分
 * （虚线章徽章，--fg-status-provisional，design.md §3.4）。
 */
defineProps<{
  refs: SpaceProfileRefInfo[]
}>()
</script>

<template>
  <section v-if="refs.length > 0" class="pending-refs" data-test="profile-ref-section">
    <span class="label">待确档引用</span>
    <span
      v-for="ref in refs"
      :key="ref.profile_id"
      class="fg-badge fg-badge--provisional ref-tag"
      :title="'此人尚未完成身份确认，仅以最小节点引用出现'"
      :data-test="`profile-ref-item-${ref.profile_id}`"
    >
      <span class="ref-name" data-test="profile-ref-name">{{ ref.name }}</span>
    </span>
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
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.ref-name {
  /* 恢复徽章内白色空格折叠（默认 nowrap）下的名字投影展示 */
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
