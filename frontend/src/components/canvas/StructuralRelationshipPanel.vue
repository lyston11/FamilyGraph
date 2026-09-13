<script setup lang="ts">
import { computed } from 'vue'
import { NButton } from 'naive-ui'

import { structuralEdgeLabel } from '@/components/canvas/relationshipDisplay'
import type { PersonalFamilyViewTopologyEdge } from '@/types/api'

/**
 * 结构边说明面板（09-13 design.md §5.3，FamilyTreeView 结构连线点击入口）：
 *
 * - 纯展示：数据只来自当前快照的 confirmed topology edge + 快照端点 display
 *   与时间信息，组件不发起请求、不做任何写操作；
 * - 展示两个**实际端点**之间的关系类型（亲子/配偶/伴侣/兄弟姐妹 + 子类型）
 *   与已确认状态——不使用「我的孙子」等 viewer 视角称谓；个人称谓与路径说明
 *   仍由个人摘要侧（资料页/个人面板）提供，本面板不伪造 PathStep 或证据；
 * - 「申请更正 / 查看待办」是两个只读安全跳转：只 emit 事件由页面导航到
 *   待办区，绝不产生任何 SourceFact 写操作。
 */

interface Props {
  edge: PersonalFamilyViewTopologyEdge
  /** 当前快照元信息（时间标注来源） */
  viewVersion: number
  computedAt: string | null
  /** 快照内 user_id → display.name 解析；解析不到返回 null（安全占位） */
  resolveName: (userId: number) => string | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'close'): void
  /** 申请更正：只跳转待办区（通知/数据权利流程入口），无任何写操作 */
  (e: 'request-correction'): void
  /** 查看待办：跳转 /notifications 待办区 */
  (e: 'view-todos'): void
}>()

const label = computed(() => structuralEdgeLabel(props.edge))

const subtypeText = computed(() => {
  if (props.edge.edge_kind !== 'parent' || props.edge.subtype === null) return null
  const SUBTYPES: Record<string, string> = {
    biological: '亲生',
    adoptive: '收养',
    step: '继亲',
    guardian: '监护',
  }
  return SUBTYPES[props.edge.subtype] ?? null
})

/** 端点名不可解析时用安全占位（不显示原始 id） */
const fromName = computed(() => props.resolveName(props.edge.from_user_id) ?? '某位成员')
const toName = computed(() => props.resolveName(props.edge.to_user_id) ?? '某位成员')

const endpointsText = computed(() => `${fromName.value} — ${toName.value}`)

const computedAtText = computed(() => props.computedAt ?? '暂无更新时间')
</script>

<template>
  <aside
    class="structural-panel"
    role="complementary"
    aria-label="结构关系说明"
    data-test="structural-panel"
  >
    <header class="panel-head">
      <h2 class="panel-title">亲属关系</h2>
      <NButton
        quaternary
        size="small"
        aria-label="关闭结构关系说明"
        data-test="structural-panel-close"
        @click="emit('close')"
      >
        关闭
      </NButton>
    </header>

    <dl class="panel-body">
      <div class="field" data-test="structural-endpoints">
        <dt>两端成员</dt>
        <dd>{{ endpointsText }}</dd>
      </div>

      <div class="field" data-test="structural-kind">
        <dt>关系类型</dt>
        <dd>
          {{ label }}
          <span v-if="subtypeText" class="concept-code" data-test="structural-subtype">
            {{ subtypeText }}
          </span>
        </dd>
      </div>

      <div class="field" data-test="structural-state">
        <dt>事实状态</dt>
        <dd>已确认的直接亲属事实</dd>
      </div>

      <div class="field" data-test="structural-meta">
        <dt>数据来源</dt>
        <dd>仅使用服务端确认的直接亲属事实；投影版本 v{{ viewVersion }}，更新于 {{ computedAtText }}</dd>
      </div>
    </dl>

    <footer class="panel-actions">
      <!-- 只读安全跳转：emit 后由页面导航到 /notifications 待办区；
           绝不产生 SourceFact 写操作，也不渲染任何 Bridge 操作控件 -->
      <NButton
        size="small"
        secondary
        data-test="structural-correct"
        @click="emit('request-correction')"
      >
        申请更正
      </NButton>
      <NButton size="small" secondary data-test="structural-view-todos" @click="emit('view-todos')">
        查看待办
      </NButton>
    </footer>
  </aside>
</template>

<style scoped>
.structural-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 8;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(340px, calc(100% - 24px));
  max-height: calc(100% - 24px);
  overflow: auto;
  box-sizing: border-box;
  padding: 14px;
  /* 透明微光抽屉质感：毛玻璃透出星空底 + 发丝高光描边 */
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  box-shadow:
    var(--fg-shadow-raised),
    0 0 24px var(--fg-glass-glow),
    inset 0 1px 0 0 color-mix(in srgb, var(--fg-surface-raised) 30%, transparent);
}

@supports not (backdrop-filter: blur(12px)) {
  .structural-panel {
    background: var(--fg-surface-raised);
  }
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.panel-title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.panel-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0;
}

.field {
  padding: 12px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-surface-sunken) 80%, transparent) 0%,
    color-mix(in srgb, var(--fg-surface-sunken) 60%, transparent) 100%
  );
  border: 1px solid color-mix(in srgb, var(--fg-line) 70%, transparent);
  border-radius: var(--fg-radius-control);
}

.panel-body .field dt {
  margin: 0 0 4px;
  font-size: 11px;
  color: var(--fg-ink-secondary);
}

.panel-body .field dd {
  margin: 0;
  font-size: 13px;
  color: var(--fg-ink);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.concept-code {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border: 1px solid color-mix(in srgb, var(--fg-line) 80%, transparent);
  border-radius: 4px;
  font-size: 11px;
  color: var(--fg-ink-secondary);
}

.panel-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
