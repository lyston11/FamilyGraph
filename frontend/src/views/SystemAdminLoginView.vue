<script setup lang="ts">
// 系统管理员登录入口占位（PRD §2.7 / design.md §6，09-01 Phase 6）：
// - 本任务只固定前端隔离边界：独立路由、blank chrome（不套家庭壳也不套后台壳）、
//   与家庭登录页完全分离；家庭导航与家庭登录页均不提供此入口的显眼跳转；
// - 真实的系统管理员登录流程、PIN 首改与后台壳由 09-01-system-admin-governance-routes
//   任务实现，本页不渲染任何登录表单（不实现真实登录流程）。
</script>

<template>
  <main class="system-admin-login" data-test="system-admin-login-view">
    <section class="plate" aria-labelledby="system-admin-login-title">
      <p class="eyebrow">SYSTEM ADMIN</p>
      <h1 id="system-admin-login-title">系统管理员入口</h1>
      <p class="hint">
        系统管理员使用独立主体（principal_type=system_admin）登录，与家庭用户会话互斥。
        登录流程由系统管理员治理任务提供，此处为路由隔离占位页。
      </p>
    </section>
  </main>
</template>

<style scoped>
.system-admin-login {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
}

.plate {
  width: min(420px, 100%);
  padding: 36px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-raised);
  box-sizing: border-box;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--fg-accent);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

h1 {
  margin: 0 0 12px;
  font-family: var(--fg-font-display);
  font-size: 24px;
  color: var(--fg-ink);
}

.hint {
  margin: 0;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.7;
}
</style>
