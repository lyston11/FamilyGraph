/**
 * Structured logger. Secrets must never be passed as field values — callers
 * pass identifiers and error codes only.
 */

export type LogFields = Record<string, unknown>;

export interface Logger {
  info(message: string, fields?: LogFields): void;
  warn(message: string, fields?: LogFields): void;
  error(message: string, fields?: LogFields): void;
  debug(message: string, fields?: LogFields): void;
  child(fields: LogFields): Logger;
}

function emit(level: "info" | "warn" | "error" | "debug", message: string, fields: LogFields): void {
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    msg: message,
    ...fields,
  });
  if (level === "error") process.stderr.write(`${line}\n`);
  else process.stdout.write(`${line}\n`);
}

function merge(base: LogFields, extra?: LogFields): LogFields {
  return extra === undefined ? { ...base } : { ...base, ...extra };
}

export function createLogger(base: LogFields = {}): Logger {
  return {
    info: (message, fields) => emit("info", message, merge(base, fields)),
    warn: (message, fields) => emit("warn", message, merge(base, fields)),
    error: (message, fields) => emit("error", message, merge(base, fields)),
    debug: (message, fields) => {
      if (process.env["AGENT_LOG_LEVEL"] === "debug") {
        emit("debug", message, merge(base, fields));
      }
    },
    child: (fields) => createLogger(merge(base, fields)),
  };
}
