/**
 * 密码强度前端校验：与后端 admin_auth._validate_password_strength 对齐
 * （≥12 位且含小写/大写/数字）。仅作提前反馈，最终以后端为准。
 */
export function validateAdminPasswordStrength(password: string): string | null {
  if (password.length < 12) return '密码至少 12 位'
  if (!/[a-z]/.test(password)) return '密码需包含小写字母'
  if (!/[A-Z]/.test(password)) return '密码需包含大写字母'
  if (!/[0-9]/.test(password)) return '密码需包含数字'
  return null
}
