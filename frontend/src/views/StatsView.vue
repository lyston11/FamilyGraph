<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NEmpty, NSpin } from 'naive-ui'

import { fetchStats, type StatsPayload } from '@/api/stats'

/**
 * 家族统计页（m3c）：总人数/男女比例/世代分布/本月生日。范围=服务端可见性过滤。
 * 门面自绘（design.md §2.1）：数字立牌走 --fg-* token，P5 随全站迁 naive-ui。
 */
const stats = ref<StatsPayload | null>(null)
const loading = ref(false)
const router = useRouter()

onMounted(async () => {
  loading.value = true
  try {
    stats.value = await fetchStats()
  } catch {
    stats.value = null
  } finally {
    loading.value = false
  }
})

const maxBucket = computed(() =>
  Math.max(1, ...(stats.value?.generation_histogram.map((h) => h.count) ?? [1])),
)

const genderRows = computed(() => {
  const g = stats.value?.by_gender
  if (!g) return []
  return [
    { label: '男', count: g.m },
    { label: '女', count: g.f },
    { label: '不详', count: g.unknown },
  ].filter((r) => r.count > 0)
})

const genderText = computed(() => genderRows.value.map((r) => `${r.label} ${r.count}`).join(' · '))
</script>

<template>
  <NSpin :show="loading">
    <main class="stats-view">
        <header class="title-row">
          <NButton text data-test="stats-back" @click="router.push({ name: 'family-space' })">
            ← 家庭空间
          </NButton>
          <h2 class="title">家族统计</h2>
        </header>

      <template v-if="stats">
        <section class="cards" data-test="stats-cards">
          <div class="num-card">
            <div class="num">{{ stats.total }}</div>
            <div class="label">总人数</div>
          </div>
          <div class="num-card">
            <div class="num num--text">{{ genderText || '—' }}</div>
            <div class="label">男女比例</div>
          </div>
          <div class="num-card">
            <div class="num">{{ stats.birthdays_this_month.length }}</div>
            <div class="label">本月生日</div>
          </div>
        </section>

        <section class="histogram" data-test="generation-histogram">
          <h3 class="block-title">世代分布（按出生年份，20 岁一档）</h3>
          <div v-for="row in stats.generation_histogram" :key="row.bucket" class="bar-row">
            <span class="bucket">{{ row.bucket }}~{{ row.bucket + 19 }} 后</span>
            <div class="bar-track">
              <div class="bar" :style="{ width: `${(row.count / maxBucket) * 100}%` }" />
            </div>
            <span class="count">{{ row.count }}</span>
          </div>
          <p v-if="stats.generation_histogram.length === 0" class="hint">
            暂无带生日的成员数据
          </p>
        </section>

        <section class="birthdays" data-test="birthdays-list">
          <h3 class="block-title">本月生日</h3>
          <ul>
            <li v-for="b in stats.birthdays_this_month" :key="b.id" class="birthday-row">
              <span class="birthday-dot" aria-hidden="true">寿</span>
              {{ b.name }}（{{ b.date }}）
            </li>
            <li v-if="stats.birthdays_this_month.length === 0" class="hint">本月没有寿星</li>
          </ul>
        </section>
      </template>
      <NEmpty v-else-if="!loading" class="empty" description="统计数据加载失败" />
    </main>
  </NSpin>
</template>

<style scoped>
.stats-view {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px;
}

.title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 20px;
  color: var(--fg-ink);
}

/* 数字立牌：纸感卡面 + 显示字体数字（design.md §2.3） */
.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.num-card {
  padding: 16px;
  background: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
}

.num {
  font-family: var(--fg-font-display);
  font-size: 28px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--fg-accent);
}

/* 文本值（男女比例串）比纯数字降一档，避免换行拥挤 */
.num--text {
  font-size: 18px;
  letter-spacing: 0.02em;
}

.label {
  margin-top: 4px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.block-title {
  margin: 0 0 12px;
  font-family: var(--fg-font-display);
  font-size: 15px;
  color: var(--fg-ink);
}

.histogram {
  margin-bottom: 24px;
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.bucket {
  width: 96px;
  font-size: 13px;
  text-align: right;
  color: var(--fg-ink-secondary);
}

.bar-track {
  flex: 1;
  height: 14px;
  background: var(--fg-surface-sunken);
  border-radius: 7px;
  overflow: hidden;
}

.bar {
  height: 100%;
  background: var(--fg-accent);
  border-radius: 7px;
  transition: width 0.4s ease;
}

.count {
  width: 32px;
  font-size: 13px;
  color: var(--fg-ink);
}

.birthdays ul {
  list-style: none;
  padding: 0;
}

.birthday-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 14px;
  color: var(--fg-ink);
  border-bottom: 1px solid var(--fg-line);
}

/* 生日行首字章：纸墨印章隐喻的轻量变体 */
.birthday-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 11px;
  color: var(--fg-accent-ink);
  background: var(--fg-accent);
  border-radius: 50%;
}

.hint {
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

.empty {
  margin: 48px 0;
}
</style>
