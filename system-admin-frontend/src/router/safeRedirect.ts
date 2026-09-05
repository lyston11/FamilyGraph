/**
 * redirect 白名单：只接受当前 admin SPA 的内部路径。
 * 绝不接受家庭 URL、绝对外部 URL、协议相对路径（//host）或任意 host。
 */

export function resolveSafeRedirect(raw: unknown): string | null {
  if (typeof raw !== 'string' || raw === '') return null
  if (!raw.startsWith('/')) return null
  if (raw.startsWith('//')) return null
  // 协议/主机注入（https:、javascript: 等在 path 中不会出现，但防御性排除）
  if (raw.includes('://')) return null
  // 登录页/强制改密页自身不作为落点，避免回环
  if (raw.startsWith('/login') || raw.startsWith('/force-change-password')) return null
  return raw
}
