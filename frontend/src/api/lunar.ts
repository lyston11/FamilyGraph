import { apiClient } from './client'

/** 公农历互转预览（m3b；后端 lunar-python 单一实现） */
export interface LunarMirror {
  mirror: string | null
  /** 闰月标记，恒描述农历那一侧 */
  is_leap_month: boolean
}

export async function fetchLunarMirror(
  calType: 'solar' | 'lunar',
  date: string,
  isLeapMonth = false,
): Promise<LunarMirror> {
  const { data } = await apiClient.get<LunarMirror>('/lunar/mirror', {
    params: { cal_type: calType, date, is_leap_month: isLeapMonth },
  })
  return data
}
