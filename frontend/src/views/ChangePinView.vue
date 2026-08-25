<script setup lang="ts">
import { useRouter } from 'vue-router'

import ChangePinForm from '@/components/common/ChangePinForm.vue'

/**
 * 首登强制改 PIN 页（pin_must_change=true 时的唯一可停留页面）。
 * 改毕服务端使全部会话失效 → 回登录页。
 */
const router = useRouter()

function onChanged(): void {
  void router.replace({ name: 'login' })
}
</script>

<template>
  <main class="force-change-pin-view">
    <el-card class="card" data-test="force-change-pin-card">
      <h1 class="title">请先修改初始 PIN 码</h1>
      <p class="desc">首次登录必须设置你自己的 PIN 码后才能继续使用系统。</p>
      <ChangePinForm @changed="onChanged" />
    </el-card>
  </main>
</template>

<style scoped>
.force-change-pin-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}

.card {
  width: 380px;
}

.title {
  margin: 0 0 8px;
  font-size: 20px;
  text-align: center;
}

.desc {
  color: var(--el-text-color-secondary);
}
</style>
