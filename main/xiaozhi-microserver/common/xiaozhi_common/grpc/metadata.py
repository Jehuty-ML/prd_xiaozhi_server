"""Optional metadata interceptor (client_id / message_id).

Phase-1 services primarily pass ids via request fields; this helper is available
for services that want gRPC metadata propagation later.
"""

from __future__ import annotations

from typing import Any


def extract_metadata(context: Any) -> tuple[str, str]:
    md = dict(context.invocation_metadata() or [])
    return md.get("client_id", ""), md.get("message_id", "")
