<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

import MaskedField from '@/components/common/MaskedField.vue'
import type { FamilyCanvasNodeData } from '@/composables/useFamilyTreeCanvas'

/**
 * 家族树成员名牌（09-01 design.md §5.2，PersonalFamilyView 口径）：
 *
 * - 纯展示组件：props 只接收已解码的 PersonalFamilyViewDisplay 与可见性层级
 *   （画布组件禁业务请求、禁读路由——红线），点击仅 emit select，由页面决定
 *   跳转目标（自己 → 家庭卡；他人 → 公示页）；
 * - 节点状态用 icon + 文字表达（不只靠颜色）：自己强调、self_private、
 *   household_detail、lineage_summary；masked 字段复用 MaskedField 统一锁形章；
 * - lineage_summary 节点：虚线卡 + 「族谱摘要 · 不可展开」标记，无展开按钮、
 *   无家庭卡入口（design.md §1.3）。
 */
interface Props {
  id: string
  data: FamilyCanvasNodeData
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select', userId: number): void
}>()

const display = computed(() => props.data.display)

const LEVEL_BADGES = {
  self_private: { text: '仅本人', cls: 'fg-badge fg-badge--accent' },
  household_detail: { text: '家庭可见', cls: 'fg-badge fg-badge--confirmed' },
  lineage_summary: { text: '族谱摘要 · 不可展开', cls: 'fg-badge fg-badge--provisional' },
} as const

const levelBadge = computed(() => LEVEL_BADGES[props.data.visibilityLevel])

/** 姓字纸牌头像位：display.name 是 baseline 字段，恒明文 */
const avatarChar = computed(() => display.value.name.slice(0, 1))

function select(): void {
  emit('select', display.value.id)
}
</script>

<template>
  <div
    class="member-node"
    :class="{
      'summary-card': data.visibilityLevel === 'lineage_summary',
      'is-self': data.isSelf,
    }"
    data-test="canvas-member-card"
    role="button"
    tabindex="0"
    @click="select"
    @keyup.enter="select"
  >
    <Handle type="target" :position="Position.Top" class="handle" />
    <div class="card-head">
      <span class="avatar" aria-hidden="true">{{ avatarChar }}</span>
      <span class="name">{{ display.name }}</span>
      <span v-if="data.isSelf" class="fg-badge fg-badge--accent self-chip" data-test="self-chip">我</span>
    </div>
    <div class="card-meta">
      <span v-if="data.term" class="term-chip" data-test="view-label">{{ data.term }}</span>
      <span v-if="display.birth !== null && !('__masked__' in display.birth)" class="birth" data-test="node-birth">
        {{ display.birth.date ?? '不详' }}
      </span>
      <MaskedField v-else-if="display.birth !== null" :value="display.birth" />
    </div>
    <span class="level-badge" :class="levelBadge.cls" data-test="visibility-badge">
      <svg
        v-if="data.visibilityLevel === 'lineage_summary'"
        class="badge-icon"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <circle cx="12" cy="5" r="2" />
        <circle cx="6" cy="18" r="2" />
        <circle cx="18" cy="18" r="2" />
        <path d="M12 7v3.5M6 16v-1.2a1.8 1.8 0 0 1 1.8-1.8h8.4A1.8 1.8 0 0 1 18 14.8V16" />
      </svg>
      <svg
        v-else-if="data.visibilityLevel === 'self_private'"
        class="badge-icon"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path d="M12 3a5 5 0 0 0-5 5v2H6a2 2 0 0 0-2 2v8h16v-8a2 2 0 0 0-2-2h-1V8a5 5 0 0 0-5-5Zm-3 7V8a3 3 0 1 1 6 0v2H9Z" />
      </svg>
      {{ levelBadge.text }}
    </span>
    <Handle type="source" :position="Position.Bottom" class="handle" />
  </div>
</template>

<style scoped>
/* 名牌/立牌：纸墨=直角纸面 + 发丝线 + 悬停微浮起；清雅=白底大圆角柔和阴影（token 驱动） */
.member-node {
  position: relative;
  min-width: 150px;
  padding: 10px 14px 14px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  transition:
    box-shadow 0.2s,
    border-color 0.2s;
}

.member-node:hover,
.member-node:focus-visible {
  border-color: var(--fg-accent);
  box-shadow: var(--fg-shadow-raised);
}

/* 自己强调：主色描边（表达当前主体，不改变权限） */
.member-node.is-self {
  border-color: var(--fg-accent);
  box-shadow: 0 0 0 1px var(--fg-accent), var(--fg-shadow-card);
}

.member-node.summary-card {
  border-style: dashed;
}

/* 连接点：定位尺寸由 @vue-flow/core/dist/style.css 提供，这里只着色（不引 theme-default 配色） */
.handle {
  width: 8px;
  height: 8px;
  background-color: var(--fg-line-strong);
  border: 1px solid var(--fg-surface-raised);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 姓字纸牌头像位：主色柔底 + 标题字体 */
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  font-family: var(--fg-font-display);
  font-size: 17px;
  font-weight: 700;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: var(--fg-radius-control);
}

[data-theme='modern'] .avatar {
  border-radius: 999px;
}

.name {
  font-family: var(--fg-font-display);
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.self-chip {
  flex-shrink: 0;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  flex-wrap: wrap;
}

/* 称谓 chip：主色柔底彩字（对卡面 ≥4.5:1），称谓是服务端解析的安全投影 */
.term-chip {
  padding: 1px 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: 999px;
  white-space: nowrap;
}

.birth {
  color: var(--fg-ink-faint);
  font-size: 12px;
  white-space: nowrap;
}

/* 可见性层级徽章：icon + 文字（design.md §7 不只靠颜色） */
.level-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
}

.badge-icon {
  width: 12px;
  height: 12px;
  fill: currentColor;
  flex-shrink: 0;
}
</style>
