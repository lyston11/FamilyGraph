<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NInput, NSelect, NSpace, NTag, useMessage } from 'naive-ui'

import {
  confirmContentVersion,
  createContentVersion,
  createGrant,
  listContentVersions,
  listGrants,
  revokeGrant,
  type AgentGrantOut,
  type ContentVersionOut,
} from '@/api/attachments'
import { useSpacesStore } from '@/stores/spaces'

/**
 * 附件供助手使用：内容确认 + 空间授权 + 撤销。
 *
 * 三个动作**互不蕴含**，这是本组件的核心语义，不要合并成「一个开关」：
 *
 * 1. 保存文字 → 产生 `draft` 内容版本（不可变，改文字 = 新 revision）；
 * 2. 确认文字 → `confirmed`。这只是「这段文字是对的」；
 * 3. 授权给家庭空间 → 助手才能在**该空间**检索到它。这只是「允许模型用」。
 *
 * 「确认」不等于「授权」：用户可能确认文字后发现里面有不想给模型看的内容。
 * 因此 UI 必须让两步各自可见、各自可撤销。
 *
 * 授权**不提升任何人的浏览权限**：能检索到 ≠ 能打开原图。撤销后后续检索与
 * 引用立即失效（服务端先打墓碑再异步清理）。
 */
const props = defineProps<{ attachmentId: number; canEdit: boolean }>()

const message = useMessage()
const spaces = useSpacesStore()

const versions = ref<ContentVersionOut[]>([])
const grants = ref<AgentGrantOut[]>([])
const draftText = ref('')
const busy = ref(false)

/** 只允许授权给家庭空间：lineage 的可见性形状与本合同的 household 过滤不同构。 */
const householdOptions = ref<{ label: string; value: number }[]>([])
const selectedSpace = ref<number | null>(null)

async function load(): Promise<void> {
  try {
    const [versionRows, grantRows] = await Promise.all([
      listContentVersions(props.attachmentId),
      listGrants(props.attachmentId),
    ])
    versions.value = versionRows
    grants.value = grantRows
  } catch {
    versions.value = []
    grants.value = []
  }
  const households = spaces.spaces.filter((space) => space.kind === 'household')
  householdOptions.value = households.map((space) => ({ label: space.name, value: space.id }))
  if (selectedSpace.value === null && householdOptions.value.length > 0) {
    selectedSpace.value = householdOptions.value[0].value
  }
}

watch(() => props.attachmentId, load, { immediate: true })

function activeGrantFor(versionId: number): AgentGrantOut | undefined {
  return grants.value.find((grant) => grant.content_version_id === versionId && grant.status === 'active')
}

async function saveText(): Promise<void> {
  const text = draftText.value.trim()
  if (!text) return
  busy.value = true
  try {
    await createContentVersion(props.attachmentId, text)
    draftText.value = ''
    message.success('已保存内容版本，请确认后再授权')
    await load()
  } catch {
    message.error('保存失败：文字为空或超长（≤12000 字）')
  } finally {
    busy.value = false
  }
}

async function confirm(version: ContentVersionOut): Promise<void> {
  busy.value = true
  try {
    await confirmContentVersion(version.id)
    message.success('内容已确认')
    await load()
  } catch {
    message.error('确认失败：该版本可能已被新版本取代')
  } finally {
    busy.value = false
  }
}

async function grant(version: ContentVersionOut): Promise<void> {
  if (selectedSpace.value === null) {
    message.error('请先选择一个家庭空间')
    return
  }
  busy.value = true
  try {
    await createGrant(version.id, selectedSpace.value)
    message.success('已允许助手在该空间使用这份内容')
    await load()
  } catch {
    message.error('授权失败：只有已确认内容可以授权，且需为本人所在的家庭空间')
  } finally {
    busy.value = false
  }
}

async function revoke(item: AgentGrantOut): Promise<void> {
  busy.value = true
  try {
    await revokeGrant(item.id)
    message.success('已撤销授权')
    await load()
  } catch {
    message.error('撤销失败：授权可能已撤销')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="canEdit" class="agent-grant" data-test="agent-grant">
    <h5>供助手使用</h5>
    <p class="hint">
      确认文字后，可单独允许助手在某个家庭空间使用它。授权不会让其他人看到原图，
      撤销后助手立即无法再检索到这份内容。
    </p>
    <NInput
      v-model:value="draftText"
      type="textarea"
      :rows="2"
      maxlength="12000"
      placeholder="填写可供引用的文字（例如照片说明）"
      data-test="grant-text"
    />
    <NSpace class="row">
      <NButton size="small" :loading="busy" :disabled="!draftText.trim()" @click="saveText">
        保存内容
      </NButton>
      <NSelect
        v-model:value="selectedSpace"
        size="small"
        class="space-select"
        :options="householdOptions"
        placeholder="选择家庭空间"
        data-test="grant-space"
      />
    </NSpace>

    <ul class="versions" data-test="version-list">
      <li v-for="version in versions" :key="version.id" :data-test="`version-${version.id}`">
        <div class="line">
          <NTag size="tiny" :type="version.status === 'confirmed' ? 'success' : 'default'">
            {{ version.status }}
          </NTag>
          <span class="rev">v{{ version.revision }}</span>
          <span class="text">{{ version.text.slice(0, 60) }}</span>
        </div>
        <NSpace class="row">
          <NButton
            v-if="version.status === 'draft'"
            size="tiny"
            :loading="busy"
            data-test="confirm"
            @click="confirm(version)"
          >
            确认这段文字
          </NButton>
          <template v-if="version.status === 'confirmed'">
            <NButton
              v-if="!activeGrantFor(version.id)"
              size="tiny"
              type="primary"
              :loading="busy"
              data-test="grant"
              @click="grant(version)"
            >
              允许助手使用
            </NButton>
            <template v-else>
              <NTag size="tiny" type="info" data-test="granted">已授权</NTag>
              <NButton
                size="tiny"
                :loading="busy"
                data-test="revoke"
                @click="revoke(activeGrantFor(version.id)!)"
              >
                撤销授权
              </NButton>
            </template>
          </template>
        </NSpace>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.agent-grant {
  margin-top: 12px;
  border-top: 1px solid var(--n-border-color);
  padding-top: 8px;
}
.hint {
  font-size: 12px;
  opacity: 0.75;
  margin: 4px 0 8px;
}
.row {
  margin-top: 6px;
}
.space-select {
  width: 160px;
}
.versions {
  list-style: none;
  padding: 0;
  margin: 10px 0 0;
}
.versions li {
  border-top: 1px solid var(--n-border-color);
  padding: 6px 0;
}
.line {
  display: flex;
  gap: 6px;
  align-items: center;
}
.rev {
  font-size: 12px;
  opacity: 0.7;
}
.text {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
