<script setup lang="ts">
/**
 * Agent 监控：runs/jobs 聚合只读视图。
 *
 * 轮询纪律（PRD FE-F6 / design §6）：
 * - 每 5 秒轮询一次 runs 与 jobs；
 * - document.visibilityState !== 'visible' 时暂停（可见性恢复立即刷新）；
 * - 组件卸载：清除 timer + 中止 in-flight 请求；
 * - 只渲染二次脱敏诊断（error_code/component/stack_location/summary）；
 *   prompt、消息、context、tool result、密钥、token 一律不可渲染。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiAgentJobs, apiAgentRuns } from '@/api/read'
import { RequestAbortedError } from '@/api/client'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import StewardOpsPanel from '@/components/StewardOpsPanel.vue'
import type {
  AdminAgentErrorOut,
  AdminAgentJobOut,
  AdminAgentRunOut,
  AdminPageOut,
} from '@/types/api'

const POLL_INTERVAL_MS = 5000

type AdminAgentRunStatus =
  | 'queued'
  | 'leased'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired'

const runs = ref<AdminPageOut<AdminAgentRunOut> | null>(null)
const jobs = ref<AdminPageOut<AdminAgentJobOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const runsPage = ref(1)
const jobsPage = ref(1)
const pageSize = 20
const statusFilter = ref<'' | AdminAgentRunStatus>('')
const lastRefreshedAt = ref<number | null>(null)

let pollTimer: number | null = null
let inFlightController: AbortController | null = null

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  leased: '已租约',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  expired: '已过期',
}

async function loadOnce(): Promise<void> {
  // 上一轮请求未结束则中止（卸载/重叠轮询的防重入）
  inFlightController?.abort()
  const controller = new AbortController()
  inFlightController = controller
  try {
    const [runsOut, jobsOut] = await Promise.all([
      apiAgentRuns({
        page: runsPage.value,
        pageSize,
        status: statusFilter.value || null,
        signal: controller.signal,
      }),
      apiAgentJobs({
        page: jobsPage.value,
        pageSize,
        status: statusFilter.value || null,
        signal: controller.signal,
      }),
    ])
    if (controller.signal.aborted) return
    runs.value = runsOut
    jobs.value = jobsOut
    state.value = 'ready'
    lastRefreshedAt.value = Date.now()
  } catch (error) {
    if (error instanceof RequestAbortedError) return
    state.value = 'error'
  } finally {
    if (inFlightController === controller) {
      inFlightController = null
    }
  }
}

function tick(): void {
  // 页面不可见时暂停轮询，避免后台监控制造持续负载
  if (document.visibilityState !== 'visible') return
  void loadOnce()
}

function clearPollTimer(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling(): void {
  // 只清 timer，不中止 in-flight 请求（否则会把首轮加载杀掉）
  clearPollTimer()
  pollTimer = window.setInterval(tick, POLL_INTERVAL_MS)
}

/** 卸载：清 timer 并中止 in-flight 请求。 */
function stopPolling(): void {
  clearPollTimer()
  inFlightController?.abort()
  inFlightController = null
}

function onVisibilityChange(): void {
  if (document.visibilityState === 'visible') {
    void loadOnce()
  }
}

watch([runsPage, jobsPage, statusFilter], () => void loadOnce())

onMounted(() => {
  void loadOnce()
  startPolling()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onBeforeUnmount(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

/** 组件层防线：诊断对象只允许四个安全字段。 */
function diagnosticLines(error: AdminAgentErrorOut | null): string[] {
  if (!error) return []
  const lines: string[] = []
  if (error.error_code) lines.push(`错误码：${error.error_code}`)
  if (error.component) lines.push(`组件：${error.component}`)
  if (error.stack_location) lines.push(`位置：${error.stack_location}`)
  if (error.summary) lines.push(`摘要：${error.summary}`)
  return lines
}
</script>

<template>
  <div>
    <h1 class="ag-page-title">Agent 监控</h1>
    <p class="ag-page-subtitle">
      运行/作业聚合状态，每 5 秒自动刷新（页面不可见时暂停）。
      <span v-if="lastRefreshedAt" data-testid="agent-last-refreshed">
        上次刷新：{{ new Date(lastRefreshedAt).toLocaleTimeString() }}
      </span>
    </p>

    <!-- Steward 管家引擎观测（R5）：安全白名单 DTO，独立轮询生命周期 -->
    <StewardOpsPanel />

    <div class="ag-toolbar">
      <label class="ag-filter-label">
        状态
        <select v-model="statusFilter" aria-label="按运行状态筛选" data-testid="agent-status">
          <option value="">全部</option>
          <option value="queued">排队中</option>
          <option value="leased">已租约</option>
          <option value="running">运行中</option>
          <option value="succeeded">成功</option>
          <option value="failed">失败</option>
          <option value="cancelled">已取消</option>
          <option value="expired">已过期</option>
        </select>
      </label>
    </div>

    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无运行数据" @retry="loadOnce" />
    <template v-else>
      <section class="ag-card" aria-labelledby="agent-runs-title">
        <h2 id="agent-runs-title" class="ag-page-title ag-heading-sm">Runs</h2>
        <div v-if="!runs || runs.items.length === 0">
          <PageState state="empty" empty-text="暂无 Agent 运行" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="agent-runs-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Session</th>
                  <th>Space</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>尝试</th>
                  <th>诊断</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="run in runs.items" :key="run.id">
                  <td>#{{ run.id }}</td>
                  <td>{{ run.session_id }}</td>
                  <td>{{ run.space_id }}</td>
                  <td>{{ run.kind }}</td>
                  <td>
                    <span
                      class="ag-tag"
                      :class="run.status === 'failed' ? 'ag-tag-anomaly' : ''"
                    >
                      {{ STATUS_LABELS[run.status] ?? run.status }}
                    </span>
                  </td>
                  <td>{{ run.attempt }}/{{ run.max_attempts }}</td>
                  <td data-testid="agent-run-diagnostics">
                    <template v-if="diagnosticLines(run.error).length > 0">
                      <div v-for="line in diagnosticLines(run.error)" :key="line">{{ line }}</div>
                    </template>
                    <span v-else-if="run.error_code">{{ run.error_code }}</span>
                    <span v-else>-</span>
                  </td>
                  <td>{{ run.updated_at }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <ListPagination
            :page="runs.page"
            :page-size="runs.page_size"
            :total="runs.total"
            @update:page="runsPage = $event"
          />
        </template>
      </section>

      <section class="ag-card" aria-labelledby="agent-jobs-title">
        <h2 id="agent-jobs-title" class="ag-page-title ag-heading-sm">Jobs</h2>
        <div v-if="!jobs || jobs.items.length === 0">
          <PageState state="empty" empty-text="暂无 Agent 作业" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="agent-jobs-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Run</th>
                  <th>Space</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>尝试</th>
                  <th>诊断</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="job in jobs.items" :key="job.id">
                  <td>#{{ job.id }}</td>
                  <td>{{ job.run_id }}</td>
                  <td>{{ job.space_id ?? '-' }}</td>
                  <td>{{ job.kind }}</td>
                  <td>
                    <span class="ag-tag" :class="job.status === 'failed' ? 'ag-tag-anomaly' : ''">
                      {{ STATUS_LABELS[job.status] ?? job.status }}
                    </span>
                  </td>
                  <td>{{ job.attempt }}/{{ job.max_attempts }}</td>
                  <td data-testid="agent-job-diagnostics">
                    <template v-if="diagnosticLines(job.error).length > 0">
                      <div v-for="line in diagnosticLines(job.error)" :key="line">{{ line }}</div>
                    </template>
                    <span v-else>-</span>
                  </td>
                  <td>{{ job.updated_at }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <ListPagination
            :page="jobs.page"
            :page-size="jobs.page_size"
            :total="jobs.total"
            @update:page="jobsPage = $event"
          />
        </template>
      </section>
    </template>
  </div>
</template>
