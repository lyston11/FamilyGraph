<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

import type { Member } from '@/types/api'

/**
 * 画布成员卡（m1d）：头像占位/名字/称谓标签/世代角标；点击开档案抽屉。
 * 通过 Vue Flow 自定义节点插槽传入 data。
 */
interface Props {
  id: string
  data: { member: Member; viewLabel: string | null; generation?: number }
}

const props = defineProps<Props>()

const emit = defineEmits<{ (e: 'open', memberId: number): void }>()

const genderText = computed(() =>
  props.data.member.gender === 'f' ? '女' : props.data.member.gender === 'm' ? '男' : '不详',
)
</script>

<template>
  <div class="member-node" data-test="canvas-member-card" @click="emit('open', data.member.id)">
    <Handle type="target" :position="Position.Top" class="handle" />
    <div class="card-head">
      <span class="avatar">{{ data.member.name.slice(0, 1) }}</span>
      <span class="name">{{ data.member.name }}</span>
    </div>
    <div class="card-meta">
      <el-tag v-if="data.viewLabel" size="small" data-test="view-label">{{ data.viewLabel }}</el-tag>
      <el-tag v-if="data.generation !== undefined" size="small" type="info" data-test="generation-badge">
        第 {{ data.generation }} 代
      </el-tag>
      <span class="gender">{{ genderText }}</span>
    </div>
    <Handle type="source" :position="Position.Bottom" class="handle" />
  </div>
</template>

<style scoped>
.member-node {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  padding: 10px 14px;
  min-width: 130px;
  cursor: pointer;
  box-shadow: var(--el-box-shadow-light);
}

.handle {
  opacity: 0.35;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--el-color-primary-light-7);
  color: var(--el-color-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
}

.name {
  font-weight: 600;
}

.card-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 6px;
}

.gender {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
