<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NForm, NFormItem, NInput, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import ChangePinForm from '@/components/common/ChangePinForm.vue'
import DataRightsPanel from '@/components/member/DataRightsPanel.vue'
import DisclosureMatrix from '@/components/member/DisclosureMatrix.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { themeTokens, type ThemeName, type ThemeTokens } from '@/styles/tokens'

/**
 * 设置页（v2）：改名 / 改 PIN / 登出 + 披露偏好矩阵（§0.1）+
 * 我的数据（F-5：导出/更正/删除/争议）。
 * P5：主题切换入口落位——双主题预览小卡消费 stores/ui.setTheme
 * （持久化与 data-theme 切换由 store 承担，刷新后保持，见 stores/ui.ts）。
 * 预览色取自 tokens.ts 的 L2 token（token 单一来源，不写死色值）。
 */
const auth = useAuthStore()
const router = useRouter()
const message = useMessage()
const ui = useUiStore()

const nameForm = reactive({ name: auth.user?.name ?? '' })
const savingName = ref(false)
const nameError = ref('')

async function saveName(): Promise<void> {
  if (!nameForm.name.trim()) {
    nameError.value = '名字不能为空'
    return
  }
  savingName.value = true
  nameError.value = ''
  try {
    await auth.updateName(nameForm.name.trim())
    message.success('名字已更新')
  } catch (error) {
    nameError.value = error instanceof ApiError ? error.message : '保存失败，请稍后重试'
  } finally {
    savingName.value = false
  }
}

async function doLogout(): Promise<void> {
  await auth.logout()
  message.success('已退出登录')
  void router.replace({ name: 'login' })
}

// ---- 主题选择（双主题预览小卡） ----
const themeChoices: Array<{ name: ThemeName; tokens: ThemeTokens; desc: string }> = [
  { name: 'paper', tokens: themeTokens.paper, desc: '宣纸点阵 · 宋体标题 · 朱砂点睛' },
  { name: 'modern', tokens: themeTokens.modern, desc: '纯白留白 · 无衬线 · 青蓝点缀' },
]

/** 主题缩略预览：底色 + 点阵 + 主色样，全部取自该主题 L2 token */
function previewStyle(tokens: ThemeTokens): Record<string, string> {
  return {
    backgroundColor: tokens.vars['surface'],
    backgroundImage: `radial-gradient(circle, ${tokens.vars['dot']} 1px, transparent 1px)`,
    backgroundSize: '8px 8px',
  }
}
</script>

<template>
  <main class="settings-view">
    <NCard class="card" data-test="settings-card">
      <template #header>
        <div class="title-row">
          <NButton text data-test="settings-back" @click="router.push({ name: 'family-space' })">
            ← 家庭空间
          </NButton>
          <span class="card-title">设置</span>
        </div>
      </template>
      <template #header-extra>
        <div class="header">
          <NButton text type="primary" data-test="go-memory" @click="router.push('/memory')">
            记忆与知识
          </NButton>
          <NButton text type="error" data-test="logout-btn" @click="doLogout">退出登录</NButton>
        </div>
      </template>

      <section class="section">
        <h2 class="section-title">当前账号</h2>
        <p class="meta" data-test="current-user">
          {{ auth.user?.name }}<template v-if="auth.isPlatformOperator">（平台运营者）</template>
        </p>
      </section>

      <section class="section" data-test="theme-section">
        <h2 class="section-title">外观主题</h2>
        <div class="theme-cards" role="group" aria-label="选择配色主题（即时生效并记住偏好）">
          <button
            v-for="t in themeChoices"
            :key="t.name"
            type="button"
            class="theme-card"
            :class="{ 'is-active': ui.theme === t.name }"
            :aria-pressed="ui.theme === t.name"
            :data-test="`theme-card-${t.name}`"
            @click="ui.setTheme(t.name)"
          >
            <span class="theme-preview" :style="previewStyle(t.tokens)" aria-hidden="true">
              <span
                class="preview-title"
                :style="{ fontFamily: t.tokens.vars['font-display'], color: t.tokens.vars['ink'] }"
              >
                家谱
              </span>
              <span class="preview-accent" :style="{ background: t.tokens.vars['accent'] }" />
            </span>
            <span class="theme-name" :style="{ fontFamily: t.tokens.vars['font-display'] }">
              {{ t.tokens.label }}
              <span v-if="ui.theme === t.name" class="theme-check" aria-hidden="true">✓</span>
            </span>
            <span class="theme-desc">{{ t.desc }}</span>
          </button>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">修改名字</h2>
        <NForm inline :show-feedback="false" @submit.prevent="saveName">
          <NFormItem label="新名字" :label-props="{ for: 'settings-name-input' }">
            <NInput
              v-model:value="nameForm.name"
              :input-props="{ id: 'settings-name-input' }"
              data-test="name-input"
            />
          </NFormItem>
          <NFormItem>
            <NButton type="primary" :loading="savingName" data-test="name-save" @click="saveName">
              保存
            </NButton>
          </NFormItem>
        </NForm>
        <p v-if="nameError" class="error" data-test="name-error">{{ nameError }}</p>
      </section>

      <section class="section">
        <h2 class="section-title">修改 PIN 码</h2>
        <ChangePinForm />
      </section>

      <section class="section" data-test="memory-entry-section">
        <h2 class="section-title">长期知识</h2>
        <p class="meta">管理待确认记忆、共享范围和可追溯的知识引用。</p>
        <NButton type="primary" secondary data-test="go-memory" @click="router.push('/memory')">
          打开记忆与知识
        </NButton>
      </section>

      <section class="section" data-test="disclosure-section">
        <h2 class="section-title">披露偏好</h2>
        <DisclosureMatrix />
      </section>

      <section class="section" data-test="data-rights-section">
        <h2 class="section-title">我的数据</h2>
        <DataRightsPanel />
      </section>
    </NCard>
  </main>
</template>

<style scoped>
.settings-view {
  display: flex;
  justify-content: center;
  padding: 24px 16px 40px;
}

.card {
  width: min(640px, 100%);
}

.card-title {
  font-family: var(--fg-font-display);
  font-size: 18px;
  color: var(--fg-ink);
}

.title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section {
  margin-bottom: 28px;
}

.section-title {
  margin: 0 0 12px;
  font-size: 15px;
  color: var(--fg-ink);
}

.meta {
  margin: 0;
  color: var(--fg-ink-secondary);
}

/* 双主题预览小卡：选中态主色描边 + 对勾（design.md §2.3 双主题气质缩影） */
.theme-cards {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.theme-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px;
  text-align: left;
  cursor: pointer;
  background: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  font: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.theme-card:hover {
  border-color: var(--fg-line-strong);
  box-shadow: var(--fg-shadow-card);
}

.theme-card.is-active {
  border-color: var(--fg-accent);
  box-shadow: 0 0 0 1px var(--fg-accent), var(--fg-shadow-card);
}

.theme-card:focus-visible {
  outline: 2px solid var(--fg-accent);
  outline-offset: 2px;
}

.theme-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 56px;
  border: 1px solid var(--fg-line);
  border-radius: calc(var(--fg-radius-card) - 2px);
}

.preview-title {
  font-size: 15px;
  font-weight: 700;
}

.preview-accent {
  width: 12px;
  height: 12px;
  border-radius: 50%;
}

.theme-name {
  margin-top: 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--fg-ink);
}

.theme-check {
  color: var(--fg-accent);
}

.theme-desc {
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.error {
  margin: 8px 0 0;
  color: var(--fg-status-disputed);
  font-size: 13px;
}

@media (max-width: 480px) {
  .theme-cards {
    grid-template-columns: 1fr;
  }
}
</style>
