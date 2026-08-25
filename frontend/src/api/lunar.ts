import { apiClient } from './client'

/** 公农历互转预览（m3b；后端 lunar-python 单一实现） */
export async function fetchLunarMirror(
  calType: 'solar' | 'lunar',
  date: string,
): Promise<string | null> {
  const { data } = await apiClient.get<{ mirror: string | null }>('/lunar/mirror', {
    params: { cal_type: calType, date },
  })
  return data.mirror
}
