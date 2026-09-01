import type { Maskable, PersonalFamilyViewStatus, VisibilityLevel } from '@/types/api'
import { isMasked } from '@/types/api'

/**
 * API 层共享运行时解码原语（type-safety.md：unknown → 共享类型的收窄守卫）。
 *
 * 仅供新增合同模块（household/notifications/spaceStats）复用；
 * personalFamilyView.ts 的同义私有守卫保持独立，不在本次范围内重构。
 * 语义与该文件保持一致：坏字段丢弃、未知枚举丢弃、fail-closed。
 */

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

export function isOneOf<T extends string>(options: readonly T[]) {
  return (value: unknown): value is T =>
    typeof value === 'string' && (options as readonly string[]).includes(value)
}

/**
 * 遮罩字段解码：命中哨兵即保留哨兵，否则按明文守卫校验。
 * 返回 undefined 表示字段非法，由调用方决定丢弃层级（条目或整份载荷）。
 */
export function decodeMaskable<T>(
  value: unknown,
  isClear: (candidate: unknown) => candidate is T,
): Maskable<T> | undefined {
  if (isMasked(value)) return { __masked__: true }
  return isClear(value) ? value : undefined
}

/** 载荷中合法的可见性层级；`none` 由后端转 404，出现即视为脏数据 */
const AUTHORIZED_VISIBILITY_LEVELS: readonly Exclude<VisibilityLevel, 'none'>[] = [
  'self_private',
  'household_detail',
  'lineage_summary',
]

export const isAuthorizedVisibilityLevel = isOneOf(AUTHORIZED_VISIBILITY_LEVELS)

/** 视图状态机（PersonalFamilyView/SpaceStats 共用，architecture.md §11） */
const VIEW_STATUSES: readonly PersonalFamilyViewStatus[] = [
  'never_computed',
  'queued',
  'running',
  'current',
  'stale',
  'failed',
]

export const isViewStatus = isOneOf(VIEW_STATUSES)
