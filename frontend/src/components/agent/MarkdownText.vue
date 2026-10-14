<script setup lang="ts">
import { computed } from 'vue'

import MarkdownInline from '@/components/agent/MarkdownInline.vue'
import { parseMarkdown, type MarkdownBlock } from '@/components/agent/markdown'

/**
 * 助手正文的 markdown 渲染（token → 元素，无 `v-html`、无第三方依赖）。
 *
 * 只负责把 `parseMarkdown` 的块渲染出来；样式沿用气泡所在主题 token。
 * 组件对输入不做任何"修复"：不认识的语法原样显示，`<script>` 只是字符。
 */
const props = defineProps<{ text: string }>()

const blocks = computed<MarkdownBlock[]>(() => parseMarkdown(props.text))

/** 标题层级 → 元素名（`h1` 在气泡里过重，最小从 `h3` 起，语义仍保留层级顺序）。 */
function headingTag(level: number): string {
  return `h${Math.min(level + 2, 6)}`
}
</script>

<template>
  <div class="markdown" data-test="markdown-text">
    <template v-for="(block, index) in blocks" :key="index">
      <ul v-if="block.kind === 'list' && !block.ordered" class="md-list">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <MarkdownInline :tokens="item" />
        </li>
      </ul>
      <ol v-else-if="block.kind === 'list'" class="md-list">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <MarkdownInline :tokens="item" />
        </li>
      </ol>
      <pre v-else-if="block.kind === 'code'" class="md-pre"><code>{{ block.text }}</code></pre>
      <div v-else-if="block.kind === 'table'" class="md-table-wrap">
        <table class="md-table">
          <thead>
            <tr>
              <th v-for="(cell, cellIndex) in block.header" :key="cellIndex">
                <MarkdownInline :tokens="cell" />
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in block.rows" :key="rowIndex">
              <td v-for="(cell, cellIndex) in row" :key="cellIndex">
                <MarkdownInline :tokens="cell" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <component
        :is="headingTag(block.level)"
        v-else-if="block.kind === 'heading'"
        class="md-heading"
      >
        <MarkdownInline :tokens="block.line" />
      </component>
      <p v-else-if="block.kind === 'paragraph'" class="md-paragraph">
        <template v-for="(line, lineIndex) in block.lines" :key="lineIndex">
          <br v-if="lineIndex > 0" />
          <MarkdownInline :tokens="line" />
        </template>
      </p>
    </template>
  </div>
</template>

<style scoped>
/* 气泡本身是 pre-wrap（纯文本路径需要它）；markdown 块自己承担换行语义，
   所以这里重置为 normal，单换行由段内的 <br> 表达。 */
.markdown {
  white-space: normal;
}

.markdown > :first-child {
  margin-top: 0;
}

.markdown > :last-child {
  margin-bottom: 0;
}

.md-paragraph {
  margin: 0 0 6px;
}

.md-heading {
  margin: 10px 0 6px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.md-list {
  margin: 0 0 6px;
  padding-left: 1.3em;
}

.md-list li + li {
  margin-top: 2px;
}

.markdown strong {
  font-weight: 600;
}

.md-code,
.md-pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
}

.md-code {
  padding: 1px 5px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--fg-ink) 10%, transparent);
}

.md-table-wrap {
  margin: 0 0 6px;
  overflow-x: auto;
}

.md-table {
  border-collapse: collapse;
  font-size: 13px;
}

.md-table th,
.md-table td {
  padding: 3px 8px;
  border: 1px solid var(--fg-line);
  text-align: left;
  vertical-align: top;
}

.md-table th {
  background: color-mix(in srgb, var(--fg-ink) 8%, transparent);
  font-weight: 600;
}

.md-pre {
  margin: 0 0 6px;
  padding: 8px 10px;
  border: 1px solid var(--fg-line);
  border-radius: 8px;
  background: color-mix(in srgb, var(--fg-ink) 6%, transparent);
  white-space: pre;
  overflow-x: auto;
}
</style>
