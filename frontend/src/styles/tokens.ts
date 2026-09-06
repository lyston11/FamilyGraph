/**
 * 设计 token 单一来源（design.md §2.1，PRD R2/R3）。
 *
 * 分层：
 * - L1 palette / scale：原始色板与字号、间距阶（仅在本文件内派生 L2，业务代码不得直接取色）。
 * - L2 vars：语义 token，键为 CSS 变量名（注入时拼 `--fg-` 前缀），由 App.vue
 *   批量写入 documentElement——自绘样式与 naive-themes.ts 的 themeOverrides 同源。
 *
 * 两主题（暮色 paper / 雾青 modern）使用深色玻璃表面；保留已存储的主题键。
 */

export type ThemeName = 'paper' | 'modern'

/** L2 语义 token 键（不含 `--fg-` 前缀）。新增变量必须同时补齐两主题（FgVars 编译期强制）。 */
export const fgVarNames = [
  // 表面三阶：页面底 / 卡片浮层 / 下沉区（泳道、井）
  'surface',
  'surface-raised',
  'surface-sunken',
  // 墨色三阶：正文 / 次要 / 弱化
  'ink',
  'ink-secondary',
  'ink-faint',
  // 主强调：基态 / 悬停 / 按下 / 柔和底 / 其上文字
  'accent',
  'accent-hover',
  'accent-pressed',
  'accent-soft',
  'accent-ink',
  // 线：发丝线 / 强线
  'line',
  'line-strong',
  // 领域状态视觉语义（design.md §3.4，R6）
  'status-confirmed',
  'status-proposed',
  'status-disputed',
  'status-provisional',
  'status-masked',
  // 信息色（NAlert type="info" 等；naive-themes 从本变量派生 infoColor，
  // 避免 naive 默认蓝脱离色板。L1 来源：两主题 indigo 原始色）
  'info',
  // 背景点阵：点色 / 点距
  'dot',
  'dot-gap',
  // 标题字体栈（正文栈 --fg-font-body 为静态基座，见 tokens.css）
  'font-display',
  // 圆角：卡片 / 控件
  'radius-card',
  'radius-control',
  // 阴影：卡片静置 / 浮起
  'shadow-card',
  'shadow-raised',
  // 玻璃态与光晕（Cosmic Glass）
  'glass-surface',
  'glass-surface-raised',
  'glass-border',
  'glass-glow',
  // 家族树专用星空画布：深色底/节点对比/星点与光晕
  'canvas-surface',
  'canvas-surface-raised',
  'canvas-ink',
  'canvas-muted',
  'canvas-line',
  'canvas-star',
  'canvas-glow',
] as const

export type FgVarName = (typeof fgVarNames)[number]

/** L2 语义 token 集：键必须完整覆盖 fgVarNames */
export type FgVars = Record<FgVarName, string>

/** L1 原始 token：字号阶（px） */
export const fontSizeScale = {
  xs: 12,
  sm: 13,
  md: 14,
  lg: 16,
  xl: 20,
  xxl: 24,
} as const

/** L1 原始 token：间距阶（px） */
export const spacingScale = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const

/** 暮色主题原始色板；字段名保留兼容，业务层只消费语义变量。 */
export interface PaperPalette {
  paperBase: string
  paperRaised: string
  paperSunken: string
  paperDot: string
  ink: string
  inkSoft: string
  inkFaint: string
  vermilion: string
  vermilionHover: string
  vermilionPressed: string
  indigo: string
  moss: string
  ochre: string
  seal: string
  lineHairline: string
  lineStrong: string
}

/** 雾青主题原始色板。 */
export interface ModernPalette {
  whiteBase: string
  whiteRaised: string
  graySunken: string
  grayDot: string
  ink: string
  inkSoft: string
  inkFaint: string
  blue: string
  blueHover: string
  bluePressed: string
  cyan: string
  indigo: string
  amber: string
  red: string
  slate: string
  lineHairline: string
  lineStrong: string
}

export interface ThemeTokens {
  /** 主题标识（store 写入 `<html data-theme>`） */
  name: ThemeName
  /** 中文显示名（主题切换控件用） */
  label: string
  /** L1 原始色板（union：仅 tokens.ts 内派生与文档用途，消费方一律走 vars） */
  palette: PaperPalette | ModernPalette
  /** L2 语义 token：完整覆盖 fgVarNames，值从 palette 派生 */
  vars: FgVars
}

const paperPalette: PaperPalette = {
  paperBase: '#161b1c',
  paperRaised: '#273032',
  paperSunken: '#1b2426',
  paperDot: '#637676',
  ink: '#f0f1ed',
  inkSoft: '#bac5c2',
  inkFaint: '#9baaa5',
  vermilion: '#edb5aa',
  vermilionHover: '#f5ccc3',
  vermilionPressed: '#dba094',
  indigo: '#a7c7d5',
  moss: '#a3ccb3',
  ochre: '#e6c591',
  seal: '#adbcb5',
  lineHairline: '#3e4c4c',
  lineStrong: '#60736e',
}

const modernPalette: ModernPalette = {
  whiteBase: '#131c20',
  whiteRaised: '#243338',
  graySunken: '#1a272c',
  grayDot: '#617c87',
  ink: '#eef4f4',
  inkSoft: '#b6c8ce',
  inkFaint: '#99adb6',
  blue: '#a6d8d3',
  blueHover: '#c0e8e3',
  bluePressed: '#8fc4bd',
  cyan: '#a5d3bd',
  indigo: '#a8c8ed',
  amber: '#e5c48f',
  red: '#f3afa4',
  slate: '#a4b4bf',
  lineHairline: '#3d5058',
  lineStrong: '#607a86',
}

/** 暮色（默认）：石墨底、银灰玻璃、珊瑚强调色与宋体标题。 */
export const paperTokens: ThemeTokens = {
  name: 'paper',
  label: '暮色',
  palette: paperPalette,
  vars: {
    'surface': paperPalette.paperBase,
    'surface-raised': paperPalette.paperRaised,
    'surface-sunken': paperPalette.paperSunken,
    'ink': paperPalette.ink,
    'ink-secondary': paperPalette.inkSoft,
    'ink-faint': paperPalette.inkFaint,
    'accent': paperPalette.vermilion,
    'accent-hover': paperPalette.vermilionHover,
    'accent-pressed': paperPalette.vermilionPressed,
    'accent-soft': 'rgba(237, 181, 170, 0.1)',
    'accent-ink': paperPalette.paperRaised,
    'line': paperPalette.lineHairline,
    'line-strong': paperPalette.lineStrong,
    'status-confirmed': paperPalette.moss,
    'status-proposed': paperPalette.ochre,
    'status-disputed': paperPalette.vermilion,
    'status-provisional': paperPalette.inkFaint,
    'status-masked': paperPalette.seal,
    'info': paperPalette.indigo,
    'dot': paperPalette.paperDot,
    'dot-gap': '22px',
    'font-display':
      '"Songti SC", "Noto Serif CJK SC", "Noto Serif SC", STSong, SimSun, serif',
    'radius-card': '8px',
    'radius-control': '6px',
    'shadow-card': '0 8px 24px rgba(4, 9, 10, 0.2)',
    'shadow-raised': '0 24px 60px rgba(4, 9, 10, 0.4)',
    'glass-surface': 'rgba(51, 65, 65, 0.38)',
    'glass-surface-raised': 'rgba(31, 43, 45, 0.84)',
    'glass-border': 'rgba(203, 224, 216, 0.19)',
    'glass-glow': 'rgba(197, 223, 210, 0.08)',
    'canvas-surface': '#111819',
    'canvas-surface-raised': '#202c2b',
    'canvas-ink': '#f3f7ff',
    'canvas-muted': '#a8bad4',
    'canvas-line': 'rgba(168, 186, 216, 0.32)',
    'canvas-star': 'rgba(237, 245, 255, 0.78)',
    'canvas-glow': 'rgba(114, 164, 224, 0.28)',
  },
}

/** 雾青：中性深底、薄雾玻璃、薄荷强调色与无衬线标题。 */
export const modernTokens: ThemeTokens = {
  name: 'modern',
  label: '雾青',
  palette: modernPalette,
  vars: {
    'surface': modernPalette.whiteBase,
    'surface-raised': modernPalette.whiteRaised,
    'surface-sunken': modernPalette.graySunken,
    'ink': modernPalette.ink,
    'ink-secondary': modernPalette.inkSoft,
    'ink-faint': modernPalette.inkFaint,
    'accent': modernPalette.blue,
    'accent-hover': modernPalette.blueHover,
    'accent-pressed': modernPalette.bluePressed,
    'accent-soft': 'rgba(166, 216, 211, 0.1)',
    'accent-ink': modernPalette.whiteRaised,
    'line': modernPalette.lineHairline,
    'line-strong': modernPalette.lineStrong,
    'status-confirmed': modernPalette.cyan,
    'status-proposed': modernPalette.amber,
    'status-disputed': modernPalette.red,
    'status-provisional': modernPalette.inkFaint,
    'status-masked': modernPalette.slate,
    'info': modernPalette.indigo,
    'dot': modernPalette.grayDot,
    'dot-gap': '24px',
    // 无衬线标题：引用 tokens.css 的静态正文栈，避免第二份字体字面量
    'font-display': 'var(--fg-font-body)',
    'radius-card': '8px',
    'radius-control': '8px',
    'shadow-card': '0 8px 24px rgba(4, 9, 12, 0.2)',
    'shadow-raised': '0 24px 60px rgba(4, 9, 12, 0.4)',
    'glass-surface': 'rgba(46, 68, 76, 0.4)',
    'glass-surface-raised': 'rgba(28, 44, 51, 0.84)',
    'glass-border': 'rgba(193, 225, 235, 0.2)',
    'glass-glow': 'rgba(181, 224, 232, 0.09)',
    'canvas-surface': '#11171b',
    'canvas-surface-raised': '#202c32',
    'canvas-ink': '#f4f8ff',
    'canvas-muted': '#a9bfd7',
    'canvas-line': 'rgba(166, 198, 227, 0.34)',
    'canvas-star': 'rgba(237, 248, 255, 0.82)',
    'canvas-glow': 'rgba(68, 183, 190, 0.3)',
  },
}

export const themeTokens: Record<ThemeName, ThemeTokens> = {
  paper: paperTokens,
  modern: modernTokens,
}

/** 判定字符串是否为合法主题名（store 读 localStorage 时收窄用） */
export function isThemeName(value: string): value is ThemeName {
  return value in themeTokens
}

/** 展开主题 L2 token 为 CSS 变量条目（`--fg-` 前缀），供 App.vue 注入 documentElement */
export function themeCssVars(tokens: ThemeTokens): Array<[string, string]> {
  return (Object.keys(tokens.vars) as FgVarName[]).map(
    (key) => [`--fg-${key}`, tokens.vars[key]] as [string, string],
  )
}
