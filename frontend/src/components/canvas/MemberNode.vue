<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { LockKeyhole, Network } from 'lucide-vue-next'

import MaskedField from '@/components/common/MaskedField.vue'
import {
  HANDLE_SOURCE_BOTTOM,
  HANDLE_SOURCE_RIGHT,
  HANDLE_TARGET_LEFT,
  HANDLE_TARGET_TOP,
  type FamilyCanvasNodeData,
} from '@/composables/useFamilyTreeCanvas'

/**
 * 家族树成员名牌（09-01 design.md §5.2 / 09-13 design.md §5.2）：
 *
 * - 纯展示组件：props 只接收已解码的 PersonalFamilyViewDisplay 与可见性层级
 *   （画布组件禁业务请求、禁读路由——红线），点击仅 emit select，由页面决定
 *   跳转目标（自己 → 家庭卡；他人 → 公示页）；
 * - 端口仅用于展示结构连线：顶部 target / 底部 source 连接亲子（上代在上、
 *   子女在下），左右端口连接配偶/伴侣/兄弟姐妹（同代横连）；端口不可拖线
 *   创建任何事实；
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
    @keydown.space.prevent="select"
  >
    <!-- 端口仅用于展示结构连线，全部显式 id（Vue Flow 按「类型内第一个端口」
         解析未指定 handle 的边，空 id 依赖会导致亲子边从同代端口出线） -->
    <Handle type="target" :id="HANDLE_TARGET_TOP" :position="Position.Top" class="handle" />
    <Handle
      type="target"
      :id="HANDLE_TARGET_LEFT"
      :position="Position.Left"
      class="handle handle-side"
    />
    <Handle
      type="source"
      :id="HANDLE_SOURCE_RIGHT"
      :position="Position.Right"
      class="handle handle-side"
    />
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
      <Network v-if="data.visibilityLevel === 'lineage_summary'" class="badge-icon" :size="12" aria-hidden="true" />
      <LockKeyhole v-else-if="data.visibilityLevel === 'self_private'" class="badge-icon" :size="12" aria-hidden="true" />
      {{ levelBadge.text }}
    </span>
    <Handle
      type="source"
      :id="HANDLE_SOURCE_BOTTOM"
      :position="Position.Bottom"
      class="handle"
    />
  </div>
</template>

<style scoped>
.member-node {
  position: relative; width: 192px; min-height: 144px; box-sizing: border-box;
  padding: 18px 16px 16px; cursor: pointer; color: var(--fg-canvas-ink);
  background: linear-gradient(135deg, color-mix(in srgb, var(--fg-canvas-ink) 7%, transparent), transparent 70%), var(--fg-glass-surface);
  backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
  border: 1px solid var(--fg-canvas-line); border-radius: 8px;
  box-shadow: 0 4px 0 color-mix(in srgb, var(--fg-canvas-surface) 85%, transparent),
    0 20px 30px color-mix(in srgb, var(--fg-canvas-surface) 75%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-canvas-ink) 12%, transparent);
  transition: box-shadow 0.25s ease, border-color 0.25s ease, background-color 0.25s ease;
}
.member-node:hover, .member-node:focus-visible {
  border-color: var(--fg-canvas-muted);
  box-shadow: 0 5px 0 var(--fg-canvas-surface), 0 26px 40px color-mix(in srgb, var(--fg-canvas-surface) 85%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-canvas-ink) 25%, transparent);
}
.member-node.is-self {
  background: color-mix(in srgb, var(--fg-accent) 10%, var(--fg-glass-surface)); color: var(--fg-ink);
  border-color: color-mix(in srgb, var(--fg-accent) 65%, transparent);
  box-shadow: 0 5px 0 color-mix(in srgb, var(--fg-canvas-muted) 25%, var(--fg-canvas-surface)),
    0 24px 40px color-mix(in srgb, var(--fg-canvas-surface) 80%, transparent), inset 0 1px 0 var(--fg-surface-raised);
}
.summary-card { border-style: dashed; }
.handle { width: 6px; height: 6px; background: var(--fg-canvas-muted); border: 2px solid var(--fg-canvas-surface); }
.handle-side { opacity: 0.8; }
.card-head { display: flex; align-items: center; gap: 10px; }
.avatar {
  display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px;
  flex-shrink: 0; font-family: var(--fg-font-display); font-size: 20px; font-weight: 600;
  color: var(--fg-canvas-ink); background: color-mix(in srgb, var(--fg-canvas-ink) 8%, transparent);
  border: 1px solid var(--fg-canvas-line); border-radius: 50%;
  box-shadow: inset 0 1px 0 color-mix(in srgb, var(--fg-canvas-ink) 16%, transparent), 0 3px 5px color-mix(in srgb, var(--fg-canvas-surface) 25%, transparent);
}
.is-self .avatar { background: var(--fg-accent-soft); color: var(--fg-accent); border-color: var(--fg-line); }
.name { font-family: var(--fg-font-display); font-size: 18px; font-weight: 600; letter-spacing: 0; overflow-wrap: anywhere; min-width: 0; }
.self-chip { flex-shrink: 0; padding: 0 6px; font-size: 10px; }
.card-meta { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.term-chip { font-size: 12px; color: var(--fg-canvas-ink); }
.birth { color: var(--fg-canvas-muted); font-size: 11px; white-space: nowrap; }
.level-badge { display: inline-flex; align-items: center; gap: 5px; margin-top: 10px; padding: 0; border: none; background: none; color: var(--fg-canvas-muted); font-size: 10px; }
.is-self .term-chip { color: var(--fg-accent); }
.is-self .birth, .is-self .level-badge { color: var(--fg-ink-secondary); }
.badge-icon { flex-shrink: 0; }
.member-node :deep(.masked-field) { color: var(--fg-canvas-muted); }
@supports not (backdrop-filter: blur(12px)) {
  .member-node { background: var(--fg-canvas-surface-raised); }
  .member-node.is-self { background: var(--fg-surface-raised); }
}
@media (prefers-reduced-motion: reduce) {
  .member-node { transition: none; }
}
</style>
