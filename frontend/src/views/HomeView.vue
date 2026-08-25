<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/** 首页占位：M1 将替换为家庭空间聚合视图（AD-3） */
const auth = useAuthStore()
const router = useRouter()

function goSettings(): void {
  void router.push({ name: 'settings' })
}
</script>

<template>
  <main class="home-view">
    <el-card class="card" data-test="home-card">
      <h1 class="title">你好，{{ auth.user?.name }}</h1>
      <p class="desc">家庭空间将在 M1 里程碑开放，当前可前往设置页维护账号信息。</p>
      <el-button type="primary" data-test="go-settings" @click="goSettings">打开设置</el-button>
    </el-card>
  </main>
</template>

<style scoped>
.home-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}

.card {
  width: 400px;
}

.title {
  margin: 0 0 8px;
  font-size: 22px;
}

.desc {
  color: var(--el-text-color-secondary);
}
</style>
