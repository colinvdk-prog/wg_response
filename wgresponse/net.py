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
            A TLS connection the far end closed without a close_notify
            (ssl.SSLEOFError, "UNEXPECTED_EOF_WHILE_READING") counts as
            DROPPED (2026-11-19): world23's two blind cycles on the live
            watcher (01:06 and 02:54 UTC) began with exactly this error in
            the WFIGS handshake, raised straight through as "not worth a
            retry" while the same hosts' plain resets were retried and
            recovered. Classed DROPPED, not UNSENT: the exception does not
            say whether the handshake or the answer was cut, so a
            non-repeatable GET (terrain) is still never sent twice.
Never retried: an HTTP answer (urllib.error.HTTPError — the server spoke)
and a timeout (a slow server stays slow; a second wait would double the
cycle's cost). A second failure raises exactly as the first would have:
the caller's blind-fetch handling is unchanged. The warning names the
host only — FIRMS URLs carry the MAP_KEY and LANDFIRE's the contact
email.

GZIP (2026-11-18, the fire -> alert latency review, lane A finding A4;
cfg.feeds_gzip, SAFETY SEMANTICS 57): get(..., gzip=True) asks
Accept-Encoding: gzip and decompresses an answer whose Content-Encoding is
gzip (or x-gzip). A body that does not decompress — or an encoding we did
not ask for — RAISES (CorruptBodyError, never retried: the server spoke),
so the caller's transport-error path runs: a feed fetch goes blind
(SAFETY SEMANTICS 6), exactly as a failed GET does today. gzip=False (the
default) = byte-identical: no header added, nothing decoded.

TRANSFER STATS (2026-11-18, feed_timings telemetry): every successful get
leaves {status, body_bytes, wire_bytes, encoding, attempts} in a per-
THREAD slot, read back by last_transfer() on the same thread (the feed
adapters' per-request telemetry; the parallel feed fetch runs each adapter
on its own thread). Reading it changes nothing."""
from __future__ import annotations

import gzip as _gzip
import http.client
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Callable, Optional

log = logging.getLogger(__name__)

#: the pause before the one retry (s)
RETRY_PAUSE_S = 3.0

_TLS = threading.local()


class CorruptBodyError(ValueError):
    """A gzip answer that does not decompress, or an encoding we did not
    ask for (get(..., gzip=True)): the caller's transport-error path."""


def clear_transfer() -> None:
    """Forget this thread's last transfer (before a timed request)."""
    _TLS.last = None


def last_transfer() -> Optional[dict]:
    """This thread's last successful get: {status, body_bytes, wire_bytes,
    encoding, attempts} — None when none since clear_transfer()."""
    return getattr(_TLS, "last", None)


def _decode(body: bytes, encoding: Optional[str], netloc: str) -> bytes:
    """gzip=True: the answer's body as the server meant it (module
    docstring). Raises CorruptBodyError on a body that does not decode."""
    enc = (encoding or "").strip().lower()
    if enc in ("", "identity"):
        return body
    if enc not in ("gzip", "x-gzip"):
        raise CorruptBodyError(f"unrequested Content-Encoding {enc!r} from "
                               f"{netloc or '?'}")
    try:
        return _gzip.decompress(body)
    except (OSError, EOFError, zlib.error) as exc:
        raise CorruptBodyError(f"gzip body from {netloc or '?'} does not "
                               f"decompress ({type(exc).__name__})") from exc


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
                           http.client.IncompleteRead, ssl.SSLEOFError)):
        return "dropped"
    return None


def get(url: str, headers: Optional[dict] = None, timeout_s: float = 30.0,
        *, retry_dropped: bool = True,
        opener: Optional[Callable] = None,
        sleep: Callable[[float], None] = time.sleep,
        pause_s: float = RETRY_PAUSE_S,
        gzip: bool = False) -> "tuple[int, bytes]":
    """Stdlib urllib GET -> (HTTP status, body bytes), retried once after
    pause_s when the network failed (module docstring); the second failure
    raises as the first would have. gzip=True: Accept-Encoding gzip and the
    body decompressed (CorruptBodyError when it does not decode)."""
    open_ = opener or urllib.request.urlopen
    hdrs = dict(headers or {})
    if gzip and not any(k.lower() == "accept-encoding" for k in hdrs):
        hdrs["Accept-Encoding"] = "gzip"
    netloc = urllib.parse.urlsplit(url).netloc
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with open_(req, timeout=timeout_s) as resp:  # nosec: fixed URLs
                status, raw = resp.getcode(), resp.read()
                encoding = None
                if gzip:
                    h = getattr(resp, "headers", None)
                    encoding = h.get("Content-Encoding") if h is not None \
                        else None
                    body = _decode(raw, encoding, netloc)
                else:
                    body = raw
                _TLS.last = {"status": status, "body_bytes": len(body),
                             "wire_bytes": len(raw),
                             "encoding": encoding or None,
                             "attempts": attempt}
                return status, body
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
