<script setup lang="ts">
/**
 * 普通 404 页：所有未注册路径（含 /system-admin、/admin 等深链）的统一归宿。
 * 只说明"页面不存在"，不跳转、不提示任何其他产品面（09-04 家庭端零后台痕迹）。
 */
import { useRouter } from 'vue-router'

const router = useRouter()

function goHome(): void {
  void router.replace({ name: 'home' })
}
</script>

<template>
  <main class="not-found-view">
    <section class="plate" data-test="not-found-card">
      <div class="brand" aria-hidden="true">
        <span class="seal">谱</span>
      </div>
      <h1 class="title">页面不存在</h1>
      <p class="desc">你访问的地址不存在或已被移除。</p>
      <button class="home-btn" type="button" data-test="not-found-home" @click="goHome">
        返回首页
      </button>
    </section>
  </main>
</template>

<style scoped>
.not-found-view {
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
  text-align: center;
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
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--fg-ink);
}

.desc {
  margin: 0 0 20px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--fg-ink-secondary);
}

.home-btn {
  padding: 10px 24px;
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-control);
  background-color: var(--fg-surface-raised);
  color: var(--fg-ink);
  font-size: 14px;
  cursor: pointer;
}

.home-btn:hover {
  background-color: var(--fg-surface-sunken);
}
</style>
