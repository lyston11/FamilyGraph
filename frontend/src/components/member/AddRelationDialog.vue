<script setup lang="ts">
import { ref } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, NModal, NRadio, NRadioGroup } from 'naive-ui'

import type { DirClass, Member } from '@/types/api'

import { fetchMembersByPrefix } from '@/api/members'
import { useAuthStore } from '@/stores/auth'
import { useGraphStore } from '@/stores/graph'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void; (e: 'submitted'): void }>()

const graph = useGraphStore()
const auth = useAuthStore()

/** 四分类自然问法（D2）：TA 是我的 ___ */
const DIR_OPTIONS: { value: DirClass; text: string }[] = [
  { value: 'elder', text: '长辈' },
  { value: 'younger', text: '晚辈' },
  { value: 'peer', text: '平辈' },
  { value: 'spouse', text: '配偶' },
]

const keyword = ref('')
const results = ref<Member[]>([])
const selected = ref<Member | null>(null)
const dirClass = ref<DirClass>('elder')
const label = ref('')
const submitting = ref(false)
const submitted = ref(false)

async function search() {
  if (!keyword.value.trim()) return
  const data = await fetchMembersByPrefix(keyword.value.trim())
  results.value = data.filter((m) => m.id !== auth.user?.id)
}

function choose(m: Member) {
  selected.value = m
}

async function submit() {
  if (!selected.value) return
  submitting.value = true
  try {
    await graph.connect(selected.value.id, dirClass.value, label.value.trim() || null)
    submitted.value = true
    emit('submitted')
  } finally {
    submitting.value = false
  }
}

function close() {
  emit('update:visible', false)
  keyword.value = ''
  results.value = []
  selected.value = null
  label.value = ''
  submitted.value = false
}
</script>

<template>
  <NModal
    :show="visible"
    preset="card"
    title="添加关系"
    data-test="add-relation-dialog"
    @update:show="emit('update:visible', $event)"
    @after-leave="close"
  >
    <NAlert v-if="submitted" type="success" :show-icon="true" title="请求已发送，等待对方确认后生效" />

    <NForm v-else label-placement="left" :label-width="72" :show-feedback="false">
      <NFormItem label="搜索家人">
        <div class="search-block">
          <div class="search-row">
            <NInput v-model:value="keyword" placeholder="输入名字前缀" @keyup.enter="search" />
            <NButton @click="search">搜索</NButton>
          </div>
          <div class="hint">找不到？请先通过「添加成员」建档</div>
        </div>
      </NFormItem>

      <NFormItem v-if="results.length" label="选择">
        <ul class="candidates">
          <li
            v-for="m in results"
            :key="m.id"
            class="candidate"
            :class="{ active: selected?.id === m.id }"
            data-test="candidate"
            @click="choose(m)"
          >
            {{ m.name }}（#{{ m.id }}）
          </li>
        </ul>
      </NFormItem>

      <NFormItem v-if="selected" label="TA 是我的">
        <NRadioGroup v-model:value="dirClass" data-test="dir-class-group">
          <NRadio v-for="opt in DIR_OPTIONS" :key="opt.value" :value="opt.value">
            {{ opt.text }}
          </NRadio>
        </NRadioGroup>
      </NFormItem>

      <NFormItem v-if="selected" label="称谓">
        <NInput v-model:value="label" placeholder="选填，如：三叔公" :maxlength="64" />
      </NFormItem>
    </NForm>

    <template #footer>
      <div class="footer-actions">
        <NButton @click="close">关闭</NButton>
        <NButton
          v-if="!submitted"
          type="primary"
          :disabled="!selected"
          :loading="submitting"
          data-test="submit-relation"
          @click="submit"
        >
          发送请求
        </NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.search-block {
  width: 100%;
}

.search-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.hint {
  color: var(--fg-ink-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.candidates {
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
}

.candidate {
  cursor: pointer;
  padding: 6px 10px;
  border-radius: var(--fg-radius-control);
  border: 1px solid transparent;
}

.candidate.active,
.candidate:hover {
  background-color: var(--fg-accent-soft);
  border-color: var(--fg-accent);
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='add-relation-dialog'] {
  width: min(420px, calc(100vw - 48px));
}
</style>
