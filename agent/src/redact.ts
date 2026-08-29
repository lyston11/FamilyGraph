/**
 * Free-form error text redaction before logs / settle payloads.
 *
 * Provider (and other upstream) error strings may embed request bodies,
 * API keys, bearer tokens or URL credentials. Nothing unredacted may reach
 * structured logs or the backend settle payload — both are durable.
 */

/** Max characters retained from any single error text (settle + log share this). */
export const MAX_ERROR_TEXT = 300;

/** Patterns whose matches are replaced unconditionally. */
const SECRET_PATTERNS: RegExp[] = [
  // URL credentials: scheme://user:password@host
  /\b([a-z][a-z0-9+.-]*:\/\/)[^\s/@]*:[^\s/@]*@/gi,
  // Bearer / token auth headers
  /\b(bearer|basic|token)\s+[\w.~+/=-]{8,}/gi,
  // Common API key shapes (OpenAI sk-, generic key= / api_key= / access_token= params)
  /\bsk-[\w-]{8,}/g,
  /\b(api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|pwd|authorization)\s*[=:]\s*["']?[\w.~+/=-]{6,}["']?/gi,
];

function stripControlChars(text: string): string {
  // Keep printable ASCII + common whitespace; drop everything else (upstream
  // bodies sometimes carry binary junk).
  return text.replace(/[^\x20-\x7e\t\n\r]/g, "\uFFFD");
}

/**
 * Redact a free-form upstream error string for durable sinks (log + settle):
 * control/binary chars masked, secret-shaped substrings replaced, length capped.
 */
export function redactErrorText(raw: string, maxLen: number = MAX_ERROR_TEXT): string {
  let text = stripControlChars(String(raw));
  for (const pattern of SECRET_PATTERNS) {
    text = text.replace(pattern, (_match, keep) => `${typeof keep === "string" ? keep : ""}[REDACTED]`);
  }
  if (text.length > maxLen) text = `${text.slice(0, maxLen)}…`;
  return text;
}
