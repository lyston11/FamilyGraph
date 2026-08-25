<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchStats, type StatsPayload } from '@/api/stats'

/** 家族统计页（m3c）：总人数/男女比例/世代分布/本月生日。范围=服务端可见性过滤。 */
const stats = ref<StatsPayload | null>(null)
const loading = ref(false)

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
</script>

<template>
  <main class="stats-view" v-loading="loading">
    <h2 class="title">家族统计</h2>

    <template v-if="stats">
      <section class="cards" data-test="stats-cards">
        <el-card class="num-card">
          <div class="num">{{ stats.total }}</div>
          <div class="label">总人数</div>
        </el-card>
        <el-card class="num-card">
          <div class="num">{{ genderRows.map((r) => `${r.label} ${r.count}`).join(' · ') }}</div>
          <div class="label">男女比例</div>
        </el-card>
        <el-card class="num-card">
          <div class="num">{{ stats.birthdays_this_month.length }}</div>
          <div class="label">本月生日</div>
        </el-card>
      </section>

      <section class="histogram" data-test="generation-histogram">
        <h3>世代分布（按出生年份，20 岁一档）</h3>
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
        <h3>本月生日</h3>
        <ul>
          <li v-for="b in stats.birthdays_this_month" :key="b.id">
            🎂 {{ b.name }}（{{ b.date }}）
          </li>
          <li v-if="stats.birthdays_this_month.length === 0" class="hint">本月没有寿星</li>
        </ul>
      </section>
    </template>
    <el-empty v-else-if="!loading" description="统计数据加载失败" />
  </main>
</template>

<style scoped>
.stats-view {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px;
}

.title {
  font-size: 20px;
  margin-bottom: 16px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.num {
  font-size: 22px;
  font-weight: 600;
}

.label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
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
}

.bar-track {
  flex: 1;
  height: 14px;
  background: var(--el-fill-color-light);
  border-radius: 7px;
  overflow: hidden;
}

.bar {
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 7px;
  transition: width 0.4s ease;
}

.count {
  width: 32px;
  font-size: 13px;
}

.birthdays ul {
  list-style: none;
  padding: 0;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
