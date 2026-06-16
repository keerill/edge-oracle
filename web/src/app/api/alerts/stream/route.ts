// SSE endpoint: subscribe to the Redis alerts channel and stream each system alert to the
// dashboard. Unlike /api/stream (high-rate signals, conflated), alerts are low-rate and each one
// is meaningful, so there is NO conflation — every validated alert is forwarded immediately. The
// quant payload is untrusted and Zod-validated (AlertSchema) before forwarding.

import Redis from "ioredis";
import { ALERTS_CHANNEL, REDIS_URL } from "@/lib/env";
import { AlertSchema } from "@/lib/schemas/alert";
import { logger } from "@/lib/logger";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HEARTBEAT_MS = 15_000; // keep proxies from closing an idle stream

export async function GET(request: Request) {
  const encoder = new TextEncoder();
  // Dedicated connection: a subscribed ioredis client can't run other commands.
  const sub = new Redis(REDIS_URL, { lazyConnect: true, maxRetriesPerRequest: null });

  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let closed = false;

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (chunk: string) => {
        if (!closed) controller.enqueue(encoder.encode(chunk));
      };

      const teardown = () => {
        if (closed) return;
        closed = true;
        if (heartbeat) clearInterval(heartbeat);
        sub.disconnect();
        try {
          controller.close();
        } catch {
          // already closed
        }
      };

      sub.on("message", (_channel, payload) => {
        let json: unknown;
        try {
          json = JSON.parse(payload);
        } catch {
          logger.warn("alerts sse: non-JSON payload");
          return;
        }
        const parsed = AlertSchema.safeParse(json);
        if (parsed.success) {
          send(`data: ${JSON.stringify(parsed.data)}\n\n`);
        } else {
          logger.warn("alerts sse: invalid alert payload");
        }
      });
      sub.on("error", (err) => {
        logger.warn("alerts sse: redis error", { err: String(err) });
        teardown();
      });

      try {
        await sub.connect();
        await sub.subscribe(ALERTS_CHANNEL);
      } catch (err) {
        logger.error("alerts sse: subscribe failed", { err: String(err) });
        teardown();
        return;
      }

      send(`: connected\n\n`); // open the stream immediately so EventSource fires `onopen`
      heartbeat = setInterval(() => send(`: ping\n\n`), HEARTBEAT_MS);
      request.signal.addEventListener("abort", teardown);
    },
    cancel() {
      closed = true;
      if (heartbeat) clearInterval(heartbeat);
      sub.disconnect();
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
