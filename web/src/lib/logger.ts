// Server-only structured logger — one JSON object per line to stdout, sharing the five base
// fields with the quant JSON logs (ts/level/logger/msg/service). Used by the SSE routes and the
// API client; never import this into a "use client" component.

type Level = "info" | "warn" | "error";

export function log(level: Level, msg: string, fields?: Record<string, unknown>): void {
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    logger: "web",
    msg,
    service: "web",
    ...fields,
  });
  console[level](line);
}

export const logger = {
  info: (msg: string, fields?: Record<string, unknown>) => log("info", msg, fields),
  warn: (msg: string, fields?: Record<string, unknown>) => log("warn", msg, fields),
  error: (msg: string, fields?: Record<string, unknown>) => log("error", msg, fields),
};
