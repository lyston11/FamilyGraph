import { describe, expect, it } from 'vitest'

import { readFileSync } from 'node:fs'

/**
 * 卡片弹窗宽度约定契约（09-20）。
 *
 * naive 的 preset="card" 弹窗没有内置宽度上限：card preset 走 card 样式
 * （`.n-card { width: 100% }`，无 max-width），外层滚动容器也不限宽，于是
 * `.n-modal.n-card` 的 width 直接取到视口宽——宽屏上等于全屏。
 *
 * 仓库既有约定是在组件自己的**非 scoped** `<style>` 块里用 data-test 锚定根节点：
 *   [data-test='…'] { width: min(Npx, calc(100vw - 48px)); }
 * 必须非 scoped（弹窗 teleport 到 body，scoped 属性对不上）。
 *
 * 本测试穷举全部 card 弹窗，确保**每个**都有宽度声明——缺一个就会铺满视口
 * （09-20 的缺口正是 5 个弹窗漏了这条）。
 *
 * 注意：不得改用全局 `.n-modal.n-card { width: … }`。它的特异度 (0,2,0) 高于
 * `[data-test='…']` (0,1,0)，会把各弹窗挑选的取值一次性覆盖成同一个值
 * （实测：既有 360px 会被覆盖为 560px）。最后一个用例守住这一点。
 *
 * jsdom 无布局引擎，故这里断言源码契约；真实像素由运行实例实测（见任务
 * implement.md 的验证段）。
 */

// global.css 不能经 `?raw`/`?inline` 读取（vite 把 .css 交给样式管线，两者都返回
// 空串），也无法从 jsdom 的 styleSheets 拿到（vitest 不注入）。直接读源文件。
const GLOBAL_CSS = readFileSync(`${process.cwd()}/src/styles/global.css`, 'utf8')

const modules = import.meta.glob('../../**/*.vue', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

interface DialogWidth {
  file: string
  dataTest: string
  width: string
}

/**
 * 解析组件里的非 scoped 宽度规则。
 *
 * 约定形如（可由多个 data-test 共享一条规则）：
 *   [data-test='a'], [data-test='b'] { width: min(420px, calc(100vw - 48px)); }
 * 所以按 CSS 规则粒度解析：先取出所有 `<style>` 块内的 `selector { body }`，
 * 再从中匹配 data-test。
 */
function parseWidthRules(source: string): Map<string, string> {
  const byTest = new Map<string, string>()
  for (const styleBlock of source.matchAll(/<style(?![^>]*scoped)[^>]*>([\s\S]*?)<\/style>/g)) {
    const css = styleBlock[1] ?? ''
    for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = rule[1] ?? ''
      const width = rule[2]?.match(/width:\s*([^;]+);/)?.[1]?.trim()
      if (width === undefined) continue
      for (const anchor of selector.matchAll(/\[data-test='([^']+)'\]/g)) {
        byTest.set(anchor[1]!, width)
      }
    }
  }
  return byTest
}

/** 抽出该文件里每个 preset="card" 的 NModal 块及其宽度声明。 */
function collectCardDialogs(): DialogWidth[] {
  const found: DialogWidth[] = []
  for (const [path, source] of Object.entries(modules)) {
    const file = path.split('/').pop() ?? path
    const rules = parseWidthRules(source)
    for (const match of source.matchAll(/<NModal\b[^>]*>/g)) {
      const tag = match[0]
      if (!tag.includes('preset="card"')) continue
      const dataTest = tag.match(/data-test="([^"]+)"/)?.[1] ?? '(无 data-test)'
      // 宽度可写在组件底部非 scoped style 规则，或 NModal 的内联 :style
      const inline = tag.match(/maxWidth:\s*'([^']+)'/)?.[1]
      found.push({ file, dataTest, width: rules.get(dataTest) ?? inline ?? '' })
    }
  }
  return found
}

describe('卡片弹窗宽度约定（09-20）', () => {
  const dialogs = collectCardDialogs()

  it('能枚举到全部 card 弹窗（扫描本身有效）', () => {
    expect(dialogs.length).toBeGreaterThanOrEqual(18)
  })

  it('每个 card 弹窗都有宽度声明（缺失即铺满视口）', () => {
    const missing = dialogs.filter((d) => d.width === '').map((d) => `${d.file} (${d.dataTest})`)
    expect(missing).toEqual([])
  })

  it('补齐的 5 个弹窗取既定值', () => {
    const byTest = new Map(dialogs.map((d) => [d.dataTest, d.width]))
    expect(byTest.get('family-space-join-dialog')).toBe('min(520px, calc(100vw - 48px))')
    expect(byTest.get('memory-editor-dialog')).toBe('min(520px, calc(100vw - 48px))')
    expect(byTest.get('confirm-memory-dialog')).toBe('min(460px, calc(100vw - 48px))')
    expect(byTest.get('execute-confirm-dialog')).toBe('min(420px, calc(100vw - 48px))')
    // 原为内联 maxWidth: 520px，收敛为同形 CSS 约定（取值不变）
    expect(byTest.get('suggestion-dialog')).toBe('min(520px, calc(100vw - 48px))')
  })

  it('既有弹窗的取值未被改动（抽查各量级）', () => {
    const byTest = new Map(dialogs.map((d) => [d.dataTest, d.width]))
    expect(byTest.get('kinship-correction-dialog')).toBe('min(360px, calc(100vw - 48px))')
    expect(byTest.get('invite-dialog')).toBe('min(380px, calc(100vw - 48px))')
    expect(byTest.get('binding-confirm-dialog')).toBe('min(400px, calc(100vw - 48px))')
    expect(byTest.get('one-time-pin-dialog')).toBe('min(420px, calc(100vw - 48px))')
    expect(byTest.get('space-create-dialog')).toBe('min(460px, calc(100vw - 48px))')
    expect(byTest.get('wizard-dialog')).toBe('min(520px, calc(100vw - 48px))')
    expect(byTest.get('space-governance-dialog')).toBe('min(700px, calc(100vw - 48px))')
  })

  it('窄屏一律走 calc(100vw - 48px)，不溢出 375px 视口', () => {
    const wrong = dialogs
      .filter((d) => d.width !== '' && !d.width.includes('calc(100vw - 48px)'))
      .map((d) => `${d.file} (${d.dataTest}): ${d.width}`)
    expect(wrong).toEqual([])
    // 375 - 48 = 327，仍在视口内
    expect(375 - 48).toBe(327)
  })

  it('不得引入全局 .n-modal 宽度规则（特异度会覆盖各弹窗取值）', () => {
    expect(GLOBAL_CSS).not.toMatch(/\.n-modal[^,{]*\{[^}]*width:/)
  })
})
