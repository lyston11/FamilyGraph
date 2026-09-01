/**
 * 关系说明安全文案（09-01 design.md §5.3，RelationshipDetailPanel 与
 * PersonProfileView 共用，单一来源避免两处漂移）：
 *
 * - 只对服务端返回的安全枚举码做文字化（path_class / inclusion_reason_code），
 *   不展示私有记忆、隐藏节点、其他空间事实或模型自由文本；
 * - 未知码回退到通用安全文案，不把原始码含义渲染成断言。
 */

/** path_class 服务端枚举的安全中文摘要（不含授权细节） */
export const PATH_CLASS_LABELS: Record<string, string> = {
  self: '本人',
  none: '无确认路径',
  direct_line: '直系血亲',
  collateral: '旁系血亲',
  affinal: '姻亲',
  step_adoptive: '继亲或养亲',
  guardian: '监护',
  cross_space: '跨家族空间桥接',
}

/** path_class → 安全文案；未知码回退通用描述 */
export function pathClassLabel(pathClass: string): string {
  return PATH_CLASS_LABELS[pathClass] ?? '关系类型说明暂缺'
}

/**
 * 边的事实状态（inclusion_reason_code）安全文案。当前服务端边只有
 * confirmed_path；未知码一律回退「服务端确认的关系事实」，不渲染原始码。
 */
export function factStateLabel(inclusionReasonCode: string): string {
  if (inclusionReasonCode === 'confirmed_path') return '已确认的关系事实'
  return '服务端确认的关系事实'
}
