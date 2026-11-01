"""wgresponse.net — the live adapters' stdlib GET, with ONE retry after a
short pause when the network, not the server, failed (2026-11-01; founder:
"add the DNS retry fix").

world20's third live cycle went blind on WFIGS and one FIRMS satellite at
the same moment; the raw FIRMS log's blind event reads "<urlopen error
[Errno -3] Temporary failure in name resolution>" — the App Runner
instance briefly lost DNS. Each blind cycle freezes every pair of the
blind feed (SAFETY SEMANTICS 6: absence is not clearance) and the next
cycle rebuilds forecasts: the slow cycles seen since 2026-10-31.

What is retried, once, after RETRY_PAUSE_S:
  UNSENT    the name did not resolve (socket.gaierror) or the connection
            was refused — the request never reached the server, so a
            second try is always safe;
  DROPPED   the connection was reset, aborted or cut off mid-answer —
            only when the caller says the request may be repeated
            (retry_dropped; LANDFIRE's job submission is a GET that
            creates a job, so the terrain adapter says no).
Never retried: an HTTP answer (urllib.error.HTTPError — the server spoke)
and a timeout (a slow server stays slow; a second wait would double the
cycle's cost). A second failure raises exactly as the first would have:
the caller's blind-fetch handling is unchanged. The warning names the
host only — FIRMS URLs carry the MAP_KEY and LANDFIRE's the contact
email."""
from __future__ import annotations

import http.client
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

log = logging.getLogger(__name__)

#: the pause before the one retry (s)
RETRY_PAUSE_S = 3.0


def failure_kind(exc: BaseException) -> Optional[str]:
    """"unsent", "dropped" or None (not worth a retry) for a GET failure."""
    if isinstance(exc, urllib.error.HTTPError):
        return None                       # the server answered
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return None
    if isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
        return "unsent"
    if isinstance(reason, (ConnectionError, http.client.RemoteDisconnected,
                           http.client.IncompleteRead)):
        return "dropped"
    return None


def get(url: str, headers: Optional[dict] = None, timeout_s: float = 30.0,
        *, retry_dropped: bool = True,
        opener: Optional[Callable] = None,
        sleep: Callable[[float], None] = time.sleep,
        pause_s: float = RETRY_PAUSE_S) -> "tuple[int, bytes]":
    """Stdlib urllib GET -> (HTTP status, body bytes), retried once after
    pause_s when the network failed (module docstring); the second failure
    raises as the first would have."""
    open_ = opener or urllib.request.urlopen
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with open_(req, timeout=timeout_s) as resp:  # nosec: fixed URLs
                return resp.getcode(), resp.read()
        except Exception as exc:
            kind = failure_kind(exc)
            if attempt == 2 or kind is None or (kind == "dropped"
                                                and not retry_dropped):
                raise
            log.warning("GET to %s failed (%s: %s) — one retry in %g s",
                        urllib.parse.urlsplit(url).netloc or "?", kind,
                        getattr(exc, "reason", exc), pause_s)
            sleep(pause_s)
    raise AssertionError("unreachable")       # pragma: no cover
