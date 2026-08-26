/** Exponential backoff with jitter for transient failures only. */

export interface BackoffPolicy {
  baseDelayMs: number;
  maxDelayMs: number;
  maxAttempts: number;
}

export const DEFAULT_BACKOFF: BackoffPolicy = {
  baseDelayMs: 200,
  maxDelayMs: 5000,
  maxAttempts: 4,
};

/** Delay before attempt `attempt` (1-based); attempt > maxAttempts → null. */
export function backoffDelayMs(
  policy: BackoffPolicy,
  attempt: number,
  random: () => number = Math.random,
): number | null {
  if (attempt < 1 || attempt > policy.maxAttempts) return null;
  const exponential = Math.min(
    policy.baseDelayMs * 2 ** (attempt - 1),
    policy.maxDelayMs,
  );
  // Full jitter in [exponential/2, exponential).
  return Math.floor(exponential / 2 + random() * (exponential / 2));
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
