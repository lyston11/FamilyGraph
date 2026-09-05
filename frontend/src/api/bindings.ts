import type { Binding } from '@/types/api'

import { apiClient } from './client'

/**
 * 绑定请求 client（09-05 Chunk D 合同；字段与 backend/app/schemas/binding.py 一一对应）。
 * 被绑定人视角：确认前只披露发起人显示名与建档人物名（决策 16 隐私边界）。
 * 错误：404 绑定请求不存在或非本人（防枚举）/ 409 终态不可再处理 / 401 PIN 复验统一文案。
 */

/** 我的绑定请求（被绑定人视角；含已决议历史，UI 按 status 处置） */
export async function fetchMyBindings(): Promise<Binding[]> {
  const { data } = await apiClient.get<Binding[]>('/bindings')
  return data
}

/** 「这是我」确认：被绑定人本人 + PIN 复验（后端经 identity_fsm 唯一转换点确档） */
export async function confirmBinding(bindingId: number, pin: string): Promise<Binding> {
  const { data } = await apiClient.post<Binding>(`/bindings/${bindingId}/confirm`, { pin })
  return data
}

/** 被绑定人拒绝（终态）：撞名建档产生的人物随之废弃 */
export async function rejectBinding(bindingId: number): Promise<Binding> {
  const { data } = await apiClient.post<Binding>(`/bindings/${bindingId}/reject`)
  return data
}

/** 发起人取消 pending 绑定（终态）；仅发起人本人可操作 */
export async function cancelBinding(bindingId: number): Promise<Binding> {
  const { data } = await apiClient.delete<Binding>(`/bindings/${bindingId}`)
  return data
}
