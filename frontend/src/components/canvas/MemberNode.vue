<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

import type { Member } from '@/types/api'

/**
 * 画布成员名牌（design.md §3.2 重绘）：姓字衬线纸牌头像位 + 名字 + 称谓 +
 * 右下角确档状态章（identity_confirmed=墨点实底 / provisional=空心虚线章）。
 *
 * - 纯展示组件：数据全部由 VueFlow 节点 data 注入（规范红线：画布组件禁业务请求）；
 * - 样式全部走 --fg-* token，主题随根层 CSS 变量自动联动（组件内不判断主题、不 watch token，
 *   避免主题切换引发全画布重渲染）；
 * - lineage_summary 摘要节点：虚线边 + 「申请进入 TA 的家庭空间」动作（m2c）。
 */
interface Props {
  id: string
  data: {
    member?: Member
    viewLabel: string | null
    summary?: boolean
  }
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'open', memberId: number): void
  (e: 'join', memberId: number): void
}>()

const member = computed(() => props.data.member)

/** 确档状态徽章（architecture §0.3 身份状态机投影）：
 * 「这是我」确认是 claimed ⇆ identity_confirmed 的唯一合法联动，故 claim_status 即确档投影。 */
const identity = computed(() =>
  member.value?.claim_status === 'claimed'
    ? { text: '已确档', cls: 'fg-badge fg-badge--confirmed' }
    : { text: '待确档', cls: 'fg-badge fg-badge--provisional' },
)

const genderText = computed(() => {
  const gender = member.value?.gender
  return gender === 'f' ? '女' : gender === 'm' ? '男' : '不详'
})

function open(): void {
  if (member.value) emit('open', member.value.id)
}
</script>

<template>
  <div
    v-if="member"
    class="member-node"
    :class="{ 'summary-card': data.summary }"
    data-test="canvas-member-card"
    role="button"
    tabindex="0"
    @click="open"
    @keyup.enter="open"
  >
    <Handle type="target" :position="Position.Top" class="handle" />
    <div class="card-head">
      <span class="avatar" aria-hidden="true">{{ member.name.slice(0, 1) }}</span>
      <span class="name">{{ member.name }}</span>
    </div>
    <div class="card-meta">
      <span v-if="data.viewLabel" class="term-chip" data-test="view-label">{{ data.viewLabel }}</span>
      <span class="gender">{{ genderText }}</span>
    </div>
    <span class="identity-stamp" :class="identity.cls" data-test="identity-stamp">
      {{ identity.text }}
    </span>
    <button
      v-if="data.summary"
      type="button"
      class="join-btn"
      data-test="join-request-btn"
      @click.stop="emit('join', member.id)"
    >
      申请进入 TA 的家庭空间
    </button>
    <Handle type="source" :position="Position.Bottom" class="handle" />
  </div>
  <!-- 瞬态兜底：graph 先于 members 到齐时不出空壳 -->
  <div v-else class="member-node member-node--placeholder" data-test="canvas-member-card">#</div>
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

.member-node--placeholder {
  min-width: 60px;
  text-align: center;
  color: var(--fg-ink-faint);
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

/* 姓字纸牌头像位：主色柔底 + 标题字体（与 HomeView 成员卡同一隐喻） */
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
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

/* 称谓 chip：主色柔底彩字（对卡面 ≥4.5:1），清晰称谓是家谱产品要求 */
.term-chip {
  padding: 1px 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: 999px;
  white-space: nowrap;
}

.gender {
  color: var(--fg-ink-faint);
  font-size: 12px;
}

/* 右下角确档章：压角微旋（章印隐喻）；配色走 .fg-badge--* 全站统一工具类 */
.identity-stamp {
  position: absolute;
  right: -6px;
  bottom: -9px;
  transform: rotate(-6deg);
  box-shadow: var(--fg-shadow-card);
}

.join-btn {
  margin-top: 8px;
  width: 100%;
  padding: 4px 8px;
  font-size: 12px;
  font-family: var(--fg-font-body);
  color: var(--fg-accent);
  background-color: transparent;
  border: 1px solid var(--fg-accent);
  border-radius: var(--fg-radius-control);
  cursor: pointer;
}

.join-btn:hover {
  background-color: var(--fg-accent-soft);
}
</style>
