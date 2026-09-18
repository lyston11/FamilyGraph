/**
 * Typed errors for the FastAPI internal protocol. HTTP status mapping is
 * fail-closed: unexpected statuses never silently succeed.
 */

export class InternalApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
    this.name = new.target.name;
  }
}

export class AuthError extends InternalApiError {
  /** 401 — service/run token missing, expired or invalid. */
  constructor(message: string) {
    super(message, 401, "auth");
  }
}

export class ForbiddenError extends InternalApiError {
  /** 403 — token valid but scope/allowlist denies the operation (user JWT etc.). */
  constructor(message: string) {
    super(message, 403, "forbidden");
  }
}

export class ConflictError extends InternalApiError {
  /** 409 — protocol-level conflict (e.g. run already settled). */
  constructor(message: string) {
    super(message, 409, "conflict");
  }
}

/**
 * 409 whose body carries `detail.reason == "cancel_requested"`: the server has
 * already adjudicated this run as cancelled.
 *
 * This is NOT a protocol conflict to report as a sidecar failure. The run is
 * still `running` in the DB (cancel_requested does not change the status), so
 * every in-flight internal write starts returning 409 the moment the browser
 * cancels — long before the lease/3 heartbeat cadence can observe the flag.
 * Treating it as an ordinary conflict let the catch-all settle the run `failed`
 * with `SIDECAR_ERROR`, overwriting the server's cancelled verdict and showing
 * the user "助手服务暂时不可用" instead of "已取消".
 *
 * `agent_queue.request_cancel` keeps a sidecar `failed` as-is, so the only
 * correct behaviour is to stop issuing work and never settle.
 */
export class RunCancelledError extends InternalApiError {
  constructor(message: string) {
    super(message, 409, "run_cancelled");
  }
}

export class GoneError extends InternalApiError {
  /** 410 — lease lost / job reaped; worker must drop the run immediately. */
  constructor(message: string) {
    super(message, 410, "gone");
  }
}

/** Retryable: network failure or transient upstream (502/503/504). */
export class TransientError extends Error {
  constructor(
    message: string,
    readonly status: number | undefined,
  ) {
    super(message);
    this.name = new.target.name;
  }
}
