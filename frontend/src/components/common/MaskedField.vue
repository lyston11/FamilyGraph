<script setup lang="ts">
import { computed } from 'vue'

/**
 * 遮罩字段渲染（architecture §6）：{__masked__: true} → 锁样式；
 * 否则透传渲染值。所有可见性敏感展示统一走此组件。
 */
const props = defineProps<{ value: unknown }>()

const masked = computed(
  () =>
    typeof props.value === "object" &&
    props.value !== null &&
    "__masked__" in (props.value as Record<string, unknown>),
)

const text = computed(() => {
  const v = props.value
  if (v === null || v === undefined) return "不详"
  if (typeof v === "string") return v
  if (typeof v === "object") {
    // 结构化日期：优先人读文本，其次 mirror，最后 ISO
    const d = v as { date?: string | null; mirror_date?: string | null }
    return d.date ?? d.mirror_date ?? "不详"
  }
  return String(v)
})
</script>

<template>
  <span v-if="masked" class="masked-field" data-test="masked-field">🔒 已隐藏</span>
  <span v-else data-test="unmasked-value">{{ text }}</span>
</template>

<style scoped>
.masked-field {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  letter-spacing: 1px;
}
</style>
