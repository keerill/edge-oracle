"""Async CLOB client — POST a signed order to ``/order``. Network I/O via an INJECTED client.

The HTTP client (an ``httpx.AsyncClient``) is passed in, so this module imports without httpx at
runtime and tests drive it through ``httpx.MockTransport`` (no network, no creds). The order
payload + L2 headers are built by :mod:`app.clob.order` / :mod:`app.clob.auth`; this layer is just
the transport + error surfacing. Responses are untrusted — surfaced as a dict for the caller to
validate.

This is the Route-C deliverable: the live wire is built + tested against a mock, but NOT yet called
from the execution pipeline (``relay.submit`` stays dry-run). Flipping it on is a creds-gated step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # import only for typing — keeps httpx out of the runtime import graph
    import httpx


class ClobError(Exception):
    """A non-2xx response from the CLOB API (carries the status + body for the audit log)."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"CLOB /order returned {status}: {body}")
        self.status = status
        self.body = body


async def place_order(
    client: httpx.AsyncClient,
    base_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """POST the signed-order ``payload`` with L2 ``headers`` to ``{base_url}/order``.

    Returns the parsed JSON response (untrusted — caller validates). Raises :class:`ClobError`
    on a non-2xx status."""
    response = await client.post(f"{base_url}/order", json=payload, headers=headers)
    if response.status_code >= 400:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        raise ClobError(response.status_code, body)
    return response.json()
