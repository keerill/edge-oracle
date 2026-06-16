// Server-only configuration. QUANT_API_URL is read by the BFF route handlers / typed client
// (app/api/* and lib/api/client.ts) — never shipped to the browser, so the quant base URL
// stays internal. The browser talks only to our own /api/* routes.

export const QUANT_API_URL = process.env.QUANT_API_URL ?? "http://localhost:8000";

// Redis the live-signal SSE route subscribes to. Server-only — the browser talks only to our
// /api/stream endpoint, never to Redis directly. Must match the quant engine's EDGE_SIGNALS_CHANNEL.
export const REDIS_URL = process.env.REDIS_URL ?? "redis://localhost:6379";
export const SIGNALS_CHANNEL = process.env.SIGNALS_CHANNEL ?? "edge:signals";
