<script setup lang="ts">
import { ref } from 'vue'
import type { InputHTMLAttributes as VueInputHTMLAttributes } from 'vue'
import { NButton, NInput, NModal, NRadio, NRadioGroup, useMessage } from 'naive-ui'

import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace } from '@/types/api'

/**
 * 创建空间弹窗（自旧 HomeView 内联逻辑抽出，09-01 Phase 3 流程保全）。
 *
 * - 仅包装既有 `POST /spaces` 命令（spaces store → api/spaces.ts），不静默创建：
 *   弹窗只由调用方的显式入口打开；
 * - 调用方（家庭卡空状态等）决定入口资格，本组件不重复授权判定；
 * - 创建成功后 emit created(spaces store 已 reload 成员关系)。
 */
const props = withDefaults(defineProps<{ visible?: boolean; defaultKind?: 'household' | 'lineage' }>(), {
  visible: false,
  defaultKind: 'household',
})
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'created', space: FamilySpace): void
}>()

const spaces = useSpacesStore()
const message = useMessage()

const name = ref('')
const kind = ref<'household' | 'lineage'>(props.defaultKind)
const submitting = ref(false)

const nameInputProps = {
  'data-test': 'space-name-input',
  'aria-label': '空间名称',
} as VueInputHTMLAttributes

function close(): void {
  emit('update:visible', false)
}

async function submit(): Promise<void> {
  if (!name.value.trim() || submitting.value) return
  submitting.value = true
  try {
    const space = await spaces.create(name.value.trim(), kind.value)
    name.value = ''
    close()
    message.success('空间已创建')
    emit('created', space)
  } catch {
    message.error('创建空间失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <NModal
    :show="props.visible"
    preset="card"
    title="创建空间"
    data-test="space-create-dialog"
    @update:show="emit('update:visible', $event)"
  >
    <NInput
      v-model:value="name"
      placeholder="例如：我们家"
      :maxlength="64"
      :input-props="nameInputProps"
      @keyup.enter="submit"
    />
    <NRadioGroup
      v-model:value="kind"
      class="kind-group"
      name="space-kind"
      data-test="space-kind-group"
      aria-label="空间类型"
    >
      <NRadio value="household">家庭空间（共同生活）</NRadio>
      <NRadio value="lineage">家族空间（谱系）</NRadio>
    </NRadioGroup>
    <template #footer>
      <div class="modal-actions">
        <NButton @click="close">取消</NButton>
        <NButton
          type="primary"
          :loading="submitting"
          :disabled="!name.trim()"
          data-test="space-create-submit"
          @click="submit"
        >创建</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.kind-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='space-create-dialog'] {
  width: min(460px, calc(100vw - 48px));
}
</style>
