<script setup lang="ts">
import { useRouter } from 'vue-router'

import ChangePinForm from '@/components/common/ChangePinForm.vue'

/**
 * 首登强制改 PIN 页（沉浸页，meta.chrome='blank'；pin_must_change=true 时的唯一可停留页面）。
 * 改毕服务端使全部会话失效 → 回登录页。白名单路由逻辑在守卫内，本页不感知。
 */
const router = useRouter()

function onChanged(): void {
  void router.replace({ name: 'login' })
}
</script>

<template>
  <main class="force-change-pin-view">
    <section class="plate" data-test="force-change-pin-card">
      <div class="brand" aria-hidden="true">
        <span class="seal">谱</span>
      </div>
      <h1 class="title">请先修改初始 PIN 码</h1>
      <p class="desc">首次登录必须设置你自己的 PIN 码后才能继续使用系统。</p>
      <ChangePinForm @changed="onChanged" />
    </section>
  </main>
</template>

<style scoped>
.force-change-pin-view {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
}

/* 与登录/引导页同族的"名牌"卡基座（token 驱动双主题观感） */
.plate {
  position: relative;
  width: min(400px, 100%);
  padding: 36px 36px 32px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-raised);
  box-sizing: border-box;
}

.plate::before {
  content: '';
  position: absolute;
  inset: 6px;
  border: 1px solid var(--fg-line);
  border-radius: calc(var(--fg-radius-card) - 2px);
  pointer-events: none;
}

[data-theme='modern'] .plate::before {
  display: none;
}

.brand {
  display: flex;
  justify-content: center;
}

.seal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--fg-radius-control);
  background-color: var(--fg-accent);
  color: var(--fg-accent-ink);
  font-family: var(--fg-font-display);
  font-size: 22px;
  font-weight: 700;
  box-shadow: var(--fg-shadow-card);
}

.title {
  margin: 12px 0 8px;
  text-align: center;
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--fg-ink);
}

.desc {
  margin: 0 0 20px;
  text-align: center;
  font-size: 13px;
  line-height: 1.6;
  color: var(--fg-ink-secondary);
}
</style>
