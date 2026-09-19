import { describe, expect, it } from 'vitest'

import { CLIENT_AGENT_ERRORS, friendlyAgentError } from '@/api/agent'

/**
 * AGENT_ERROR_COPY / friendlyAgentError 文案映射（09-06 治理迁移，design §6.4）：
 * - PROVIDER_UNRESOLVED 依据 detail 两态细分：平台通道未配置 vs 空间未选/未同意云；
 * - 无 detail（SSE run.failed 路径）落基线第三句（去模型设置选择模型）；
 * - 其余错误码与客户端合成码映射不变。
 */

describe('friendlyAgentError：PROVIDER_UNRESOLVED 两态细分', () => {
  it('detail.platform_default_configured=false → 联系平台管理员（通道未配置）', () => {
    expect(
      friendlyAgentError('PROVIDER_UNRESOLVED', undefined, {
        policy_result: 'denied',
        reason: 'no_space_setting',
        platform_default_configured: false,
      }),
    ).toBe('助手模型尚未由平台管理员配置，请联系平台管理员')
  })

  it('detail.reason=cloud_not_allowed（默认已配置）→ 去模型设置开启云同意', () => {
    expect(
      friendlyAgentError('PROVIDER_UNRESOLVED', undefined, {
        policy_result: 'denied_cloud_forbidden',
        reason: 'cloud_not_allowed',
        platform_default_configured: true,
      }),
    ).toBe('该模型需要云端执行同意，请到 空间管理 → 模型设置 开启')
  })

  it('其余 PROVIDER_UNRESOLVED（含无 detail 的 SSE 路径）→ 去模型设置选择模型', () => {
    expect(friendlyAgentError('PROVIDER_UNRESOLVED')).toBe('请到 空间管理 → 模型设置 选择模型')
    expect(
      friendlyAgentError('PROVIDER_UNRESOLVED', undefined, {
        policy_result: 'denied',
        reason: 'setting_disabled',
      }),
    ).toBe('请到 空间管理 → 模型设置 选择模型')
  })

  it('detail 非 object（SSE 载荷异常形态）同样落基线文案', () => {
    expect(friendlyAgentError('PROVIDER_UNRESOLVED', undefined, 'unexpected')).toBe(
      '请到 空间管理 → 模型设置 选择模型',
    )
  })
})

describe('friendlyAgentError：其余映射不变', () => {
  it('AGENT_ERROR_COPY 其他码照旧映射', () => {
    expect(friendlyAgentError('AGENT_RUNTIME_DISABLED')).toBe('助手功能当前未启用')
    expect(friendlyAgentError('PROVIDER_DENIED_NO_LOCAL')).toBe(
      '该空间要求本地模型执行，但本地服务暂不可用',
    )
  })

  it('sidecar 运行期错误码（09-06 实测补齐）：provider 出网失败与策略拦截', () => {
    expect(friendlyAgentError('PROVIDER_STREAM_ERROR')).toBe('模型服务暂时不可用，请稍后重试')
    expect(friendlyAgentError('POLICY_PROVIDER_BLOCKED')).toBe(
      '当前模型与空间的安全策略不匹配，请联系空间所有者调整模型设置',
    )
    expect(friendlyAgentError('POLICY_TOOL_RESULT_BLOCKED')).toBe(
      '回答涉及的某些内容被安全策略拦截，请换个问法',
    )
  })

  it('撤权收敛码（09-19）说明是权限变化，不是服务故障', () => {
    expect(friendlyAgentError('AGENT_MEMBERSHIP_REVOKED')).toBe(
      '你已不是该空间的活跃成员，本次回答已停止',
    )
    // 不得回退成「稍后重试」——重试不可能成功（授权是永久失效）。
    expect(friendlyAgentError('AGENT_MEMBERSHIP_REVOKED')).not.toContain('重试')
    expect(friendlyAgentError('AGENT_MEMBERSHIP_REVOKED')).not.toBe('操作失败，请稍后重试')
  })

  it('sidecar 运行期错误码：空最终回答给出中文解释而非通用兜底', () => {
    expect(friendlyAgentError('PROVIDER_EMPTY_ANSWER')).toBe('模型没有返回内容，请重试或换个问法')
    expect(friendlyAgentError('PROVIDER_EMPTY_ANSWER')).not.toBe('操作失败，请稍后重试')
  })

  it('客户端合成错误码映射；未知码回退 fallback 或通用文案', () => {
    expect(friendlyAgentError(CLIENT_AGENT_ERRORS.STREAM_LOST)).toBe(
      '连接中断，任务状态未知，请点击「重试」恢复',
    )
    expect(friendlyAgentError('UNKNOWN_CODE', '自定义兜底')).toBe('自定义兜底')
    expect(friendlyAgentError(null)).toBe('操作失败，请稍后重试')
  })

  it('detail 不透传给用户（只映射文案，不拼接原始 JSON）', () => {
    const text = friendlyAgentError('PROVIDER_UNRESOLVED', undefined, {
      policy_result: 'denied',
      reason: 'no_space_setting',
      platform_default_configured: false,
    })
    expect(text).not.toContain('platform_default_configured')
    expect(text).not.toContain('{')
  })
})
