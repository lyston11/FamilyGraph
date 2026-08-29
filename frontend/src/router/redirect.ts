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
