/**
 * 助手正文的最小 markdown 子集解析（无依赖、无 HTML）。
 *
 * ## 为什么需要它
 *
 * 模型输出天然是 markdown（`**依据**`、`- 逐跳路径`、`#` 标题…），而气泡此前
 * 用 `{{ item.text }}` + `white-space: pre-wrap` 直出纯文本，于是 `**依据**`
 * 按字面量显示（2026-10-14 现场截图）。渲染它的方式是解析成 **token**，再由
 * Vue 组件按 token 生成元素——全程不产生 HTML 字符串，因此不需要 `v-html`、
 * 不需要 sanitizer，也不新增依赖（`frontend/package.json` 至今没有 markdown 库，
 * 全仓也没有一处 `v-html`）。
 *
 * ## 支持子集（刻意的边界）
 *
 * 块：段落（单换行 → `<br>`）、`#`…`######` 标题、无序/有序列表、``` 围栏代码、
 * GFM 管道表格（`| a | b |` + `|---|---|` 分隔行）；
 * 行内：`**粗体**` / `__粗体__`、`*斜体*` / `_斜体_`、`` `行内代码` ``。
 *
 * **不支持**（不声明为能力，而不是"暂时没做"）：脚注、任务列表、链接、图片、
 * 引用块、嵌套强调、单元格对齐、原始 HTML 直通。任何不认识的语法按纯文本原样
 * 显示——这也是安全边界：`<script>` 只是字符，永远不会变成元素。
 *
 * 表格不是"顺手加的"：首轮真实验收（2026-10-14，run 1850）里模型自己产出了
 * `| 来源层级 | 可用叫法 |` 表格，不渲染就退化成一行竖线。
 */

export type InlineToken =
  | { kind: 'text'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'em'; text: string }
  | { kind: 'code'; text: string }

/** 一行内的行内 token 序列（行是行内解析的最小单位）。 */
export type MarkdownLine = InlineToken[]

/** 表格的一行：单元格序列（每个单元格是一行行内 token）。 */
export type MarkdownRow = MarkdownLine[]

export type MarkdownBlock =
  | { kind: 'paragraph'; lines: MarkdownLine[] }
  | { kind: 'heading'; level: number; line: MarkdownLine }
  | { kind: 'list'; ordered: boolean; items: MarkdownLine[] }
  | { kind: 'table'; header: MarkdownRow; rows: MarkdownRow[] }
  | { kind: 'code'; text: string }

/**
 * 行内标记的匹配顺序即优先级：先行内代码（其中的 `*` 不再解释），再 `**`，
 * 再单个 `*`/`_`。两侧都要求至少一个字符，因此 `**` 单独出现不会吞掉后续文本。
 */
const INLINE_PATTERN = /(`[^`\n]+`)|(\*\*[^\n]+?\*\*)|(__[^\n]+?__)|(\*[^*\n]+?\*)|(_[^_\n]+?_)/g

const WORD_CHAR_PATTERN = /[\p{L}\p{N}]/u

/** 行内解析。未闭合的标记按纯文本保留（流式到达途中会短暂出现这种形态）。 */
export function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let cursor = 0
  const pushText = (value: string): void => {
    if (value !== '') tokens.push({ kind: 'text', text: value })
  }

  INLINE_PATTERN.lastIndex = 0
  let match = INLINE_PATTERN.exec(text)
  while (match !== null) {
    const raw = match[0]
    // 词内下划线不是强调（`concept_code` 不得被渲染成斜体）。
    const precededByWordChar = match.index > 0 && WORD_CHAR_PATTERN.test(text[match.index - 1]!)
    if (raw.startsWith('_') && precededByWordChar) {
      match = INLINE_PATTERN.exec(text)
      continue
    }
    pushText(text.slice(cursor, match.index))
    tokens.push(inlineToken(raw))
    cursor = match.index + raw.length
    match = INLINE_PATTERN.exec(text)
  }
  pushText(text.slice(cursor))
  return tokens
}

function inlineToken(raw: string): InlineToken {
  if (raw.startsWith('`')) return { kind: 'code', text: raw.slice(1, -1) }
  if (raw.startsWith('**') || raw.startsWith('__')) return { kind: 'strong', text: raw.slice(2, -2) }
  return { kind: 'em', text: raw.slice(1, -1) }
}

const HEADING_PATTERN = /^(#{1,6})\s+(.*)$/
const UNORDERED_ITEM_PATTERN = /^[-*+]\s+(.*)$/
const ORDERED_ITEM_PATTERN = /^\d+[.)]\s+(.*)$/
const FENCE_PATTERN = /^\s*```/
const TABLE_ROW_PATTERN = /^\s*\|.*\|\s*$/
/** 表格分隔行：`|---|:--:|` 形态（单元格里只允许 `-`、`:` 与空白）。 */
const TABLE_SEPARATOR_PATTERN = /^\s*\|(\s*:?-{2,}:?\s*\|)+\s*$/

/** 单元格切分：去掉最外层管道后按 `|` 切开（不支持转义管道，属已声明边界）。 */
function parseTableRow(line: string): MarkdownRow {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => parseInline(cell.trim()))
}

/** 块级解析。返回值可直接被组件按 kind 渲染，无需再做字符串处理。 */
export function parseMarkdown(text: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = []
  let paragraph: MarkdownLine[] = []
  let list: { ordered: boolean; items: MarkdownLine[] } | null = null
  let fence: string[] | null = null

  const flushParagraph = (): void => {
    if (paragraph.length > 0) {
      blocks.push({ kind: 'paragraph', lines: paragraph })
      paragraph = []
    }
  }
  const flushList = (): void => {
    if (list !== null) {
      blocks.push({ kind: 'list', ordered: list.ordered, items: list.items })
      list = null
    }
  }
  const flushAll = (): void => {
    flushParagraph()
    flushList()
  }

  const rawLines = text.replace(/\r\n?/g, '\n').split('\n')
  for (let index = 0; index < rawLines.length; index += 1) {
    const line = rawLines[index]!
    if (fence !== null) {
      if (FENCE_PATTERN.test(line)) {
        blocks.push({ kind: 'code', text: fence.join('\n') })
        fence = null
      } else {
        fence.push(line)
      }
      continue
    }
    if (FENCE_PATTERN.test(line)) {
      flushAll()
      fence = []
      continue
    }
    if (line.trim() === '') {
      flushAll()
      continue
    }
    const heading = HEADING_PATTERN.exec(line)
    if (heading !== null) {
      flushAll()
      blocks.push({ kind: 'heading', level: heading[1]!.length, line: parseInline(heading[2]!.trim()) })
      continue
    }
    // 表格：当前行是管道行且下一行是分隔行时才成立（避免把普通 `|` 文本吃掉）。
    const next = rawLines[index + 1]
    if (TABLE_ROW_PATTERN.test(line) && next !== undefined && TABLE_SEPARATOR_PATTERN.test(next)) {
      flushAll()
      const header = parseTableRow(line)
      const rows: MarkdownRow[] = []
      index += 2
      while (index < rawLines.length && TABLE_ROW_PATTERN.test(rawLines[index]!)) {
        rows.push(parseTableRow(rawLines[index]!))
        index += 1
      }
      index -= 1 // 交回 for 递增，避免跳过表格后的下一行
      blocks.push({ kind: 'table', header, rows })
      continue
    }
    const unordered = UNORDERED_ITEM_PATTERN.exec(line)
    const ordered = unordered === null ? ORDERED_ITEM_PATTERN.exec(line) : null
    if (unordered !== null || ordered !== null) {
      const isOrdered = ordered !== null
      flushParagraph()
      if (list === null || list.ordered !== isOrdered) {
        flushList()
        list = { ordered: isOrdered, items: [] }
      }
      list.items.push(parseInline((unordered ?? ordered)![1]!))
      continue
    }
    flushList()
    paragraph.push(parseInline(line))
  }

  // 未闭合的围栏按代码块收尾：流式到达途中会短暂出现，丢弃内容比原样显示更糟。
  if (fence !== null) blocks.push({ kind: 'code', text: fence.join('\n') })
  flushAll()
  return blocks
}
