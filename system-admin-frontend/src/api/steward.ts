/**
 * Steward 运维观测 API（R5，09-11 整改）。
 *
 * 只消费后端 /admin-api/v1/steward/* 已有合同（app/api/admin_steward.py），
 * 字段为后端白名单 DTO：状态、开关解释、队列计数、worker 心跳、最近安全
 * 错误码与作业元数据（job_id/space_id/cause/status/attempt/error_code）。
 * 响应天然不含 prompt、家庭内容、token 与 provider 原始响应；
 * decoder fail-closed：字段缺失/类型不符抛 ContractViolationError。
 *
 * 重跑入口：后端 POST /steward/spaces/{id}/rerun 合同已存在，但
 * expected_policy_version 需要后端在 status DTO 暴露现行策略版本后才能安全
 * 接线（依赖记录于任务 notes）；本模块暂不提供重跑调用，前端显示明确
 * disabled 状态，不新增旁路写操作。
 */
import { adminRequest } from '@/api/client'
import {
  expectArray,
  expectBoolean,
  expectLiteral,
  expectNumber,
  expectNumberOrNull,
  expectObject,
  expectString,
  expectStringArray,
  expectStringOrNull,
} from '@/api/decode'

export type StewardState = 'disabled' | 'paused' | 'running' | 'degraded'

export interface StewardSwitchState {
  key: string
  enabled: boolean
  explanation: string
}

export interface StewardStatus {
  state: StewardState
  switches: StewardSwitchState[]
  worker_heartbeat_at: string | null
  queue_counts: Record<string, number>
  oldest_queued_seconds: number | null
  recent_error_codes: string[]
}

export interface StewardJobMeta {
  job_id: number
  space_id: number
  cause: string
  status: string
  attempt: number
  available_at: string | null
  error_code: string | null
}

export interface StewardJobsPage {
  items: StewardJobMeta[]
  page: number
  page_size: number
}

const STEWARD_STATES: readonly StewardState[] = ['disabled', 'paused', 'running', 'degraded']

function decodeSwitch(raw: unknown): StewardSwitchState {
  const obj = expectObject(raw, 'steward switch')
  return {
    key: expectString(obj['key'], 'key'),
    enabled: expectBoolean(obj['enabled'], 'enabled'),
    explanation: expectString(obj['explanation'], 'explanation'),
  }
}

function decodeStewardStatus(raw: unknown): StewardStatus {
  const obj = expectObject(raw, 'steward status')
  const countsRaw = expectObject(obj['queue_counts'], 'queue_counts')
  const queueCounts: Record<string, number> = {}
  for (const [key, value] of Object.entries(countsRaw)) {
    queueCounts[key] = expectNumber(value, `queue_counts.${key}`)
  }
  return {
    state: expectLiteral(obj['state'], STEWARD_STATES, 'state'),
    switches: expectArray(obj['switches'], 'switches').map(decodeSwitch),
    worker_heartbeat_at: expectStringOrNull(obj['worker_heartbeat_at'], 'worker_heartbeat_at'),
    queue_counts: queueCounts,
    oldest_queued_seconds: expectNumberOrNull(obj['oldest_queued_seconds'], 'oldest_queued_seconds'),
    recent_error_codes: expectStringArray(obj['recent_error_codes'], 'recent_error_codes'),
  }
}

function decodeStewardJob(raw: unknown): StewardJobMeta {
  const obj = expectObject(raw, 'steward job')
  return {
    job_id: expectNumber(obj['job_id'], 'job_id'),
    space_id: expectNumber(obj['space_id'], 'space_id'),
    cause: expectString(obj['cause'], 'cause'),
    status: expectString(obj['status'], 'status'),
    attempt: expectNumber(obj['attempt'], 'attempt'),
    available_at: expectStringOrNull(obj['available_at'], 'available_at'),
    error_code: expectStringOrNull(obj['error_code'], 'error_code'),
  }
}

export function apiStewardStatus(signal?: AbortSignal): Promise<StewardStatus> {
  return adminRequest<unknown>({ method: 'get', url: '/v1/steward/status', signal }).then(
    decodeStewardStatus,
  )
}

export function apiStewardJobs(
  query: { page?: number; pageSize?: number; status?: string | null; signal?: AbortSignal },
): Promise<StewardJobsPage> {
  return adminRequest<unknown>({
    method: 'get',
    url: '/v1/steward/jobs',
    params: {
      page: query.page ?? 1,
      page_size: query.pageSize ?? 20,
      status: query.status || null,
    },
    signal: query.signal,
  }).then((raw) => {
    const obj = expectObject(raw, 'steward jobs page')
    return {
      items: expectArray(obj['items'], 'items').map(decodeStewardJob),
      page: expectNumber(obj['page'], 'page'),
      page_size: expectNumber(obj['page_size'], 'page_size'),
    }
  })
}
