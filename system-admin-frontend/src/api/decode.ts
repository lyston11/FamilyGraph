/**
 * 轻量运行时解码原语（type-safety spec：api 层对响应做轻量校验，
 * 字段缺失/结构不符时抛可观测错误，而不是让 undefined 流入组件）。
 *
 * 后端 schema 是 extra="forbid" 白名单；这里做"字段存在且类型正确"的
 * 单向校验（多余字段后端本就不发，不做黑名单拒绝）。
 */

export class ContractViolationError extends Error {
  constructor(message: string) {
    super(`admin api contract violation: ${message}`)
    this.name = 'ContractViolationError'
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function expectObject(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ContractViolationError(`${label} is not an object`)
  }
  return value
}

export function expectString(value: unknown, label: string): string {
  if (typeof value !== 'string') {
    throw new ContractViolationError(`${label} is not a string`)
  }
  return value
}

export function expectNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new ContractViolationError(`${label} is not a finite number`)
  }
  return value
}

export function expectBoolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') {
    throw new ContractViolationError(`${label} is not a boolean`)
  }
  return value
}

export function expectStringOrNull(value: unknown, label: string): string | null {
  if (value === null) return null
  return expectString(value, label)
}

export function expectNumberOrNull(value: unknown, label: string): number | null {
  if (value === null) return null
  return expectNumber(value, label)
}

export function expectLiteral<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T {
  const raw = expectString(value, label)
  const hit = allowed.find((item) => item === raw)
  if (hit === undefined) {
    throw new ContractViolationError(`${label} unexpected value: ${raw}`)
  }
  return hit
}

export function expectLiteralOrNull<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T | null {
  if (value === null) return null
  return expectLiteral(value, allowed, label)
}

export function expectArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new ContractViolationError(`${label} is not an array`)
  }
  return value
}

export function expectStringArray(value: unknown, label: string): string[] {
  return expectArray(value, label).map((item, index) => expectString(item, `${label}[${index}]`))
}
