"""Live streaming: CLOB market WebSocket -> in-memory book -> arb re-eval -> Redis pub/sub.

The standalone scan engine (``app.signals.engine``) re-fetches books over HTTP every
``scan_interval_s`` and persists; this package keeps the books *live* off the WS delta feed
and publishes high-net-edge arb signals to Redis for the dashboard's SSE stream. Stream-only:
it never writes the ``signals`` table (the scan engine stays the source of persisted rows).
"""
