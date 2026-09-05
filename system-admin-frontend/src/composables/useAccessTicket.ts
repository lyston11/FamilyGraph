/**
 * 敏感详情访问票据流程（design §5）：
 * 1. 无有效票据 → 弹理由表单；
 * 2. 提交理由 → POST /v1/access-sessions（store 只存内存票据）；
 * 3. 详情请求带 X-Admin-Access-Session；
 * 4. 403 票据失效 → 清内存票据 → 引导重新提交理由。
 */
import { ref } from 'vue'
import { useAdminAccessSessionStore } from '@/stores/accessSession'
import type { AccessTargetType } from '@/types/api'

export function useAccessTicket(targetType: AccessTargetType, targetId: () => number) {
  const access = useAdminAccessSessionStore()
  const requesting = ref(false)
  const ticketError = ref<string | null>(null)

  function hasValidTicket(): boolean {
    return access.peekValid(targetType, targetId()) !== null
  }

  async function authorize(reason: string): Promise<boolean> {
    requesting.value = true
    ticketError.value = null
    try {
      await access.ensureTicket(targetType, targetId(), reason)
      return true
    } catch {
      ticketError.value = '访问授权申请失败，请稍后重试'
      return false
    } finally {
      requesting.value = false
    }
  }

  /** 详情请求遇到 403（过期/错目标/撤销）：清票据并要求重新授权。 */
  function handleRejected(): void {
    access.revoke(targetType, targetId())
    ticketError.value = '访问授权已失效，请重新提交理由'
  }

  function clearError(): void {
    ticketError.value = null
  }

  return { requesting, ticketError, hasValidTicket, authorize, handleRejected, clearError }
}
