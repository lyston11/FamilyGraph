<script setup lang="ts">
/**
 * 首启引导页（沉浸页，meta.chrome='blank'）：数据库尚无任何家庭账号时显示。
 * 09-04 起系统初始化由部署方完成，家庭端不再提供任何创建/初始化表单；
 * 本页保持静态说明性质，不暴露任何其他产品面信息。
 * 09-05 决策 17（冷启动）：零账号文案从「通过开通渠道获取账号」改为注册引导——
 * 首个注册者即第一个用户；注册开关关闭时隐藏注册入口，仅保留登录指引。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const canRegister = computed(() => auth.registrationEnabled)
</script>

<template>
  <main class="onboarding-view">
    <section class="plate" data-test="onboarding-card">
      <div class="brand" aria-hidden="true">
        <span class="seal">谱</span>
      </div>

      <h1 class="title">欢迎使用 FamilyGraph</h1>
      <p v-if="canRegister" class="desc" data-test="onboarding-desc">
        系统尚未开通任何家庭账号。你可以注册第一个账号，从建立你的家庭档案开始。
      </p>
      <p v-else class="desc" data-test="onboarding-desc">系统尚未开通任何家庭账号。</p>

      <div class="actions">
        <button
          v-if="canRegister"
          class="register-btn"
          type="button"
          data-test="onboarding-to-register"
          @click="() => router.push({ name: 'register' })"
        >
          注册新账号
        </button>
        <button
          class="login-btn"
          type="button"
          data-test="onboarding-to-login"
          @click="() => router.replace({ name: 'login' })"
        >
          前往登录
        </button>
      </div>
    </section>
  </main>
</template>

<style scoped>
.onboarding-view {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
}

/* 与登录页同族的"名牌"卡基座（token 驱动双主题观感） */
.plate {
  position: relative;
  width: min(440px, 100%);
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

.actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
}

.register-btn {
  width: 100%;
  max-width: 240px;
  padding: 10px 24px;
  border: 1px solid var(--fg-accent);
  border-radius: var(--fg-radius-control);
  background-color: var(--fg-accent);
  color: var(--fg-accent-ink);
  font-size: 14px;
  cursor: pointer;
}

.register-btn:hover {
  background-color: var(--fg-accent-hover);
  border-color: var(--fg-accent-hover);
}

.login-btn {
  width: 100%;
  max-width: 240px;
  padding: 10px 24px;
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-control);
  background-color: var(--fg-surface-raised);
  color: var(--fg-ink);
  font-size: 14px;
  cursor: pointer;
}

.login-btn:hover {
  background-color: var(--fg-surface-sunken);
}
</style>
