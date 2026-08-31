<script setup lang="ts">
import { NModal, NButton } from 'naive-ui'

import SpaceGovernancePanel from '@/components/member/SpaceGovernancePanel.vue'

/** 兼容既有 Home/FamilySpace 入口的弹窗包装；治理内容集中在 SpaceGovernancePanel。 */
defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', value: boolean): void }>()
</script>

<template>
  <NModal
    :show="visible"
    preset="card"
    title="空间管理"
    data-test="space-governance-dialog"
    @update:show="emit('update:visible', $event)"
  >
    <SpaceGovernancePanel />
    <template #footer>
      <div class="footer-actions">
        <NButton data-test="governance-close" @click="emit('update:visible', false)">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.footer-actions { display: flex; justify-content: flex-end; }
</style>

<style>
[data-test='space-governance-dialog'] { width: min(700px, calc(100vw - 48px)); }
</style>
