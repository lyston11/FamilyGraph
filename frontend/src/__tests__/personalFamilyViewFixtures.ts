import type {
  PersonalFamilyViewData,
  PersonalFamilyViewNode,
  PersonalFamilyViewProgress,
  PersonalFamilyViewSnapshot,
} from '@/types/api'

export function familyNode(id: number): PersonalFamilyViewNode {
  return {
    user_id: id,
    display: { id, name: `家人${id}`, gender: 'm', birth: null, death: null, bio: null,
      avatar_path: null, privacy_mode: 'perpetual', claim_status: 'claimed' },
    visibility_level: id === 1 ? 'self_private' : 'household_detail',
    inclusion_reason_code: id === 1 ? 'root' : 'confirmed_path',
  }
}

export function familyProgress(overrides: Partial<PersonalFamilyViewProgress> = {}): PersonalFamilyViewProgress {
  const targets = overrides.targets ?? [{ user_id: 2, status: 'pending' as const, reason_code: null }]
  return {
    contract_version: 'pfv-progress-v1', generation: 7, revision: 1, topology_revision: 'parents-v1',
    phase: 'building', targets, reason_code: null, next_poll_ms: 1000,
    completed_count: targets.filter((target) => target.status === 'ready' || target.status === 'unavailable').length,
    total_count: targets.length, ...overrides,
  }
}

export function familyData(overrides: Partial<PersonalFamilyViewData> = {}): PersonalFamilyViewData {
  return {
    space_id: 9, view_version: 1, status: 'running', computed_at: null,
    nodes: [familyNode(1), familyNode(2)],
    topology_edges: [{ id: 'parent:biological:2:1', from_user_id: 2, to_user_id: 1, edge_kind: 'parent', subtype: 'biological' }],
    edges: [], inferred_edges: [], truncated: false, next_cursor: null, stale_reason: null,
    progress: familyProgress(), ...overrides,
  }
}

export function familySnapshot(
  overrides: Partial<PersonalFamilyViewData> = {},
  metadata: Partial<Omit<PersonalFamilyViewSnapshot, 'data'>> = {},
): PersonalFamilyViewSnapshot {
  const data = familyData(overrides)
  return {
    data, etag: `"${data.progress?.generation}:${data.progress?.revision}"`,
    displayUntil: Math.floor(Date.now() / 1000) + 60,
    serverDate: Date.now(), displayExpiresAt: Date.now() + 60_000, ...metadata,
  }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
