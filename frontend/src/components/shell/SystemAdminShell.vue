<template>
  <div class="system-admin-shell">
    <header class="system-admin-header">
      <RouterLink class="system-admin-brand" :to="{ name: 'system-admin' }">FamilyGraph · 系统治理</RouterLink>
      <nav aria-label="系统管理导航">
        <RouterLink :to="{ name: 'system-admin' }">治理概览</RouterLink>
      </nav>
    </header>
    <main><RouterView /></main>
  </div>
</template>

<style scoped>
.system-admin-shell { min-height: 100vh; background: var(--fg-surface); color: var(--fg-ink); }
.system-admin-header { display: flex; align-items: center; gap: 28px; min-height: 64px; padding: 0 28px; border-bottom: 1px solid var(--fg-line); background: var(--fg-surface-raised); }
.system-admin-brand { color: var(--fg-ink); font-family: var(--fg-font-display); font-weight: 700; text-decoration: none; }
nav a { color: var(--fg-ink-secondary); font-size: 13px; text-decoration: none; }
nav a.router-link-active { color: var(--fg-accent); }
main { min-width: 0; }
</style>
