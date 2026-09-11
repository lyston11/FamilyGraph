import { describe, expect, it } from 'vitest'

import memoryManagerSource from '@/components/memory/MemoryManager.vue?raw'
import dataRightsSource from '@/components/member/DataRightsPanel.vue?raw'
import disclosureSource from '@/components/member/DisclosureMatrix.vue?raw'
import governanceSource from '@/components/member/SpaceGovernancePanel.vue?raw'
import attachmentsSource from '@/components/member/AttachmentsSection.vue?raw'
import memoryViewSource from '@/views/MemoryView.vue?raw'
import notificationsSource from '@/views/NotificationsView.vue?raw'
import personProfileSource from '@/views/PersonProfileView.vue?raw'
import settingsSource from '@/views/SettingsView.vue?raw'
import spaceManagementSource from '@/views/SpaceManagementView.vue?raw'
import statsSource from '@/views/StatsView.vue?raw'

/**
 * Phase 7：375px 响应式与视觉质量源级契约（jsdom 无布局引擎，断言源码中的
 * 断点/类名/结构而非像素）：
 * - 各页面 375px 单列化（媒体查询契约）与 44px 点按目标；
 * - 表格类内容经 NDataTable scroll-x 降级为「可横向滚动的独立区域」，页面本身
 *   不横向滚动；
 * - 质量门禁：无 v-html、无 element-plus、无硬编码色值（颜色一律 --fg-* token
 *   或 color-mix 派生）。
 */

const ALL_SOURCES = [
  ['memory/MemoryManager.vue', memoryManagerSource],
  ['member/DataRightsPanel.vue', dataRightsSource],
  ['member/DisclosureMatrix.vue', disclosureSource],
  ['member/SpaceGovernancePanel.vue', governanceSource],
  ['member/AttachmentsSection.vue', attachmentsSource],
  ['views/MemoryView.vue', memoryViewSource],
  ['views/NotificationsView.vue', notificationsSource],
  ['views/PersonProfileView.vue', personProfileSource],
  ['views/SettingsView.vue', settingsSource],
  ['views/SpaceManagementView.vue', spaceManagementSource],
  ['views/StatsView.vue', statsSource],
] as const

describe('Phase 7：375px 响应式源级契约', () => {
  it('记忆页与设置页同构框架：容器 1120px 居中 + 左侧分区导航 ≤768px 收敛为横向滑动标签条（44px 点按目标）', () => {
    // 页面容器与设置页同构（max-width 居中，不再是全宽 hero 大卡）
    expect(memoryViewSource).toMatch(/\.memory-view\s*\{[^}]*max-width: 1120px/)
    // 分区导航 ≤768px 变横向滑动标签条（与设置页 tabs 同断点），标签 ≥44px 点按目标
    expect(memoryManagerSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.memory-tabs\s*\{[\s\S]*?flex-direction: row;/,
    )
    expect(memoryManagerSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.memory-tabs\s*\{[\s\S]*?overflow-x: auto;/,
    )
    expect(memoryManagerSource).toMatch(/\.memory-tab\s*\{[^}]*min-height: 44px;/)
  })

  it('设置页：inline 表单窄屏纵向堆叠 + 主题预览卡单列', () => {
    expect(settingsSource).toMatch(
      /@media \(max-width: 600px\)[\s\S]*\.n-form--inline \.n-form-item[^{]*\{[\s\S]*?width: 100%;/,
    )
    expect(settingsSource).toMatch(
      /@media \(max-width: 480px\)[\s\S]*\.theme-cards\s*\{[\s\S]*?grid-template-columns: 1fr;/,
    )
  })

  it('统计页：摘要卡窄屏降级两列 + 动作按钮 44px；页面单列纵排', () => {
    // repeat(2, 1fr) 与 repeat(2, minmax(0, 1fr))（防长内容撑爆网格）均满足契约：
    // 摘要卡在 ≤600px 降级为两列。
    expect(statsSource).toMatch(
      /@media \(max-width: 600px\)[\s\S]*\.summary-cards[^{]*\{[\s\S]*?repeat\(2, (minmax\(0, )?1fr\)/,
    )
    expect(statsSource).toMatch(
      /@media \(max-width: 600px\)[\s\S]*\.n-button--small-type[^}]*min-height: 44px;/,
    )
    expect(statsSource).toMatch(/\.stats-view\s*\{[^}]*flex-direction: column;/)
  })

  it('通知页：三分区单列纵排 + 动作按钮 44px', () => {
    expect(notificationsSource).toMatch(/\.notifications-view\s*\{[^}]*flex-direction: column;/)
    expect(notificationsSource).toMatch(
      /@media \(max-width: 600px\)[\s\S]*\.n-button--small-type[^}]*min-height: 44px;/,
    )
  })

  it('空间管理页：≤768px 分区导航收敛为顶部水平标签条（可滑动）+ 分区按钮 44px', () => {
    expect(spaceManagementSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.management-nav\s*\{[\s\S]*?flex-direction: row;/,
    )
    expect(spaceManagementSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.management-nav\s*\{[\s\S]*?overflow-x: auto;/,
    )
    expect(spaceManagementSource).toMatch(/\.management-tab\s*\{[^}]*min-height: 44px;/)
  })

  it('个人公示页：单列纵排、返回按钮 44px、窄屏隐藏次要入口文案', () => {
    expect(personProfileSource).toMatch(/\.person-profile-view\s*\{[^}]*flex-direction: column;/)
    expect(personProfileSource).toMatch(/\.back-button\s*\{[^}]*min-height: 44px;/)
    expect(personProfileSource).toMatch(
      /@media \(max-width: 480px\)[\s\S]*\.relation-entry\s*\{[\s\S]*?display: none;/,
    )
  })

  it('表格类内容：披露矩阵/数据权利/成员表启用内部横向滚动（scroll-x），页面不横向滚动', () => {
    expect(disclosureSource).toContain(':scroll-x="tableScrollX"')
    expect(disclosureSource).toMatch(/const tableScrollX = computed\(/)
    expect(dataRightsSource).toContain(':scroll-x="640"')
    expect(governanceSource).toContain(':scroll-x="480"')
  })
})

describe('Phase 7：质量门禁源级检查（views/components）', () => {
  it.each(ALL_SOURCES)('%s：无 v-html、无 element-plus、无硬编码色值', (_name, source) => {
    expect(source).not.toContain('v-html')
    expect(source).not.toMatch(/element-plus|El[A-Z][a-z]|<el-|v-loading|--el-/)
    // 颜色红线：hex/rgb()/hsl() 一律不允许（颜色走 --fg-* token 或 color-mix 派生）
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(source).not.toMatch(/\brgb\(|\brgba\(|\bhsl\(/)
  })
})
