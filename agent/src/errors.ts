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
