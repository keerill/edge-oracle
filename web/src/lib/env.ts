// Server-only configuration. QUANT_API_URL is read by the BFF route handlers / typed client
// (app/api/* and lib/api/client.ts) — never shipped to the browser, so the quant base URL
// stays internal. The browser talks only to our own /api/* routes.

export const QUANT_API_URL = process.env.QUANT_API_URL ?? "http://localhost:8000";
// Shared secret the BFF sends as X-API-Key when quant has EDGE_API_KEY set. Server-only secret;
// undefined in local dev (quant then runs open). Inject via env / a secret manager, never commit.
export const QUANT_API_KEY = process.env.QUANT_API_KEY;

// The executor's control API (Phase 6-UI: list/approve pending intents). Server-only — the
// browser talks only to our /api/exec/* routes, never to the executor directly. EXEC_API_KEY is
// the shared secret the BFF sends as X-API-Key when the executor has EDGE_EXEC_CONTROL_API_KEY set.
export const EXEC_API_URL = process.env.EXEC_API_URL ?? "http://localhost:8010";
export const EXEC_API_KEY = process.env.EXEC_API_KEY;

// Redis the live-signal SSE route subscribes to. Server-only — the browser talks only to our
// /api/stream endpoint, never to Redis directly. Must match the quant engine's EDGE_SIGNALS_CHANNEL.
export const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
export const SIGNALS_CHANNEL = process.env.SIGNALS_CHANNEL ?? "edge:signals";
// Redis channel the alerts SSE route subscribes to. Must match the quant EDGE_ALERTS_CHANNEL.
export const ALERTS_CHANNEL = process.env.ALERTS_CHANNEL ?? "edge:alerts";
