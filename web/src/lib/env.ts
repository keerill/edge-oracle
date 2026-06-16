// Server-only configuration. QUANT_API_URL is read by the BFF route handlers / typed client
// (app/api/* and lib/api/client.ts) — never shipped to the browser, so the quant base URL
// stays internal. The browser talks only to our own /api/* routes.

export const QUANT_API_URL = process.env.QUANT_API_URL ?? "http://localhost:8000";
