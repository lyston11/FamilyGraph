<script setup lang="ts">
import type { InlineToken } from '@/components/agent/markdown'

/**
 * 行内 token → 元素。**唯一**的行内映射点：所有块类型共用它，避免每个块各写
 * 一份 v-if 链。文本走插值（Vue 自动转义），因此这里不存在 HTML 字符串，
 * 也不存在 v-html。
 */
defineProps<{ tokens: InlineToken[] }>()
</script>

<template>
  <template v-for="(token, index) in tokens" :key="index">
    <strong v-if="token.kind === 'strong'">{{ token.text }}</strong>
    <em v-else-if="token.kind === 'em'">{{ token.text }}</em>
    <code v-else-if="token.kind === 'code'" class="md-code">{{ token.text }}</code>
    <template v-else>{{ token.text }}</template>
  </template>
</template>
