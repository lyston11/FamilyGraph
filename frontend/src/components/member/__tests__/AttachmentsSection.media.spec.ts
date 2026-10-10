import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { h } from 'vue'
import { NMessageProvider } from 'naive-ui'

import { useSpacesStore } from '@/stores/spaces'
import AttachmentsSection from '@/components/member/AttachmentsSection.vue'
import { fetchAttachmentBlob, fetchAttachments, type AttachmentOut } from '@/api/attachments'

// AttachmentsSection 媒体渲染生命周期（R1，09-11 整改）：
// - 缩略图经 fetchAttachmentBlob 取 blob 并以 object URL 渲染（不在 DOM 出现 /raw 直链）；
// - 卸载时 revoke 全部 object URL；
// - 单张失败显示统一安全失败态，不泄露附件存在性。
vi.mock('@/api/attachments', () => ({
  fetchAttachments: vi.fn(),
  fetchAttachmentBlob: vi.fn(),
  uploadImage: vi.fn(),
  addLink: vi.fn(),
  deleteAttachment: vi.fn(),
  MEDIA_UNAVAILABLE_MESSAGE: '照片暂时无法加载',
}))

const mockedFetchAttachments = vi.mocked(fetchAttachments)
const mockedFetchBlob = vi.mocked(fetchAttachmentBlob)

// jsdom 不实现 object URL：用确定性 stub（createObjectURL 生成可断言的 blob: URL）
let urlSeq = 0
const revokeObjectURL = vi.fn()
const createObjectURL = vi.fn(() => `blob:mock-${++urlSeq}`)
beforeAll(() => {
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true })
})

function makeImage(id: number): AttachmentOut {
  return {
    id,
    user_id: 1,
    type: 'image',
    title: `照片${id}`,
    description: null,
    url_or_path: null,
    created_at: '2026-09-11T08:00:00',
  }
}

function makeBlob(): Blob {
  return { type: 'image/png', size: 100 } as unknown as Blob
}

async function mountAttachments(pinia = createPinia()) {
  // useMessage 需要 NMessageProvider 上下文（spec 组件约定）
  const wrapper = mount(NMessageProvider, {
    props: {},
    slots: { default: () => h(AttachmentsSection, { userId: 1, canEdit: false }) },
    global: { plugins: [pinia] },
  })
  await vi.waitFor(() => expect(mockedFetchAttachments).toHaveBeenCalled())
  return wrapper
}

describe('AttachmentsSection 媒体 object URL 生命周期', () => {
  afterEach(() => {
    revokeObjectURL.mockClear()
    createObjectURL.mockClear()
    mockedFetchAttachments.mockReset()
    mockedFetchBlob.mockReset()
  })

  it('加载图片附件后经 object URL 渲染缩略图；失败显示安全态', async () => {
    mockedFetchAttachments.mockResolvedValue([makeImage(1), makeImage(2), makeImage(3)])
    mockedFetchBlob.mockImplementation(async (id: number) => {
      if (id === 2) throw new Error('403')
      return makeBlob()
    })

    const wrapper = await mountAttachments()
    await vi.waitFor(() => {
      expect(wrapper.findAll('[data-test="photo-thumb"]').length).toBe(2)
    })
    expect(wrapper.find('[data-test="photo-unavailable"]').exists()).toBe(true)
    // 缩略图 src 是 blob: object URL，不是 raw 端点直链
    const src = wrapper.find('[data-test="photo-thumb"]').attributes('src') ?? ''
    expect(src.startsWith('blob:')).toBe(true)
    expect(src).not.toContain('/raw')
    wrapper.unmount()
  })

  it('切换空间立即释放旧媒体，迟到图片不回写', async () => {
    const pinia = createPinia()
    const spaces = useSpacesStore(pinia)
    spaces.currentSpaceId = 10
    let resolveOld!: (value: Blob) => void
    const oldBlob = new Promise<Blob>((resolve) => { resolveOld = resolve })
    mockedFetchAttachments.mockResolvedValueOnce([makeImage(1), makeImage(2)]).mockResolvedValueOnce([])
    mockedFetchBlob.mockResolvedValueOnce(makeBlob()).mockReturnValueOnce(oldBlob)
    const wrapper = await mountAttachments(pinia)
    await vi.waitFor(() => expect(wrapper.findAll('[data-test="photo-thumb"]')).toHaveLength(1))
    expect(mockedFetchAttachments).toHaveBeenCalledWith(1, 10)
    expect(mockedFetchBlob).toHaveBeenCalledWith(2, undefined, 10)
    const oldUrl = wrapper.find('[data-test="photo-thumb"]').attributes('src')
    spaces.currentSpaceId = 20
    await flushPromises()
    expect(revokeObjectURL).toHaveBeenCalledWith(oldUrl)
    expect(mockedFetchAttachments).toHaveBeenLastCalledWith(1, 20)
    resolveOld(makeBlob())
    await flushPromises()
    expect(wrapper.findAll('[data-test="photo-thumb"]')).toHaveLength(0)
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('旧空间列表晚到时不会触发图片读取；卸载后的图片不创建 URL', async () => {
    const pinia = createPinia()
    const spaces = useSpacesStore(pinia)
    spaces.currentSpaceId = 10
    let resolveList!: (value: AttachmentOut[]) => void
    let resolveBlob!: (value: Blob) => void
    mockedFetchAttachments.mockReturnValueOnce(new Promise((resolve) => { resolveList = resolve }))
      .mockResolvedValueOnce([makeImage(2)])
    mockedFetchBlob.mockReturnValueOnce(new Promise((resolve) => { resolveBlob = resolve }))
    const wrapper = await mountAttachments(pinia)
    spaces.currentSpaceId = 20
    await flushPromises()
    expect(mockedFetchBlob).toHaveBeenCalledWith(2, undefined, 20)
    resolveList([makeImage(1)])
    await flushPromises()
    expect(mockedFetchBlob).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    resolveBlob(makeBlob())
    await flushPromises()
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it('组件卸载时 revoke 全部 object URL', async () => {
    mockedFetchAttachments.mockResolvedValue([makeImage(1), makeImage(2)])
    mockedFetchBlob.mockResolvedValue(makeBlob())

    const wrapper = await mountAttachments()
    await vi.waitFor(() => expect(wrapper.findAll('[data-test="photo-thumb"]').length).toBe(2))
    const revokeCount = revokeObjectURL.mock.calls.length
    wrapper.unmount()
    expect(revokeObjectURL.mock.calls.length).toBeGreaterThan(revokeCount)
  })

  it('重新加载时释放上一批 object URL；卸载后 create/revoke 一一对应', async () => {
    mockedFetchAttachments.mockResolvedValue([makeImage(1)])
    mockedFetchBlob.mockResolvedValue(makeBlob())

    const wrapper = await mountAttachments()
    await vi.waitFor(() => expect(createObjectURL.mock.calls.length).toBe(1))
    expect(revokeObjectURL.mock.calls.length).toBe(0)
    wrapper.unmount()
    // 卸载路径统一 revoke：每个创建的 object URL 都被释放，无泄漏
    expect(revokeObjectURL.mock.calls.length).toBe(createObjectURL.mock.calls.length)
  })
})
