// SSE endpoint: subscribe to the Redis signals channel and stream conflated updates to the
// dashboard. The quant stream engine publishes one AdvisedSignal JSON per high-net-edge arb
// detection (possibly at WS rates); we conflate server-side to <=10 frames/s so the client
// re-renders ~10/s, not 100/s. Every inbound message is Zod-validated (the quant payload is
// treated as untrusted), then forwarded as a standard `data:` SSE frame.

import Redis from "ioredis";
import { REDIS_URL, SIGNALS_CHANNEL } from "@/lib/env";
import { AdvisedSignalSchema } from "@/lib/schemas/signal";
import { Conflator } from "@/lib/stream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FLUSH_MS = 100; // conflation window -> <=10 frames/s
const HEARTBEAT_MS = 15_000; // keep proxies from closing an idle stream

export async function GET(request: Request) {
  const encoder = new TextEncoder();
  // Dedicated connection: a subscribed ioredis client can't run other commands.
  const sub = new Redis(REDIS_URL, { lazyConnect: true, maxRetriesPerRequest: null });
  const conflator = new Conflator();

  let flush: ReturnType<typeof setInterval> | undefined;
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
        if (flush) clearInterval(flush);
        if (heartbeat) clearInterval(heartbeat);
        sub.disconnect();
        try {
          controller.close();
        } catch {
          // already closed
        }
      };

      sub.on("message", (_channel, payload) => {
        const parsed = AdvisedSignalSchema.safeParse(JSON.parse(payload));
        if (parsed.success) conflator.push(parsed.data); // drop malformed frames silently
      });
      sub.on("error", teardown);

      try {
        await sub.connect();
        await sub.subscribe(SIGNALS_CHANNEL);
      } catch {
        teardown();
        return;
      }

      send(`: connected\n\n`); // open the stream immediately so EventSource fires `onopen`

      flush = setInterval(() => {
        for (const signal of conflator.drain()) {
          send(`data: ${JSON.stringify(signal)}\n\n`);
        }
      }, FLUSH_MS);

      heartbeat = setInterval(() => send(`: ping\n\n`), HEARTBEAT_MS);

      request.signal.addEventListener("abort", teardown);
    },
    cancel() {
      closed = true;
      if (flush) clearInterval(flush);
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
