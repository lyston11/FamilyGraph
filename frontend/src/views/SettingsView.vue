<script setup lang="ts">
// 设置页五分区（09-01 Phase 5 四分区 + 09-05 新增邀请码分区）：
// 1) 个人资料（auth store 本人信息 + 改名）；2) 隐私与公示（DisclosureMatrix）；
// 3) 邀请码（InviteCodeSection：我的码/创建三类/撤销/复制链接/填码加入/绑定确认）；
// 4) 账号与安全（ChangePinForm + 我的数据 DataRightsPanel + 登出）；
// 5) 显示与无障碍（paper/modern 双主题切换，消费 stores/ui.setTheme）。
// 复用现有 ChangePinForm / DisclosureMatrix / DataRightsPanel；空间管理不放进
// 全局设置（由 AppShell 当前空间管理入口承担）；无新增授权行为。
// ProfileDrawer 依赖旧 /users members 合同，不进入全局设置（差异记录见 notes.md）。
import {
  NButton,
  NCard,
  NDatePicker,
  NForm,
  NFormItem,
  NInput,
  NRadio,
  NRadioGroup,
  useMessage,
} from 'naive-ui'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/errors'
import { fetchMember, updateMember } from '@/api/members'
import ChangePinForm from '@/components/common/ChangePinForm.vue'
import DataRightsPanel from '@/components/member/DataRightsPanel.vue'
import DisclosureMatrix from '@/components/member/DisclosureMatrix.vue'
import InviteCodeSection from '@/components/member/InviteCodeSection.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { themeTokens, type ThemeName, type ThemeTokens } from '@/styles/tokens'
import type { GenderType, StructuredDate } from '@/types/api'

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

// ---- 基础资料编辑（09-05 PRD R2）：PATCH /members/{self}，本人自助 ----
const genderOptions: Array<{ value: GenderType; label: string }> = [
  { value: 'm', label: '男' },
  { value: 'f', label: '女' },
  { value: 'unknown', label: '不详' },
]

const profileForm = reactive({
  gender: 'unknown' as GenderType,
  birthTs: null as number | null,
  deathTs: null as number | null,
  bio: '',
})
const savingProfile = ref(false)

/** StructuredDate.date('YYYY-MM-DD') ⇄ NDatePicker 时间戳（本地时区日界足够） */
function structuredToTs(sd: StructuredDate | null): number | null {
  if (!sd?.date) return null
  const [y, m, d] = sd.date.split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d).getTime()
}

function tsToStructured(ts: number | null): StructuredDate | null {
  if (ts === null) return null
  const dt = new Date(ts)
  const month = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  return { cal_type: 'solar', date: `${dt.getFullYear()}-${month}-${day}` }
}

onMounted(async () => {
  const selfId = auth.user?.id
  if (!selfId) return
  try {
    const member = await fetchMember(selfId)
    profileForm.gender = member.gender ?? 'unknown'
    profileForm.birthTs = structuredToTs(member.birth)
    profileForm.deathTs = structuredToTs(member.death)
    profileForm.bio = member.bio ?? ''
  } catch {
    // 资料回填失败不打断设置页；保存时仍可提交
  }
})

async function saveProfile(): Promise<void> {
  const selfId = auth.user?.id
  if (!selfId) return
  savingProfile.value = true
  try {
    const bio = profileForm.bio.trim()
    await updateMember(selfId, {
      gender: profileForm.gender,
      birth: tsToStructured(profileForm.birthTs),
      death: tsToStructured(profileForm.deathTs),
      bio: bio === '' ? null : bio,
    })
    message.success('资料已更新')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    savingProfile.value = false
  }
}

/** 登出：沿用 auth store 全量清理（含敏感缓存）后回登录页 */
async function doLogout(): Promise<void> {
  await auth.logout()
  message.success('已退出登录')
  void router.replace({ name: 'login' })
}

// ---- 显示与无障碍：主题选择（双主题预览小卡，预览色取自 tokens.ts L2 token） ----
const themeChoices: Array<{ name: ThemeName; tokens: ThemeTokens; desc: string }> = [
  { name: 'paper', tokens: themeTokens.paper, desc: '宣纸点阵 · 宋体标题 · 朱砂点睛' },
  { name: 'modern', tokens: themeTokens.modern, desc: '纯白留白 · 无衬线 · 青蓝点缀' },
]

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
          <NButton text data-test="settings-back" @click="router.push({ name: 'home' })">
            ← 返回我的家庭
          </NButton>
          <span class="card-title">设置</span>
        </div>
      </template>

      <!-- 分区 1：个人资料 -->
      <section class="section" data-test="settings-section-profile">
        <h2 class="section-title">个人资料</h2>
        <p class="meta" data-test="current-user">
          {{ auth.user?.name }}
        </p>
        <p class="meta" data-test="profile-status">
          档案状态：{{ auth.user?.profile_status === 'identity_confirmed' ? '已确档' : '待确档' }}
        </p>
        <NForm inline :show-feedback="false" @submit.prevent="saveName">
          <NFormItem label="修改名字" :label-props="{ for: 'settings-name-input' }">
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

        <!-- 基础资料编辑（09-05 PRD R2）：PATCH /members/{self} -->
        <NForm label-placement="top" class="profile-form" @submit.prevent="saveProfile">
          <NFormItem label="性别" data-test="profile-gender-item">
            <NRadioGroup v-model:value="profileForm.gender" data-test="profile-gender">
              <NRadio v-for="option in genderOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </NRadio>
            </NRadioGroup>
          </NFormItem>
          <NFormItem label="出生日期" data-test="profile-birth-item">
            <NDatePicker
              v-model:value="profileForm.birthTs"
              type="date"
              clearable
              placeholder="选择出生日期"
              data-test="profile-birth"
            />
          </NFormItem>
          <NFormItem label="逝世日期（可选）" data-test="profile-death-item">
            <NDatePicker
              v-model:value="profileForm.deathTs"
              type="date"
              clearable
              placeholder="选择逝世日期"
              data-test="profile-death"
            />
          </NFormItem>
          <NFormItem label="简介" data-test="profile-bio-item">
            <NInput
              v-model:value="profileForm.bio"
              type="textarea"
              :maxlength="2000"
              show-count
              placeholder="介绍一下自己（对空间内成员按你的披露设置可见）"
              data-test="profile-bio"
            />
          </NFormItem>
          <NFormItem>
            <NButton
              type="primary"
              :loading="savingProfile"
              data-test="profile-save"
              @click="saveProfile"
            >
              保存资料
            </NButton>
          </NFormItem>
        </NForm>
      </section>

      <!-- 分区 2：隐私与公示 -->
      <section class="section" data-test="settings-section-privacy">
        <h2 class="section-title">隐私与公示</h2>
        <p class="meta">控制你的资料在家族空间中的披露范围；高敏感类别始终受最小披露保护。</p>
        <DisclosureMatrix />
      </section>

      <!-- 分区 3：邀请码（09-05 决策 14；provisional 用户由区块内提示，后端 403 兜底） -->
      <section class="section" data-test="settings-section-invite">
        <h2 class="section-title">邀请码</h2>
        <p class="meta">创建邀请码分享给家人朋友；或凭收到的邀请码加入已有空间。</p>
        <InviteCodeSection />
      </section>

      <!-- 分区 4：账号与安全 -->
      <section class="section" data-test="settings-section-account">
        <h2 class="section-title">账号与安全</h2>
        <p class="meta">定期更换 PIN 码；导出、更正与删除申请都在这里提交。</p>
        <ChangePinForm />
        <div class="data-rights" data-test="data-rights-section">
          <DataRightsPanel />
        </div>
        <NButton text type="error" data-test="logout-btn" @click="doLogout">退出登录</NButton>
      </section>

      <!-- 分区 5：显示与无障碍 -->
      <section class="section" data-test="settings-section-display">
        <h2 class="section-title">显示与无障碍</h2>
        <p class="meta">选择配色主题（即时生效并记住偏好）；双主题均遵循系统减弱动态设置。</p>
        <div class="theme-cards" role="group" aria-label="选择配色主题（即时生效并记住偏好）" data-test="theme-section">
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

.section {
  margin-bottom: 28px;
  padding: 24px;
  background: var(--fg-glass-surface);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.5);
  box-shadow:
    0 4px 20px color-mix(in srgb, var(--fg-ink) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 20%, transparent);
}

.section-title {
  margin: 0 0 12px;
  font-size: 15px;
  color: var(--fg-ink);
}

.meta {
  margin: 0 0 12px;
  color: var(--fg-ink-secondary);
}

.data-rights {
  margin: 16px 0;
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

/* 移动端（≤600px）：inline 表单降级为纵向堆叠（375px 单列，无横向滚动）；
   表格类内容（披露矩阵/数据权利表）由组件内 scroll-x 承担横向滚动 */
@media (max-width: 600px) {
  .settings-view :deep(.n-form--inline .n-form-item) {
    width: 100%;
    margin-right: 0;
  }

  .settings-view :deep(.n-form--inline .n-form-item-blank) {
    flex: 1;
  }
}
</style>
