import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * Internal service token (HMAC, shared-secret scheme per notes.md):
 * the sidecar mints a short-lived signed token from AGENT_SERVICE_SECRET and
 * presents it ONLY to `POST /internal/agent/jobs/lease`. Every subsequent
 * internal call uses the opaque run token issued in the lease response.
 *
 * Token format: base64url(header).base64url(payload).base64url(hmac-sha256)
 */

export interface ServiceTokenClaims {
  /** Fixed token audience/type marker (must match backend SERVICE_TOKEN_TYPE). */
  typ: "agent_service";
  /** Stable identifier of this sidecar instance (for audit only). */
  sid: string;
  /** Issued-at / expiry, epoch seconds. */
  iat: number;
  exp: number;
}

const b64u = (input: string | Buffer): string =>
  Buffer.from(input).toString("base64url");

function hmac(secret: string, data: string): string {
  return createHmac("sha256", secret).update(data).digest("base64url");
}

export function signServiceToken(
  secret: string,
  options: { sidecarId: string; nowMs?: number; ttlMs?: number },
): string {
  const nowSec = Math.floor((options.nowMs ?? Date.now()) / 1000);
  const claims: ServiceTokenClaims = {
    typ: "agent_service",
    sid: options.sidecarId,
    iat: nowSec,
    exp: nowSec + Math.floor((options.ttlMs ?? 60_000) / 1000),
  };
  const header = b64u(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64u(JSON.stringify(claims));
  const signingInput = `${header}.${payload}`;
  return `${signingInput}.${hmac(secret, signingInput)}`;
}

/** Verifies signature + expiry; returns claims or null when invalid/expired. */
export function verifyServiceToken(
  secret: string,
  token: string,
  nowMs: number = Date.now(),
): ServiceTokenClaims | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [header, payload, signature] = parts as [string, string, string];
  const expected = hmac(secret, `${header}.${payload}`);
  const a = Buffer.from(expected);
  const b = Buffer.from(signature);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  try {
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString()) as ServiceTokenClaims;
    if (claims.typ !== "agent_service") return null;
    if (claims.exp <= Math.floor(nowMs / 1000)) return null;
    return claims;
  } catch {
    return null;
  }
}

/**
 * Run tokens are minted by FastAPI at lease time (claims bound to run_id,
 * agent_kind, actor/space scope, tool allowlist, exp). The sidecar treats
 * them as opaque bearer strings — decode only for local diagnostics of the
 * unverified payload shape, never for authorization decisions.
 */
export interface RunTokenPeek {
  run_id?: unknown;
  agent_kind?: unknown;
  exp?: unknown;
}

export function peekRunTokenClaims(token: string): RunTokenPeek | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    return JSON.parse(Buffer.from(parts[1]!, "base64url").toString()) as RunTokenPeek;
  } catch {
    return null;
  }
}
