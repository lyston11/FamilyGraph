<script setup lang="ts">
/**
 * Steward 运维观测面板（R5，09-11 整改）。
 *
 * 只消费 /admin-api/v1/steward/status 与 /steward/jobs 的安全白名单 DTO：
 * 状态（disabled/paused/running/degraded）、开关解释、核心队列深度、
 * 最老 queued age、worker 心跳、最近安全错误码与作业元数据列表。
 * 不渲染 prompt、姓名、家庭内容、token、provider 原始错误。
 *
 * 轮询纪律与 Agent 监控页一致：5s 周期、页面不可见暂停、卸载清理。
 * 重跑入口：后端合同已存在但需策略版本暴露后才能安全接线 → 显示明确
 * disabled 状态并记录依赖，不新增旁路写操作。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { apiStewardJobs, apiStewardStatus } from '@/api/steward'
import type { StewardJobMeta, StewardStatus } from '@/api/steward'
import { RequestAbortedError } from '@/api/client'

const POLL_INTERVAL_MS = 5000

const status = ref<StewardStatus | null>(null)
const jobs = ref<StewardJobMeta[] | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const lastRefreshedAt = ref<number | null>(null)

let pollTimer: number | null = null
let inFlightController: AbortController | null = null

const STATE_LABELS: Record<string, string> = {
  disabled: '已停用',
  paused: '已暂停',
  running: '运行中',
  degraded: '降级（有失败作业）',
}

const STATE_TAG_CLASS: Record<string, string> = {
  disabled: '',
  paused: 'ag-tag-warning',
  running: 'ag-tag-healthy',
  degraded: 'ag-tag-anomaly',
}

const QUEUE_KEYS = ['queued', 'leased', 'running'] as const
const QUEUE_LABELS: Record<string, string> = {
  queued: '排队中',
  leased: '已租约',
  running: '执行中',
}

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  leased: '已租约',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
}

function formatAge(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h`
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

async function loadOnce(): Promise<void> {
  inFlightController?.abort()
  const controller = new AbortController()
  inFlightController = controller
  try {
    const [statusOut, jobsOut] = await Promise.all([
      apiStewardStatus(controller.signal),
      apiStewardJobs({ pageSize: 20, signal: controller.signal }),
    ])
    if (controller.signal.aborted) return
    status.value = statusOut
    jobs.value = jobsOut.items
    state.value = 'ready'
    lastRefreshedAt.value = Date.now()
  } catch (error) {
    if (error instanceof RequestAbortedError) return
    state.value = 'error'
  } finally {
    if (inFlightController === controller) inFlightController = null
  }
}

function tick(): void {
  if (document.visibilityState !== 'visible') return
  void loadOnce()
}

function startPolling(): void {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(tick, POLL_INTERVAL_MS)
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
  inFlightController?.abort()
  inFlightController = null
}

function onVisibilityChange(): void {
  if (document.visibilityState === 'visible') void loadOnce()
}

onMounted(() => {
  void loadOnce()
  startPolling()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onBeforeUnmount(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

// jobs 追加轮询由 loadOnce 统一承担；status 变化无需额外请求
watch(state, (value) => {
  if (value === 'error') startPolling()
})
</script>

<template>
  <section class="ag-card" aria-labelledby="steward-ops-title" data-testid="steward-ops">
    <h2 id="steward-ops-title" class="ag-page-title ag-heading-sm">Steward 管家引擎</h2>
    <p class="ag-page-subtitle">
      运维元数据只读视图（状态/队列/心跳/安全错误码），每 5 秒刷新；不含任何家庭内容。
    </p>

    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无观测数据" @retry="loadOnce" />

    <template v-else-if="status">
      <div class="steward-head">
        <span class="ag-tag" :class="STATE_TAG_CLASS[status.state]" data-testid="steward-state">
          {{ STATE_LABELS[status.state] ?? status.state }}
        </span>
        <span v-if="lastRefreshedAt" class="steward-refreshed">
          上次刷新：{{ new Date(lastRefreshedAt).toLocaleTimeString() }}
        </span>
      </div>

      <div class="ag-metric-grid" data-testid="steward-metrics">
        <div v-for="key in QUEUE_KEYS" :key="key" class="ag-metric">
          <div class="ag-metric-value">{{ status.queue_counts[key] ?? 0 }}</div>
          <div class="ag-metric-label">{{ QUEUE_LABELS[key] }}作业</div>
        </div>
        <div class="ag-metric">
          <div class="ag-metric-value">{{ formatAge(status.oldest_queued_seconds) }}</div>
          <div class="ag-metric-label">最老排队 age</div>
        </div>
        <div class="ag-metric">
          <div class="ag-metric-value">{{ status.worker_heartbeat_at ? '正常' : '—' }}</div>
          <div class="ag-metric-label">Worker 心跳：{{ formatTime(status.worker_heartbeat_at) }}</div>
        </div>
      </div>

      <h3 class="ag-heading-section">有效开关</h3>
      <ul class="steward-switches" data-testid="steward-switches">
        <li v-for="sw in status.switches" :key="sw.key">
          <span class="ag-tag" :class="sw.enabled ? 'ag-tag-healthy' : ''">
            {{ sw.enabled ? '开' : '关' }}
          </span>
          <strong>{{ sw.key }}</strong>
          <span class="steward-explain">{{ sw.explanation }}</span>
        </li>
      </ul>

      <h3 class="ag-heading-section">最近安全错误码</h3>
      <p v-if="status.recent_error_codes.length === 0" class="steward-empty">
        无失败作业错误码。
      </p>
      <ul v-else class="steward-error-codes" data-testid="steward-error-codes">
        <li v-for="code in status.recent_error_codes" :key="code">
          <span class="ag-tag ag-tag-anomaly">{{ code }}</span>
        </li>
      </ul>

      <h3 class="ag-heading-section">作业（最近 20 条）</h3>
      <p v-if="!jobs || jobs.length === 0" class="steward-empty">暂无 Steward 作业。</p>
      <div v-else class="ag-table-wrap">
        <table class="ag-table" data-testid="steward-jobs-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Space</th>
              <th>来源</th>
              <th>状态</th>
              <th>尝试</th>
              <th>错误码</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="job in jobs" :key="job.job_id">
              <td>#{{ job.job_id }}</td>
              <td>{{ job.space_id }}</td>
              <td>{{ job.cause }}</td>
              <td>
                <span class="ag-tag" :class="job.status === 'failed' ? 'ag-tag-anomaly' : ''">
                  {{ JOB_STATUS_LABELS[job.status] ?? job.status }}
                </span>
              </td>
              <td>{{ job.attempt }}</td>
              <td>{{ job.error_code ?? '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="steward-rerun-note" data-testid="steward-rerun-disabled">
        <button type="button" class="ag-btn-primary" disabled aria-describedby="steward-rerun-hint">
          重跑当前空间作业
        </button>
        <span id="steward-rerun-hint">
          受控重跑入口待接线：后端合同已具备，需先在观测 DTO 暴露现行策略版本后再开放
          （避免误触发 409 策略冲突）。
        </span>
      </p>
    </template>
  </section>
</template>

<style scoped>
.steward-head {
  display: flex;
  align-items: center;
  gap: var(--brand-space-3);
  margin-bottom: var(--brand-space-3);
}

.steward-refreshed {
  color: var(--ag-text-secondary);
  font-size: 12px;
}

.steward-switches {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.steward-switches li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.steward-explain {
  color: var(--ag-text-secondary);
  font-size: 12px;
}

.steward-error-codes {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.steward-empty {
  color: var(--ag-text-secondary);
  font-size: 13px;
}

.steward-rerun-note {
  margin: var(--brand-space-4) 0 0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.steward-rerun-note span {
  color: var(--ag-text-secondary);
  font-size: 12px;
}
</style>
