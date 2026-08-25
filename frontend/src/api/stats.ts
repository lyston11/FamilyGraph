import { apiClient } from './client'

export interface StatsPayload {
  total: number
  by_gender: { m: number; f: number; unknown: number }
  generation_histogram: { bucket: number; count: number }[]
  birthdays_this_month: { id: number; name: string; date: string }[]
}

export async function fetchStats(): Promise<StatsPayload> {
  const { data } = await apiClient.get<StatsPayload>('/stats')
  return data
}

export interface SearchHit {
  id: number
  name: string
  level: 'full' | 'summary'
}

export async function search(q: string): Promise<SearchHit[]> {
  const { data } = await apiClient.get<SearchHit[]>('/search', { params: { q } })
  return data
}
