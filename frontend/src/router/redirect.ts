/**
 * 只允许路由内部的绝对路径作为登录流程回跳地址。
 * 以 `/` 开头并不等于安全：`//host/path` 会被浏览器解释为跨域地址，反斜杠
 * 也可能在 URL 规范化时变成路径分隔符，因此两者都拒绝。
 */
export function getSafeInternalRedirect(value: unknown): string | undefined {
  if (
    typeof value !== 'string' ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.includes('\\')
  ) {
    return undefined
  }

  try {
    const parsed = new URL(value, window.location.origin)
    return parsed.origin === window.location.origin ? value : undefined
  } catch {
    return undefined
  }
}

/**
 * 系统管理员登录流程专用回跳白名单（SAR-F1）：只接受站内已知的
 * system-admin 路由，登录页不信任响应/查询参数中的任意 URL。
 * 系统主体不得进入家庭壳，因此家庭路由不在白名单内。
 */
const SYSTEM_ADMIN_REDIRECT_PATHS: ReadonlySet<string> = new Set(['/system-admin'])

export function getSafeSystemAdminRedirect(value: unknown): string | undefined {
  const internal = getSafeInternalRedirect(value)
  return internal !== undefined && SYSTEM_ADMIN_REDIRECT_PATHS.has(internal)
    ? internal
    : undefined
}
