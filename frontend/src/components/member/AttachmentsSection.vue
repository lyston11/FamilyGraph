<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NSpin, useMessage } from 'naive-ui'

import {
  addLink,
  attachmentRawUrl,
  deleteAttachment,
  fetchAttachments,
  uploadImage,
  type AttachmentOut,
} from '@/api/attachments'

/**
 * 档案附件区（m3a）：相册网格（上传/预览/删除）+ 链接卡片列表。
 * 权限由后端强制（D5 编辑权）；无权者后端返回空/404。
 */
const props = defineProps<{ userId: number; canEdit: boolean }>()

const message = useMessage()

const items = ref<AttachmentOut[]>([])
const loading = ref(false)
const uploading = ref(false)
const linkUrl = ref('')
const linkTitle = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const previewSrc = ref<string | null>(null)

async function load() {
  loading.value = true
  try {
    items.value = await fetchAttachments(props.userId)
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)

function pickFile() {
  fileInput.value?.click()
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    await uploadImage(props.userId, file)
    message.success('照片已上传')
    await load()
  } catch {
    message.error('上传失败：仅支持 jpg/png/webp，≤10MB')
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function submitLink() {
  if (!linkUrl.value.trim()) return
  try {
    await addLink(props.userId, { url: linkUrl.value.trim(), title: linkTitle.value.trim() || undefined })
    linkUrl.value = ''
    linkTitle.value = ''
    message.success('链接已添加')
    await load()
  } catch {
    message.error('链接添加失败（需 http/https 开头）')
  }
}

async function remove(id: number) {
  try {
    await deleteAttachment(id)
    await load()
  } catch {
    message.error('删除失败')
  }
}
</script>

<template>
  <section class="attachments-section" data-test="attachments-section">
    <h4>相册</h4>
    <NSpin :show="loading">
      <div class="album-grid" data-test="album-grid">
        <div v-for="item in items.filter((i) => i.type === 'image')" :key="item.id" class="photo-cell">
          <img
            :src="attachmentRawUrl(item.id)"
            :alt="item.title ?? '家庭照片'"
            class="thumb"
            @click="previewSrc = attachmentRawUrl(item.id)"
          />
          <NButton
            v-if="canEdit"
            size="tiny"
            type="error"
            secondary
            class="del-btn"
            :aria-label="`删除照片 ${item.title ?? item.id}`"
            data-test="delete-photo"
            @click="remove(item.id)"
          >
            删除
          </NButton>
        </div>
        <button v-if="canEdit" class="upload-cell" :disabled="uploading" data-test="upload-photo" @click="pickFile">
          {{ uploading ? '上传中…' : '+ 上传照片' }}
        </button>
        <input ref="fileInput" type="file" accept=".jpg,.jpeg,.png,.webp" style="display: none" @change="onFileChange" />
      </div>
    </NSpin>

    <h4>链接</h4>
    <ul class="link-list" data-test="link-list">
      <li v-for="item in items.filter((i) => i.type === 'link')" :key="item.id" class="link-row">
        <a :href="item.url_or_path ?? '#'" target="_blank" rel="noopener noreferrer">
          {{ item.title ?? item.url_or_path }}
        </a>
        <NButton
          v-if="canEdit"
          size="tiny"
          quaternary
          type="error"
          :data-test="`delete-link-${item.id}`"
          @click="remove(item.id)"
        >
          删除
        </NButton>
      </li>
      <li v-if="items.every((i) => i.type !== 'link')" class="empty-hint">暂无链接</li>
    </ul>
    <form v-if="canEdit" class="link-form" data-test="link-form" @submit.prevent="submitLink">
      <input v-model="linkUrl" placeholder="https://…" aria-label="链接地址" data-test="link-url-input" />
      <input v-model="linkTitle" placeholder="标题（选填）" maxlength="200" aria-label="链接标题" />
      <button type="submit">添加链接</button>
    </form>

    <!-- 放大预览 -->
    <teleport to="body">
      <div v-if="previewSrc" class="preview-mask" @click="previewSrc = null">
        <img :src="previewSrc" alt="预览大图" class="preview-img" />
      </div>
    </teleport>
  </section>
</template>

<style scoped>
.attachments-section h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
  font-weight: 600;
}

.album-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 8px;
  min-height: 40px;
}

.photo-cell {
  position: relative;
}

.thumb {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: var(--fg-radius-control);
  cursor: zoom-in;
  display: block;
}

.del-btn {
  position: absolute;
  top: 2px;
  right: 2px;
}

/* 上传占位：虚线框与 MaskedField 封条语言一致，随主题走 token */
.upload-cell {
  aspect-ratio: 1;
  border: 1px dashed var(--fg-line-strong);
  border-radius: var(--fg-radius-control);
  background: none;
  color: var(--fg-ink-secondary);
  cursor: pointer;
  font-size: 12px;
}

.upload-cell:hover:not(:disabled) {
  border-color: var(--fg-accent);
  color: var(--fg-accent);
}

.upload-cell:disabled {
  cursor: default;
  opacity: 0.6;
}

.link-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.link-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
  gap: 8px;
}

.link-row a {
  color: var(--fg-accent);
  text-decoration: none;
  overflow-wrap: anywhere;
}

.link-row a:hover {
  text-decoration: underline;
}

.empty-hint {
  color: var(--fg-ink-faint);
  font-size: 12px;
}

.link-form {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

.link-form input {
  min-width: 0;
  flex: 1;
  padding: 4px 8px;
  font-size: 12px;
  color: var(--fg-ink);
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
}

.link-form input:focus {
  border-color: var(--fg-accent);
  outline: none;
}

.link-form button {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--fg-accent-ink);
  background-color: var(--fg-accent);
  border: none;
  border-radius: var(--fg-radius-control);
  cursor: pointer;
}

.preview-mask {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 72%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 3000;
  cursor: zoom-out;
}

.preview-img {
  max-width: 92vw;
  max-height: 90vh;
}
</style>
