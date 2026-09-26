/**
 * Steward prompt version — the cross-layer literal.
 *
 * The steward's prompt text is server-owned: the in-process carrier sends
 * `steward_assist._PROMPTS[kind]` as the system message, and the projection
 * carries that same text to a Pi child run as `steward_instructions`. There is
 * deliberately no local steward system prompt here.
 *
 * Why not one. A sidecar-local steward prompt (added with the S1 skeleton) would
 * be sent *instead of* the server's text, so the two carriers would ask the model
 * different questions while `prompt_digest` — computed over the server's text —
 * claimed otherwise. For the candidate kind that difference is load-bearing: the
 * direction semantics ("biological_parent's subject is the object's parent") and
 * the conflict rules live in that text, and the output validator is a second line
 * rather than a substitute. It was also never the prompt the in-process path used,
 * so keeping it as a "fallback" would only have hidden the divergence.
 *
 * The general posture rules that used to live in this file (no fabrication, use
 * only the supplied material, minimal disclosure) are enforced server-side instead:
 * the projection carries only codenames and fact ids, and the output validator
 * rejects anything outside the closed schema. If they are ever wanted in the
 * prompt, they belong in the server's text so that both carriers get them.
 *
 * This constant is what the server sends in the context projection and what the
 * sidecar compares against before any model call, so a stale image fails closed
 * rather than running old prompt text against a newer backend.
 */
export const STEWARD_PROMPT_VERSION = "steward-v1";
