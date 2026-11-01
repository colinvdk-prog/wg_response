"""tests/test_net_retry.py — ONE RETRY WHEN THE NETWORK FAILED (founder
2026-11-01: "add the DNS retry fix"; wgresponse.net).

  UNSENT    a name that did not resolve or a refused connection is tried
            once more, after the pause — for every adapter.
  DROPPED   a reset or cut-off answer is tried once more only where the
            request may be repeated (feeds, weather; never terrain, whose
            LFPS job submission is a GET that creates a job).
  NEVER     an HTTP answer or a timeout; a second failure raises as the
            first would have; one pause at most.
  QUIET     the warning names the host, never the path or query (FIRMS
            URLs carry the MAP_KEY, LANDFIRE's the contact email).
  WIRED     the feeds, weather and terrain default getters use it."""
from __future__ import annotations

import http.client
import io
import logging
import socket
import urllib.error

import pytest

from wgresponse import feeds, net, terrain, weather

URL = ("https://firms.modaps.eosdis.nasa.gov/api/area/csv/SECRETKEY/"
       "VIIRS_NOAA20_NRT/world/2")


class _Resp:
    """Stand-in for urlopen's response (context manager, getcode, read)."""

    def __init__(self, body=b"ok", status=200, fail_read=None):
        self._body, self._status, self._fail = body, status, fail_read

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self._status

    def read(self):
        if self._fail is not None:
            raise self._fail
        return self._body


def _opener(*outcomes):
    """Each call takes the next outcome: an exception to raise, or a _Resp."""
    calls = []

    def open_(req, timeout=None):
        calls.append((req.full_url, timeout))
        out = outcomes[len(calls) - 1]
        if isinstance(out, BaseException):
            raise out
        return out
    open_.calls = calls
    return open_


def _dns():
    return urllib.error.URLError(
        socket.gaierror(-3, "Temporary failure in name resolution"))


def _get(opener, **kw):
    pauses = []
    out = net.get(URL, {"Accept": "text/csv"}, 30.0, opener=opener,
                  sleep=pauses.append, **kw)
    return out, pauses


def test_a_name_that_did_not_resolve_is_tried_once_more():
    op = _opener(_dns(), _Resp(b"rows"))
    out, pauses = _get(op)
    assert out == (200, b"rows")
    assert pauses == [net.RETRY_PAUSE_S] and len(op.calls) == 2
    assert op.calls[1] == (URL, 30.0)


def test_a_refused_connection_is_tried_once_more_even_where_drops_are_not():
    op = _opener(urllib.error.URLError(ConnectionRefusedError(111, "refused")),
                 _Resp())
    out, pauses = _get(op, retry_dropped=False)
    assert out == (200, b"ok") and len(pauses) == 1


def test_a_dropped_answer_is_retried_only_where_it_may_be_repeated():
    reset = ConnectionResetError(104, "Connection reset by peer")
    assert _get(_opener(_Resp(fail_read=reset), _Resp(b"again")))[0] == \
        (200, b"again")
    assert _get(_opener(_Resp(fail_read=http.client.RemoteDisconnected(
        "Remote end closed connection")), _Resp()))[0] == (200, b"ok")
    assert _get(_opener(_Resp(fail_read=http.client.IncompleteRead(b"x", 9)),
                        _Resp()))[0] == (200, b"ok")
    op = _opener(_Resp(fail_read=reset), _Resp())
    with pytest.raises(ConnectionResetError):
        _get(op, retry_dropped=False)
    assert len(op.calls) == 1


def test_an_http_answer_or_a_timeout_is_never_retried():
    answer = urllib.error.HTTPError(URL, 503, "Service Unavailable", {},
                                    io.BytesIO(b""))
    for exc in (answer, urllib.error.URLError(socket.timeout("timed out")),
                TimeoutError("The read operation timed out")):
        op = _opener(exc, _Resp())
        pauses = []
        with pytest.raises(type(exc)):
            net.get(URL, opener=op, sleep=pauses.append)
        assert len(op.calls) == 1 and pauses == []


def test_a_second_failure_raises_as_the_first_would_have():
    op = _opener(_dns(), _dns(), _Resp())
    pauses = []
    with pytest.raises(urllib.error.URLError) as raised:
        net.get(URL, opener=op, sleep=pauses.append)
    assert isinstance(raised.value.reason, socket.gaierror)
    assert len(op.calls) == 2 and len(pauses) == 1


def test_the_warning_names_the_host_never_the_key(caplog):
    with caplog.at_level(logging.WARNING, logger="wgresponse.net"):
        _get(_opener(_dns(), _Resp()))
    assert "firms.modaps.eosdis.nasa.gov" in caplog.text
    assert "unsent" in caplog.text and "name resolution" in caplog.text
    assert "SECRETKEY" not in caplog.text and "/api/" not in caplog.text


def test_the_default_getters_use_it(monkeypatch):
    seen = []

    def fake(url, headers=None, timeout_s=30.0, **kw):
        seen.append((url, timeout_s, kw.get("retry_dropped", True)))
        return 200, b""
    monkeypatch.setattr(net, "get", fake)
    feeds._default_http_get("https://a.example/x")
    weather._default_http_get("https://b.example/y")
    terrain._default_http_get("https://c.example/z")
    assert seen == [("https://a.example/x", 30.0, True),
                    ("https://b.example/y", 30.0, True),
                    ("https://c.example/z", 120.0, False)]
