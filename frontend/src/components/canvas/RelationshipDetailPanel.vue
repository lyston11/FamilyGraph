<script setup lang="ts">
import { computed } from 'vue'
import { NButton } from 'naive-ui'

import { factStateLabel, pathClassLabel } from '@/components/canvas/relationshipDisplay'
import type { PersonalFamilyViewEdge, PersonalFamilyViewPathStep } from '@/types/api'

/**
 * 只读关系说明面板（09-01 design.md §5.3，FamilyTreeView 边点击入口、
 * PersonProfileView 关系说明入口共用）：
 *
 * - 数据全部来自当前 PersonalFamilyView 快照的边对象 + 快照元信息
 *   （view_version/computed_at），组件不发起请求、不做任何写操作；
 * - 展示：称谓 term、主路径、最多 3 条替代路径（超出截断并提示）、
 *   path_class/concept_code 安全来源摘要、事实状态与更新时间；
 * - 遮罩/缺失安全降级：term/concept_code 缺失显示「暂无」，路径中的成员名无法
 *   在当前快照解析时显示安全占位（不显示原始 id）；lineage_summary 目标不可
 *   展开详情，面板自身无任何展开控件；
 * - 「申请更正 / 查看待办」是两个只读安全跳转（PRD §2.4）：只 emit 事件由
 *   页面导航到 /notifications（待办区），绝不产生任何 SourceFact 写操作；
 *   Bridge pending 只在通知/待办处理，本面板不渲染 approve/reject/consent/
 *   revoke 等 Bridge 操作控件。
 */

interface Props {
  edge: PersonalFamilyViewEdge
  /** 当前快照元信息（失败/陈旧标注来源） */
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

const MAX_ALTERNATIVE_PATHS = 3

const termText = computed(() => props.edge.term ?? '暂无称谓')

const pathClassText = computed(() => pathClassLabel(props.edge.path_class))

const factStateText = computed(() => factStateLabel(props.edge.inclusion_reason_code))

/** 路径步骤链 → 人读链路「A → B → C」；名字不可解析时用安全占位（不显示原始 id） */
function chainText(path: readonly PersonalFamilyViewPathStep[]): string {
  if (path.length === 0) return ''
  const parts: string[] = []
  for (const step of path) parts.push(props.resolveName(step.from) ?? '某位成员')
  const last = path[path.length - 1]
  if (last) parts.push(props.resolveName(last.to) ?? '某位成员')
  return parts.join(' → ')
}

const mainPathText = computed(() => chainText(props.edge.path))
const alternativePathTexts = computed(() =>
  props.edge.alternative_paths.slice(0, MAX_ALTERNATIVE_PATHS).map((path) => chainText(path)),
)

/** 服务端返回超过 3 条替代路径：截断渲染并提示（不显示总条数以外信息） */
const hasMoreAlternatives = computed(
  () => props.edge.alternative_paths.length > MAX_ALTERNATIVE_PATHS,
)

const computedAtText = computed(() => props.computedAt ?? '暂无更新时间')
</script>

<template>
  <aside class="relation-panel" role="complementary" aria-label="关系说明" data-test="relation-panel">
    <header class="panel-head">
      <h2 class="panel-title">关系说明</h2>
      <NButton
        quaternary
        size="small"
        aria-label="关闭关系说明"
        data-test="relation-panel-close"
        @click="emit('close')"
      >
        关闭
      </NButton>
    </header>

    <dl class="panel-body">
      <div class="field" data-test="relation-term">
        <dt>称谓</dt>
        <dd>{{ termText }}</dd>
      </div>

      <div class="field" data-test="relation-path-class">
        <dt>关系类型</dt>
        <dd>
          {{ pathClassText }}
          <span v-if="edge.concept_code" class="concept-code" data-test="relation-concept-code">
            编码 {{ edge.concept_code }}
          </span>
        </dd>
      </div>

      <div class="field" data-test="relation-main-path">
        <dt>主路径</dt>
        <dd>
          <span v-if="mainPathText" data-test="relation-main-path-text">{{ mainPathText }}</span>
          <span v-else class="degraded">暂无路径说明</span>
        </dd>
      </div>

      <div class="field" data-test="relation-alt-paths">
        <dt>替代路径（最多 3 条）</dt>
        <dd>
          <ol v-if="alternativePathTexts.length > 0" class="path-list">
            <li
              v-for="(text, pathIndex) in alternativePathTexts"
              :key="`alt-${pathIndex}`"
              class="path-step"
              :data-test="`relation-alt-path-${pathIndex}`"
            >
              {{ text }}
            </li>
          </ol>
          <span v-else class="degraded">暂无替代路径</span>
          <span
            v-if="hasMoreAlternatives"
            class="degraded"
            data-test="relation-alt-paths-truncated"
          >
            替代路径较多，仅显示前 {{ MAX_ALTERNATIVE_PATHS }} 条。
          </span>
        </dd>
      </div>

      <div class="field" data-test="relation-fact-state">
        <dt>事实状态</dt>
        <dd>{{ factStateText }}</dd>
      </div>

      <div class="field" data-test="relation-meta">
        <dt>数据来源</dt>
        <dd>仅使用服务端确认的关系事实；投影版本 v{{ viewVersion }}，更新于 {{ computedAtText }}</dd>
      </div>
    </dl>

    <footer class="panel-actions">
      <!-- 只读安全跳转：emit 后由页面导航到 /notifications 待办区；
           绝不产生 SourceFact 写操作，也不渲染任何 Bridge 操作控件 -->
      <NButton
        size="small"
        secondary
        data-test="relation-correct"
        @click="emit('request-correction')"
      >
        申请更正
      </NButton>
      <NButton size="small" secondary data-test="relation-view-todos" @click="emit('view-todos')">
        查看待办
      </NButton>
    </footer>
  </aside>
</template>

<style scoped>
.relation-panel {
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
  /* 透明微光抽屉质感（PRD §2.5）：毛玻璃透出星空底 + 发丝高光描边 */
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

/* 降级方案：不支持 backdrop-filter 时使用不透明背景 */
@supports not (backdrop-filter: blur(12px)) {
  .relation-panel {
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
  transition: all 0.3s ease;
  animation: fadeInUp 0.4s ease backwards;
}

.field:nth-child(1) { animation-delay: 0.05s; }
.field:nth-child(2) { animation-delay: 0.1s; }
.field:nth-child(3) { animation-delay: 0.15s; }
.field:nth-child(4) { animation-delay: 0.2s; }
.field:nth-child(5) { animation-delay: 0.25s; }
.field:nth-child(6) { animation-delay: 0.3s; }

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.field:hover {
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-surface-sunken) 90%, transparent) 0%,
    color-mix(in srgb, var(--fg-surface-sunken) 70%, transparent) 100%
  );
  border-color: color-mix(in srgb, var(--fg-line-strong) 80%, transparent);
  transform: translateX(2px);
}

.field dt {
  margin-bottom: 6px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--fg-ink-secondary);
  opacity: 0.8;
}

.field dd {
  margin: 0;
  font-size: 14px;
  color: var(--fg-ink);
  line-height: 1.6;
  font-weight: 500;
}

.concept-code {
  display: block;
  font-size: 12px;
  color: var(--fg-ink-faint);
}

.path-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.path-step {
  padding: 8px 12px;
  font-size: 13px;
  color: var(--fg-ink);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--fg-accent) 8%, transparent) 0%,
    transparent 100%
  );
  border-left: 3px solid var(--fg-accent);
  border-radius: var(--fg-radius-control);
  transition: all 0.3s ease;
  position: relative;
  overflow: hidden;
}

.path-step::before {
  content: '→';
  position: absolute;
  left: 8px;
  opacity: 0;
  transform: translateX(-8px);
  transition: all 0.3s ease;
  color: var(--fg-accent);
  font-weight: bold;
}

.path-step:hover {
  padding-left: 24px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--fg-accent) 12%, transparent) 0%,
    transparent 100%
  );
  border-left-width: 4px;
}

.path-step:hover::before {
  opacity: 1;
  transform: translateX(0);
}

.degraded {
  color: var(--fg-ink-faint);
  font-size: 12px;
}

.panel-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid color-mix(in srgb, var(--fg-line) 50%, transparent);
}

/* 动效降级支持 */
@media (prefers-reduced-motion: reduce) {
  .relation-panel,
  .field,
  .path-step {
    animation: none !important;
    transition: none !important;
  }

  .field:hover,
  .path-step:hover {
    transform: none;
  }

  .path-step::before {
    display: none;
  }
}
</style>
