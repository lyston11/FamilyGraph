import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { h } from 'vue'

import { apiClient } from '@/api/client'
import type { CreateMemoryCandidatePayload } from '@/api/memory'
import MemoryEditorDialog, { type MemoryEditorInitial } from '@/components/memory/MemoryEditorDialog.vue'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import type { MemoryCandidate } from '@/types/memory'

// Keep the real component -> store -> API -> Axios serialization path. Only
// the transport is replaced; FastAPI/schema/transaction tests live in backend.
const originalAdapter = apiClient.defaults.adapter
let wrapper: VueWrapper | undefined
let posts: CreateMemoryCandidatePayload[]
let creationFailures: number
let listFailures: number
let sourceRejected: boolean
let savedCandidate: MemoryCandidate | null

function response(config: InternalAxiosRequestConfig, data: unknown, status = 200) {
  return { config, data, status, statusText: status === 201 ? 'Created' : 'OK', headers: {} }
}

const adapter: AxiosAdapter = async (config) => {
  if (config.url !== '/memory-candidates') throw new Error(`Unexpected endpoint: ${config.url}`)
  if (config.method === 'post') {
    const body: CreateMemoryCandidatePayload = JSON.parse(config.data as string)
    posts.push(body)
    if (sourceRejected) {
      throw new AxiosError('Forbidden', 'ERR_BAD_REQUEST', config, undefined, response(config, {
        error: { code: 'MEMORY_SCOPE_FORBIDDEN', message: '来源已经撤销' },
      }, 403))
    }
    if (creationFailures > 0) {
      creationFailures -= 1
      throw new AxiosError('Connection lost', 'ERR_NETWORK', config)
    }
    savedCandidate = {
      id: 71, source_message_id: null, source_document_ref: null, source_span_json: {},
      source_kind: body.source.kind, source_status: 'available', allowed_scopes: ['private'],
      raw_quote: body.raw_quote ?? '服务端回读的原文', summary: body.summary, purpose: body.purpose,
      suggested_scope: body.suggested_scope, sensitivity: body.sensitivity,
      extractor_version: 'manual-v1', status: 'pending', memory_id: null,
      created_at: '2026-09-13T00:00:00Z', decided_at: null,
    }
    return response(config, savedCandidate, 201)
  }
  if (listFailures > 0) {
    listFailures -= 1
    throw new AxiosError('List unavailable', 'ERR_NETWORK', config)
  }
  return response(config, savedCandidate ? [savedCandidate] : [])
}

async function mountEditor(initial: MemoryEditorInitial | null = null) {
  const pinia = createPinia()
  const memory = useMemoryStore(pinia)
  memory.features = { memory_enabled: true, rag_enabled: true }
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [{
    id: 5, name: '当前家庭', owner_id: 1, kind: 'household',
    created_at: '2026-09-13T00:00:00Z', pending_count: 0, member_count: 2,
  }]
  spaces.currentSpaceId = 5
  wrapper = mount(NMessageProvider, {
    slots: { default: () => h(MemoryEditorDialog, { show: true, initial }) },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await flushPromises()
  return { memory, editor: wrapper.findComponent(MemoryEditorDialog) }
}

async function input(selector: string, value: string): Promise<void> {
  const element = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)
  expect(element).not.toBeNull()
  element!.value = value
  element!.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

async function fillManual(): Promise<void> {
  await input('[data-test="memory-editor-quote"] textarea', '我喜欢清淡饮食')
  await input('[data-test="memory-editor-summary"] input', '我的饮食偏好')
  await input('[data-test="memory-editor-purpose"] input', '聚餐安排')
}

async function submit(): Promise<void> {
  document.querySelector<HTMLButtonElement>('[data-test="memory-editor-submit"]')!.click()
  await flushPromises()
}

describe('MemoryEditorDialog HTTP contract and retries', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    posts = []
    creationFailures = 0
    listFailures = 0
    sourceRejected = false
    savedCandidate = null
    apiClient.defaults.adapter = adapter
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    apiClient.defaults.adapter = originalAdapter
  })

  it('serializes an explicit manual source and operation key through the real API module', async () => {
    const { editor, memory } = await mountEditor()
    await fillManual()
    await submit()

    expect(posts).toHaveLength(1)
    expect(posts[0]).toEqual({
      source: { kind: 'manual' }, idempotency_key: expect.any(String),
      raw_quote: '我喜欢清淡饮食', summary: '我的饮食偏好', purpose: '聚餐安排',
      suggested_scope: 'private', sensitivity: 'normal',
    })
    expect(posts[0]!.idempotency_key.length).toBeGreaterThanOrEqual(8)
    expect(memory.pendingCandidates.map((candidate) => candidate.id)).toEqual([71])
    expect(editor.emitted('update:show')).toEqual([[false]])
  })

  it('keeps the key after a lost response and changes it when submitted content changes', async () => {
    creationFailures = 2
    const { editor, memory } = await mountEditor()
    await fillManual()
    await submit()

    expect(document.querySelector('[data-test="memory-editor-error"]')?.textContent).toContain('网络异常')
    expect(memory.pendingCandidates).toEqual([])
    expect(editor.emitted('update:show')).toBeUndefined()
    await submit()
    expect(posts[1]!.idempotency_key).toBe(posts[0]!.idempotency_key)

    await input('[data-test="memory-editor-summary"] input', '更新后的饮食偏好')
    await submit()
    expect(posts[2]!.idempotency_key).not.toBe(posts[1]!.idempotency_key)
    expect(posts[2]!.summary).toBe('更新后的饮食偏好')
    expect(editor.emitted('update:show')).toEqual([[false]])
  })

  it('does not announce success when the committed candidate list failed to reload', async () => {
    listFailures = 1
    const { editor, memory } = await mountEditor()
    await fillManual()
    await submit()

    expect(document.querySelector('[data-test="memory-editor-error"]')?.textContent).toContain('操作已提交')
    expect(memory.pendingCandidates).toEqual([])
    expect(editor.emitted('update:show')).toBeUndefined()

    await submit()
    expect(posts[1]!.idempotency_key).toBe(posts[0]!.idempotency_key)
    expect(memory.pendingCandidates.map((candidate) => candidate.id)).toEqual([71])
    expect(editor.emitted('update:show')).toEqual([[false]])
  })

  it('preserves the RAG locator and sensitivity while leaving exact source text to the server', async () => {
    const source = {
      kind: 'rag_chunk' as const, document_id: 12, chunk_id: 34,
      revision: 2, index_version: 'v1', space_id: 5,
    }
    const { editor } = await mountEditor({
      source, raw_quote: '  这段原话的空白应原样保留。\n', summary: '本人整理的摘要',
      sensitivity: 'sensitive', allowed_scopes: ['private'],
    })
    const quote = document.querySelector<HTMLTextAreaElement>('[data-test="memory-editor-quote"] textarea')!
    expect(quote.readOnly).toBe(true)
    expect(quote.value).toBe('  这段原话的空白应原样保留。\n')
    expect(document.querySelector('[data-test="memory-editor-source-hint"]')?.textContent).toContain('摘要和用途由你整理')
    await input('[data-test="memory-editor-purpose"] input', '供本人参考')
    await submit()

    expect(posts[0]).toMatchObject({ source, sensitivity: 'sensitive', suggested_scope: 'private' })
    expect(posts[0]).not.toHaveProperty('raw_quote')
    expect(posts[0]).not.toHaveProperty('source_document_ref')
    expect(editor.emitted('update:show')).toEqual([[false]])
  })

  it('displays source rejection without closing or creating an optimistic candidate', async () => {
    sourceRejected = true
    const { editor, memory } = await mountEditor({
      source: { kind: 'rag_chunk', document_id: 12, chunk_id: 34, revision: 2, index_version: 'v1', space_id: 5 },
      raw_quote: '原来源在检索后撤销', summary: '整理摘要', allowed_scopes: ['private'],
    })
    await input('[data-test="memory-editor-purpose"] input', '参考用途')
    await submit()

    expect(document.querySelector('[data-test="memory-editor-error"]')?.textContent).toContain('来源或保存范围当前不可用')
    expect(memory.pendingCandidates).toEqual([])
    expect(editor.emitted('update:show')).toBeUndefined()
  })

  it('an already-open editor cannot submit after Memory is disabled', async () => {
    const { memory } = await mountEditor()
    await fillManual()
    memory.features = { memory_enabled: false, rag_enabled: true }
    await flushPromises()

    expect(document.querySelector<HTMLButtonElement>('[data-test="memory-editor-submit"]')!.disabled).toBe(true)
    expect(document.querySelector('[data-test="memory-editor-disabled"]')?.textContent).toContain('当前未启用')
    await submit()
    expect(posts).toEqual([])
  })
})
