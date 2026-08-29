/**
 * 设计 token 单一来源（design.md §2.1，PRD R2/R3）。
 *
 * 分层：
 * - L1 palette / scale：原始色板与字号、间距阶（仅在本文件内派生 L2，业务代码不得直接取色）。
 * - L2 vars：语义 token，键为 CSS 变量名（注入时拼 `--fg-` 前缀），由 App.vue
 *   批量写入 documentElement——自绘样式与 naive-themes.ts 的 themeOverrides 同源。
 *
 * 两主题（纸墨 paper / 清雅 modern）均为浅色系；不引外部 webfont，全部本地字体栈。
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

/** 纸墨主题 L1 原始色板：宣纸米白底 + 深墨 + 朱砂主色 + 黛青/苔绿次强调 */
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

/** 清雅主题 L1 原始色板：纯白底 + 石墨 + 青蓝主色 + 青/靛次强调 */
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
  paperBase: '#f7f4ed',
  paperRaised: '#fdfbf6',
  paperSunken: '#eee8da',
  paperDot: '#d8d1bc',
  ink: '#2b2b26',
  inkSoft: '#5c594c',
  inkFaint: '#8f8b7b',
  vermilion: '#c0392b',
  vermilionHover: '#cd4a3c',
  vermilionPressed: '#a53124',
  indigo: '#3d5a6c',
  moss: '#5f7052',
  // 调深自 #a8752c：小字文本对宣纸底 / 8% 软底须 ≥4.5:1（WCAG AA，2026-08-29 实测 5.39 / 4.86）
  ochre: '#8c5f1d',
  seal: '#6f6a59',
  lineHairline: '#ddd4bf',
  lineStrong: '#c8bda0',
}

const modernPalette: ModernPalette = {
  whiteBase: '#ffffff',
  whiteRaised: '#ffffff',
  graySunken: '#f2f4f7',
  grayDot: '#d3d7e0',
  ink: '#1f2329',
  inkSoft: '#4e5561',
  inkFaint: '#8a919e',
  blue: '#2f6fb3',
  blueHover: '#3d80c4',
  bluePressed: '#275e98',
  // 调深自 #2b8a8f：实底徽章白字对纯白底须 ≥4.5:1（WCAG AA，2026-08-29 实测 5.70）
  cyan: '#1f7176',
  indigo: '#4c5fbf',
  // 调深自 #9a6b15：小字文本对纯白底 / 8% 软底须 ≥4.5:1（WCAG AA，2026-08-29 实测 6.20 / 5.55）
  amber: '#82590f',
  red: '#c0392b',
  slate: '#6f7683',
  lineHairline: '#e5e8ec',
  lineStrong: '#cfd4dc',
}

/** 纸墨（默认）：宣纸米白 + 暖点阵 + 深墨文字 + 朱砂主色 + 宋体标题（design.md §2.3） */
export const paperTokens: ThemeTokens = {
  name: 'paper',
  label: '纸墨',
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
    // 朱砂 10% 柔和底（rgba 字面量，色相同 accent）
    'accent-soft': 'rgba(192, 57, 43, 0.1)',
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
    'radius-card': '4px',
    'radius-control': '3px',
    'shadow-card': '0 1px 2px rgba(43, 43, 38, 0.06)',
    'shadow-raised': '0 2px 10px rgba(43, 43, 38, 0.12)',
  },
}

/** 清雅：纯白大留白 + 冷灰点阵 + 石墨文字 + 青蓝主色 + 无衬线标题（design.md §2.3） */
export const modernTokens: ThemeTokens = {
  name: 'modern',
  label: '清雅',
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
    // 青蓝 10% 柔和底（rgba 字面量，色相同 accent）
    'accent-soft': 'rgba(47, 111, 179, 0.1)',
    'accent-ink': '#ffffff',
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
    'radius-card': '12px',
    'radius-control': '8px',
    'shadow-card': '0 1px 3px rgba(31, 35, 41, 0.08), 0 1px 2px rgba(31, 35, 41, 0.04)',
    'shadow-raised': '0 8px 24px rgba(31, 35, 41, 0.12)',
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
