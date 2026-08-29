<script setup lang="ts">
import { NAlert, NButton, NModal, useMessage } from 'naive-ui'

/**
 * 一次性 PIN 弹窗（A3/AD-1）：大字号展示 + 复制 + 「截图保存」警告。
 * 由父组件以 v-if 控制挂载：close 后父组件清空 PIN 内存态并卸载本组件，
 * 任何界面不可回看。
 */
const props = defineProps<{
  pin: string
  memberName: string
}>()

const emit = defineEmits<{ close: [] }>()

const message = useMessage()

async function copyPin(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.pin)
    message.success('已复制，请粘贴到安全的地方保存')
  } catch {
    message.warning('复制失败，请手动抄写')
  }
}

function done(): void {
  emit('close')
}

function onModalShowChange(show: boolean): void {
  if (!show) done()
}
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    title="请立即保存 PIN 码"
    :mask-closable="false"
    data-test="one-time-pin-dialog"
    @update:show="onModalShowChange"
  >
    <NAlert type="warning" :show-icon="true" class="warn">
      该 PIN 码仅显示这一次，关闭后无法再查看。请截图或抄写保存后交给 {{ memberName }}。
    </NAlert>
    <!-- 凭证卡：大号等宽数字 + 票据式虚线边（纸墨=票据/印感，清雅=白卡圆角） -->
    <div class="pin-display" data-test="one-time-pin">{{ pin }}</div>
    <p class="hint">首次登录：{{ memberName }} 使用名字 + 此 PIN 登录后需立即修改 PIN 完成认领。</p>
    <template #footer>
      <div class="footer-actions">
        <NButton data-test="pin-copy" @click="copyPin">复制</NButton>
        <NButton type="primary" data-test="pin-done" @click="done">我已保存，关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.warn {
  margin-bottom: 16px;
}

.pin-display {
  padding: 20px;
  text-align: center;
  font-size: 44px;
  letter-spacing: 12px;
  text-indent: 12px; /* 抵消末字符字距，保证视觉居中 */
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 700;
  color: var(--fg-ink);
  background-color: var(--fg-surface-sunken);
  border: 1px dashed color-mix(in srgb, var(--fg-status-proposed) 45%, transparent);
  border-radius: var(--fg-radius-card);
  user-select: all;
}

.hint {
  margin-top: 12px;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='one-time-pin-dialog'] {
  width: min(420px, calc(100vw - 48px));
}
</style>
