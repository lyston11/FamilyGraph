<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { AdminApiError } from '@/api/client'
import { getPlatformFeatures, updatePlatformFeatures } from '@/api/platform-features'
import type { AdminPlatformFeatureState } from '@/types/api'
import PageState from '@/components/PageState.vue'

const state = ref<'loading' | 'ready' | 'error'>('loading')
const features = ref<AdminPlatformFeatureState | null>(null)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const saving = ref<'memory' | 'rag' | 'steward' | null>(null)

async function load(): Promise<void> {
  state.value = 'loading'
  error.value = null
  try {
    features.value = await getPlatformFeatures()
    state.value = 'ready'
  } catch (reason) {
    error.value = reason instanceof AdminApiError ? reason.message : '能力状态加载失败，请重试'
    state.value = 'error'
  }
}

async function toggle(key: 'memory' | 'rag'): Promise<void> {
  if (!features.value || saving.value) return
  const next = key === 'memory' ? !features.value.memory_enabled : !features.value.rag_enabled
  saving.value = key
  error.value = null
  notice.value = null
  try {
    features.value = await updatePlatformFeatures({
      memory_enabled: key === 'memory' ? next : features.value.memory_enabled,
      rag_enabled: key === 'rag' ? next : features.value.rag_enabled,
    })
    notice.value = `${key === 'memory' ? 'Memory' : 'RAG'} 已${next ? '启用' : '停用'}，已以服务端状态同步。`
  } catch (reason) {
    error.value = reason instanceof AdminApiError ? reason.message : '保存失败，开关已保持服务端状态'
    await load()
  } finally {
    saving.value = null
  }
}

type AssistKind = 'candidate' | 'ranking' | 'explanation' | 'terminology'

function currentAssist(kind: AssistKind): boolean {
  return features.value?.steward_assist[kind] ?? false
}

/** 09-13：平台级 Steward 辅助三开关治理；全量 PUT（memory/rag 原样透传） */
async function toggleAssist(kind: AssistKind): Promise<void> {
  if (!features.value || saving.value) return
  const next = !currentAssist(kind)
  saving.value = 'steward'
  error.value = null
  notice.value = null
  try {
    features.value = await updatePlatformFeatures({
      memory_enabled: features.value.memory_enabled,
      rag_enabled: features.value.rag_enabled,
      steward_assist_candidate: kind === 'candidate' ? next : currentAssist('candidate'),
      steward_assist_ranking: kind === 'ranking' ? next : currentAssist('ranking'),
      steward_assist_explanation: kind === 'explanation' ? next : currentAssist('explanation'),
      steward_assist_terminology: kind === 'terminology' ? next : currentAssist('terminology'),
    })
    notice.value = `管家辅助（${kind}）已${next ? '启用' : '停用'}，已与服务端状态同步。`
  } catch (reason) {
    error.value = reason instanceof AdminApiError ? reason.message : '保存失败，开关已保持服务端状态'
    await load()
  } finally {
    saving.value = null
  }
}

function sourceLabel(source: AdminPlatformFeatureState['memory_source']): string {
  if (source === 'environment') return '环境回退（尚未保存平台配置）'
  if (source === 'deployment') return '部署级关闭（环境安全兜底）'
  return '平台配置'
}

onMounted(() => void load())
</script>

<template>
  <div>
    <h1 class="ag-page-title">平台能力</h1>
    <p class="ag-page-subtitle">
      管理全平台的 Memory 与 RAG 开关。这里只显示能力元数据，不读取家庭记忆、候选或检索内容。
    </p>

    <p v-if="notice" class="form-success" role="status" data-testid="platform-feature-success">{{ notice }}</p>
    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无平台能力配置" @retry="load" />

    <template v-else-if="features">
      <p class="ag-card ag-feature-source" data-testid="platform-feature-source">
        Memory 来源：{{ sourceLabel(features.memory_source) }} · RAG 来源：{{ sourceLabel(features.rag_source) }}<span v-if="features.updated_at"> · 最近更新 {{ features.updated_at }}</span>
      </p>
      <div class="ag-feature-grid">
        <section class="ag-card ag-feature-card" data-testid="platform-feature-memory">
          <div>
            <p class="ag-feature-kicker">能力 01</p>
            <h2>Memory</h2>
            <p>候选、确认后的私有记忆与空间共享记忆。停用不会删除既有数据。</p>
          </div>
          <button
            type="button"
            class="ag-feature-switch"
            role="switch"
            :aria-checked="features.memory_enabled"
            :aria-label="`Memory：${features.memory_enabled ? '已启用' : '已停用'}`"
            :disabled="saving !== null || (features.memory_source === 'deployment' && !features.memory_enabled)"
            data-testid="platform-feature-memory-toggle"
            @click="toggle('memory')"
          >
            <span class="ag-feature-switch-track"><span class="ag-feature-switch-thumb"></span></span>
            <span>{{ saving === 'memory' ? '保存中…' : features.memory_source === 'deployment' ? '部署关闭' : features.memory_enabled ? '已启用' : '已停用' }}</span>
          </button>
        </section>
        <section class="ag-card ag-feature-card" data-testid="platform-feature-rag">
          <div>
            <p class="ag-feature-kicker">能力 02</p>
            <h2>RAG</h2>
            <p>授权知识的检索与引用。停用不会改变 Memory 的候选与确认流程。</p>
          </div>
          <button
            type="button"
            class="ag-feature-switch"
            role="switch"
            :aria-checked="features.rag_enabled"
            :aria-label="`RAG：${features.rag_enabled ? '已启用' : '已停用'}`"
            :disabled="saving !== null || (features.rag_source === 'deployment' && !features.rag_enabled)"
            data-testid="platform-feature-rag-toggle"
            @click="toggle('rag')"
          >
            <span class="ag-feature-switch-track"><span class="ag-feature-switch-thumb"></span></span>
            <span>{{ saving === 'rag' ? '保存中…' : features.rag_source === 'deployment' ? '部署关闭' : features.rag_enabled ? '已启用' : '已停用' }}</span>
          </button>
        </section>
        <section class="ag-card ag-feature-card" data-testid="platform-feature-steward-assist">
          <div>
            <p class="ag-feature-kicker">能力 03</p>
            <h2>管家模型辅助</h2>
            <p>
              Steward 的候选补全 / 排序 / 解释 / 称谓优化四类模型辅助的平台级开关；
              仍需空间 owner 打开对应空间级开关才会实际调用模型。
            </p>
          </div>
          <div class="ag-assist-switches">
            <button
              v-for="assist in ([
                { kind: 'candidate', label: '候选补全' },
                { kind: 'ranking', label: '推荐排序' },
                { kind: 'explanation', label: '卡片解释' },
                { kind: 'terminology', label: '称谓优化' },
              ] as const)"
              :key="assist.kind"
              type="button"
              class="ag-feature-switch"
              role="switch"
              :aria-checked="currentAssist(assist.kind)"
              :aria-label="`管家辅助 ${assist.label}：${currentAssist(assist.kind) ? '已启用' : '已停用'}`"
              :disabled="saving !== null"
              :data-testid="`platform-feature-steward-assist-${assist.kind}`"
              @click="toggleAssist(assist.kind)"
            >
              <span class="ag-feature-switch-track"><span class="ag-feature-switch-thumb"></span></span>
              <span>{{ assist.label }}：{{ currentAssist(assist.kind) ? '已启用' : '已停用' }}</span>
            </button>
          </div>
        </section>
      </div>
      <p v-if="error" class="form-error" role="alert" data-testid="platform-feature-error">{{ error }}</p>
    </template>
  </div>
</template>

<style scoped>
.ag-feature-source {
  margin: 0 0 var(--ag-space-4);
  color: var(--ag-text-secondary);
}

.ag-feature-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ag-space-4);
}

.ag-feature-card {
  min-height: 230px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: var(--ag-space-5);
}

.ag-feature-card h2 {
  margin: 4px 0 8px;
  color: var(--ag-text);
  font-size: 22px;
}

.ag-feature-card p:not(.ag-feature-kicker) {
  margin: 0;
  color: var(--ag-text-secondary);
  line-height: 1.7;
}

.ag-feature-kicker {
  margin: 0;
  color: var(--ag-primary);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.ag-assist-switches {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--ag-space-2);
}

.ag-feature-switch {
  min-height: 48px;
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: 10px;
  padding: 8px 14px 8px 8px;
  border: 1px solid var(--ag-glass-border);
  border-radius: 999px;
  background: var(--ag-glass-bg-strong);
  color: var(--ag-text);
  font: inherit;
  cursor: pointer;
}

.ag-feature-switch[aria-checked='true'] {
  color: var(--ag-primary);
  border-color: var(--ag-primary);
}

.ag-feature-switch:disabled {
  cursor: wait;
  opacity: 0.65;
}

.ag-feature-switch-track {
  width: 36px;
  height: 22px;
  display: flex;
  align-items: center;
  padding: 3px;
  border-radius: 999px;
  background: var(--ag-border);
}

.ag-feature-switch[aria-checked='true'] .ag-feature-switch-track {
  justify-content: flex-end;
  background: var(--ag-primary);
}

.ag-feature-switch-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--ag-text);
}

@media (max-width: 700px) {
  .ag-feature-grid { grid-template-columns: 1fr; }
}
</style>
