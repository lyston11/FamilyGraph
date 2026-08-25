<script setup lang="ts">
import { ElMessage } from 'element-plus'

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

async function copyPin(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.pin)
    ElMessage.success('已复制，请粘贴到安全的地方保存')
  } catch {
    ElMessage.warning('复制失败，请手动抄写')
  }
}

function done(): void {
  emit('close')
}
</script>

<template>
  <el-dialog
    :model-value="true"
    title="请立即保存 PIN 码"
    width="420px"
    align-center
    :close-on-click-modal="false"
    data-test="one-time-pin-dialog"
    @update:model-value="done()"
  >
    <el-alert type="warning" :closable="false" show-icon class="warn">
      该 PIN 码仅显示这一次，关闭后无法再查看。请截图或抄写保存后交给 {{ memberName }}。
    </el-alert>
    <div class="pin-display" data-test="one-time-pin">{{ pin }}</div>
    <p class="hint">首次登录：{{ memberName }} 使用名字 + 此 PIN 登录后需立即修改 PIN 完成认领。</p>
    <template #footer>
      <el-button data-test="pin-copy" @click="copyPin">复制</el-button>
      <el-button type="primary" data-test="pin-done" @click="done">我已保存，关闭</el-button>
    </template>
  </el-dialog>
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
  font-family: monospace;
  font-weight: 700;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  user-select: all;
}

.hint {
  margin-top: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
