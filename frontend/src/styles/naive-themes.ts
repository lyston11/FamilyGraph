import type { GlobalThemeOverrides } from 'naive-ui'

import { themeTokens, type ThemeName } from './tokens'

/**
 * Naive UI themeOverrides（design.md §2.1）：由 tokens.ts 的 L2 语义 token 派生，
 * 本文件不出现第二份手写色值。两主题均为浅色——n-config-provider 保持 naive
 * 默认 light 主题不切换，仅注入 overrides（App.vue）。
 */
function buildOverrides(name: ThemeName): GlobalThemeOverrides {
  const { vars } = themeTokens[name]
  return {
    common: {
      primaryColor: vars['accent'],
      primaryColorHover: vars['accent-hover'],
      primaryColorPressed: vars['accent-pressed'],
      primaryColorSuppl: vars['accent-hover'],
      // 表面
      bodyColor: vars['surface'],
      cardColor: vars['surface-raised'],
      modalColor: vars['surface-raised'],
      popoverColor: vars['surface-raised'],
      tableColor: vars['surface-raised'],
      inputColor: vars['surface-raised'],
      // 文字
      textColorBase: vars['ink'],
      textColor1: vars['ink'],
      textColor2: vars['ink-secondary'],
      textColor3: vars['ink-faint'],
      placeholderColor: vars['ink-faint'],
      // 线与圆角
      borderColor: vars['line'],
      dividerColor: vars['line'],
      borderRadius: vars['radius-control'],
      borderRadiusSmall: vars['radius-control'],
    },
  }
}

export const paperThemeOverrides: GlobalThemeOverrides = buildOverrides('paper')

export const modernThemeOverrides: GlobalThemeOverrides = buildOverrides('modern')

export const themeOverrides: Record<ThemeName, GlobalThemeOverrides> = {
  paper: paperThemeOverrides,
  modern: modernThemeOverrides,
}
