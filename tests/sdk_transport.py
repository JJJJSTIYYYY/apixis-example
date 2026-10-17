"""Keep real SDK serialization and request ownership on an offline transport."""

from contextlib import contextmanager
from unittest.mock import patch

import httpx


@contextmanager
def sdk_transport(handler):
    """Inject HTTPX transport without depending on MyLLM's internal imports."""
    transport = httpx.MockTransport(handler)
    clients = []
    original_init = httpx.AsyncClient.__init__

    def init(client, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs["trust_env"] = False
        original_init(client, *args, **kwargs)
        clients.append(client)

    with patch.object(httpx.AsyncClient, "__init__", init):
        yield clients
