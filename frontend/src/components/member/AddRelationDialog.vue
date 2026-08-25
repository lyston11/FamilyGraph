<script setup lang="ts">
import { ref } from 'vue'

import type { DirClass, Member } from '@/types/api'

import { apiClient } from '@/api/client'
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
  const { data } = await apiClient.get<Member[]>('/users', { params: { q: keyword.value.trim() } })
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
  <el-dialog
    :model-value="visible"
    title="添加关系"
    width="420px"
    @update:model-value="emit('update:visible', $event)"
    @closed="close"
  >
    <el-alert
      v-if="submitted"
      type="success"
      :closable="false"
      title="请求已发送，等待对方确认后生效"
    />
    <template v-else>
      <el-form label-width="72px">
        <el-form-item label="搜索家人">
          <div class="search-row">
            <el-input v-model="keyword" placeholder="输入名字前缀" @keyup.enter="search" />
            <el-button @click="search">搜索</el-button>
          </div>
          <div class="hint">找不到？请先通过「添加成员」建档</div>
        </el-form-item>

        <el-form-item v-if="results.length" label="选择">
          <ul class="candidates">
            <li
              v-for="m in results"
              :key="m.id"
              :class="{ active: selected?.id === m.id }"
              data-test="candidate"
              @click="choose(m)"
            >
              {{ m.name }}（#{{ m.id }}）
            </li>
          </ul>
        </el-form-item>

        <el-form-item v-if="selected" label="TA 是我的">
          <el-radio-group v-model="dirClass" data-test="dir-class-group">
            <el-radio v-for="opt in DIR_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.text }}
            </el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="selected" label="称谓">
          <el-input v-model="label" placeholder="选填，如：三叔公" maxlength="64" />
        </el-form-item>
      </el-form>
    </template>

    <template #footer>
      <el-button @click="close">关闭</el-button>
      <el-button
        v-if="!submitted"
        type="primary"
        :disabled="!selected"
        :loading="submitting"
        data-test="submit-relation"
        @click="submit"
      >
        发送请求
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.search-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.candidates {
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
}

.candidates li {
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 4px;
}

.candidates li.active,
.candidates li:hover {
  background: var(--el-fill-color-light);
}
</style>
