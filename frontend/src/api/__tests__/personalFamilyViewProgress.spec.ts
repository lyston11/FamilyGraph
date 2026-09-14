import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { familyData, familyProgress } from '@/__tests__/personalFamilyViewFixtures'
import { apiClient } from '@/api/client'
import { decodePersonalFamilyView, demandPersonalFamilyView, fetchPersonalFamilyView } from '@/api/personalFamilyView'

vi.mock('@/api/client', () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }))

describe('PFV progressive wire protocol', () => {
  beforeEach(() => { vi.resetAllMocks() })
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

  it('keeps authorized target states and true counts; a missing progress block remains legacy', () => {
    const progress = familyProgress({ phase: 'ready', next_poll_ms: 0,
      targets: [{ user_id: 2, status: 'unavailable', reason_code: 'NO_VISIBLE_PATH' }] })
    expect(decodePersonalFamilyView(familyData({ progress })).progress).toEqual(progress)
    expect(decodePersonalFamilyView(familyData({ progress: undefined })).progress).toBeNull()
  })

  it.each([
    { contract_version: 'pfv-progress-v999' },
    { generation: -1 }, { revision: 1.5 }, { revision: Number.MAX_SAFE_INTEGER + 1 },
    { completed_count: 1 }, { total_count: 7 }, { phase: 'ready' },
    { topology_revision: 1 }, { next_poll_ms: -1 },
    { targets: [{ user_id: 99, status: 'pending' }] },
    { total_count: 2, targets: [{ user_id: 2, status: 'pending' }, { user_id: 2, status: 'pending' }] },
  ])('rejects unknown, inconsistent or unsafe progress: %j', (override) => {
    expect(() => decodePersonalFamilyView({ ...familyData(), progress: { ...familyProgress(), ...override } })).toThrow('暂时无法验证')
  })

  it('requires topology data for a progressive skeleton', () => {
    expect(() => decodePersonalFamilyView({ ...familyData(), topology_edges: undefined })).toThrow('暂时无法验证')
  })

  it.each([200, 304])('supports a legacy single Date on %i, retaining skew and round-trip deductions', async (status) => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2035-01-01T00:00:00Z'))
    vi.spyOn(performance, 'now').mockReturnValueOnce(100).mockReturnValueOnce(350)
    const serverDate = Date.parse('2026-09-14T00:00:00Z')
    vi.mocked(apiClient.get).mockResolvedValue({ status, data: familyData(), headers: {
      etag: '"7:1"', date: new Date(serverDate).toUTCString(), 'x-pfv-display-until': String(serverDate / 1000 + 60),
    } })
    const response = await fetchPersonalFamilyView(9, '"7:1"', { progressive: true })
    expect(response).toMatchObject({ etag: '"7:1"', serverDate,
      displayUntil: serverDate / 1000 + 60, displayExpiresAt: Date.now() + 58_750 })
    if (status === 304) expect(response).toHaveProperty('notModified', true)
    else expect(response).toHaveProperty('data.progress.contract_version', 'pfv-progress-v1')
  })

  it.each([200, 304])('uses Validated-At on %i even when Uvicorn and the app supplied duplicate Date headers', async (status) => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2035-01-01T00:00:00Z'))
    vi.spyOn(performance, 'now').mockReturnValueOnce(100).mockReturnValueOnce(350)
    const serverDate = Date.parse('2026-09-14T00:00:00Z')
    const date = new Date(serverDate).toUTCString()
    const combined = `${date}, ${date}`
    expect(Number.isNaN(Date.parse(combined))).toBe(true)
    vi.mocked(apiClient.get).mockResolvedValue({ status, data: familyData(), headers: {
      etag: '"7:1"', date: combined,
      'x-pfv-validated-at': String(serverDate / 1000),
      'x-pfv-display-until': String(serverDate / 1000 + 60),
    } })
    expect(await fetchPersonalFamilyView(9, '"7:1"', { progressive: true })).toMatchObject({
      etag: '"7:1"', serverDate, displayUntil: serverDate / 1000 + 60,
      displayExpiresAt: Date.now() + 58_750,
    })
  })

  it('does not require Date when the authoritative timestamp header is valid', async () => {
    vi.useFakeTimers()
    const serverDate = Date.parse('2026-09-14T00:00:00Z')
    vi.mocked(apiClient.get).mockResolvedValue({ status: 200, data: familyData(), headers: {
      ETag: '"7:1"', 'X-PFV-Validated-At': String(serverDate / 1000),
      'X-PFV-Display-Until': String(serverDate / 1000 + 60),
    } })
    expect(await fetchPersonalFamilyView(9, null, { progressive: true })).toMatchObject({
      serverDate, displayExpiresAt: Date.now() + 59_000,
    })
  })

  it.each(['bad', '', '0', '-1', '1.5', '1770000000, 1770000001', '9007199254740991', null, undefined])(
    'fails closed on an invalid present Validated-At (%s), even with a valid fallback Date', async (validatedAt) => {
      const serverDate = Date.parse('2026-09-14T00:00:00Z')
      for (const status of [200, 304]) {
        vi.mocked(apiClient.get).mockResolvedValue({ status, data: familyData(), headers: {
          etag: '"7:1"', date: new Date(serverDate).toUTCString(),
          'x-pfv-validated-at': validatedAt,
          'x-pfv-display-until': String(serverDate / 1000 + 60),
        } })
        expect(await fetchPersonalFamilyView(9, '"7:1"', { progressive: true })).toMatchObject({
          serverDate: null, displayExpiresAt: null,
        })
      }
    },
  )

  it('never derives a legacy deadline from combined or malformed Date values', async () => {
    const date = 'Mon, 14 Sep 2026 00:00:00 GMT'
    for (const rawDate of [`${date}, ${date}`, '2026-09-14', 'invalid']) {
      vi.mocked(apiClient.get).mockResolvedValue({ status: 304, headers: {
        etag: '"7:1"', date: rawDate, 'x-pfv-display-until': String(Date.parse(date) / 1000 + 60),
      } })
      expect(await fetchPersonalFamilyView(9, '"7:1"')).toMatchObject({ serverDate: null, displayExpiresAt: null })
    }
  })

  it.each([{ etag: '"7:1"' }, { etag: '"7:1"', 'x-pfv-display-until': '9999999999' }])(
    'does not manufacture expiry from incomplete headers', async (headers) => {
      vi.mocked(apiClient.get).mockResolvedValue({ status: 304, headers })
      expect(await fetchPersonalFamilyView(9, '"7:1"')).toMatchObject({ displayExpiresAt: null })
    },
  )

  it('focus and retry send only the authenticated-space demand shape', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { status: 'already_active', focus_user_id: 2 } })
    await demandPersonalFamilyView(9, { focusUserId: 2, retry: true })
    expect(apiClient.post).toHaveBeenCalledWith('/personal-family-view/demand',
      { space_id: 9, focus_user_id: 2, retry: true }, { signal: undefined })
  })
})
