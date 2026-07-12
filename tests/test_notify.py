"""Tests for wgresponse.notify — LogNotifier behavior, the Sprint 2 C6
SesNotifier (default-OFF env flag, consent fail-closed, send ledger dedupe,
retry x2 backoff — Council-1 OPS-9), and the SMS stub contract.

NO live AWS is ever touched: every send-path test injects a fake SES client,
and the boto3-missing test forces the import to fail.
"""
from __future__ import annotations

import json
import logging
import sys

import pytest

from wgresponse.contracts import (
    AlertEvent,
    AlertState,
    ClientProperty,
    LatLon,
)
from wgresponse.notify import LogNotifier, SesNotifier, SmsNotifier

ENV = SesNotifier.ENV_FLAG   # "WGRESP_NOTIFY_SES"


def make_event(state=AlertState.NOTICE_72H, prev=AlertState.NONE,
               client_id="c1", incident_id="f1"):
    return AlertEvent(
        t_s=1000.0,
        client_id=client_id,
        incident_id=incident_id,
        prev=prev,
        state=state,
        eta_p50_h=54.0,
        eta_p10_h=38.0,
        message="Projected fire arrival at Casa Blanca: ~54h (median), 38h (pessimistic).",
    )


def make_client(email="owner@example.com"):
    return ClientProperty(client_id="c1", loc=LatLon(34.1, -118.2),
                          name="Casa Blanca", contact_email=email)


class FakeSes:
    """Injectable stand-in for a boto3 SES client: records every send_email
    call, raising for the first `fail_times` calls (retry-path testing)."""

    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError(f"throttled (call {len(self.calls)})")
        return {"MessageId": f"mid-{len(self.calls)}"}


def ses_on(monkeypatch):
    monkeypatch.setenv(ENV, "1")


def make_ses(fake, **kw):
    """SesNotifier wired for tests: consent granted and sleep captured unless
    the test overrides them."""
    kw.setdefault("sender", "no-reply@thewildfireguardian.com")
    kw.setdefault("consent", lambda client: True)
    sleeps = []
    kw.setdefault("sleep", sleeps.append)
    n = SesNotifier(ses_client=fake, **kw)
    return n, sleeps


# ---------------------------------------------------------------------------
# LogNotifier — the default channel everywhere
# ---------------------------------------------------------------------------

def test_log_notifier_records_and_returns_true(caplog):
    n = LogNotifier()
    ev, cl = make_event(), make_client()
    with caplog.at_level(logging.INFO, logger="wgresponse.notify"):
        assert n.send(ev, cl) is True
    assert n.sent == [(ev, cl)]
    assert any("NOTIFY" in r.message for r in caplog.records)


def test_log_notifier_preserves_call_order():
    n = LogNotifier()
    ev1, ev2 = make_event(), make_event()
    cl = make_client()
    n.send(ev1, cl)
    n.send(ev2, cl)
    assert [e for e, _ in n.sent] == [ev1, ev2]


# ---------------------------------------------------------------------------
# SesNotifier — default OFF behind WGRESP_NOTIFY_SES (Sprint 2 C6, OPS-9)
# ---------------------------------------------------------------------------

def test_ses_disabled_by_default_returns_false_and_logs(monkeypatch, caplog):
    """Flag absent: constructor works, send() returns False and logs
    'disabled', and NOTHING is attempted (no consent call, no client call)."""
    monkeypatch.delenv(ENV, raising=False)
    fake = FakeSes()
    consent_calls = []
    n = SesNotifier(sender="no-reply@thewildfireguardian.com",
                    queue_url="https://sqs.us-west-2.amazonaws.com/x/background-jobs",
                    consent=lambda c: consent_calls.append(c) or True,
                    ses_client=fake)
    assert n.region == "us-west-2"          # constructor threads config today
    assert n.enabled is False
    with caplog.at_level(logging.DEBUG, logger="wgresponse.notify"):
        assert n.send(make_event(), make_client()) is False
    assert any("disabled" in r.message for r in caplog.records)
    assert fake.calls == []
    assert consent_calls == []
    assert n.ledger == []


def test_ses_flag_must_be_exactly_one(monkeypatch):
    fake = FakeSes()
    n, _ = make_ses(fake)
    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv(ENV, value)
        assert n.enabled is False
        assert n.send(make_event(), make_client()) is False
    assert fake.calls == []
    monkeypatch.setenv(ENV, "1")
    assert n.enabled is True
    assert n.send(make_event(), make_client()) is True
    assert len(fake.calls) == 1


def test_ses_default_consent_denies_fail_closed(monkeypatch, caplog):
    """No injected predicate => deny-without-explicit-consent: send refused,
    zero SES calls, loud log (ClientProperty has no consent fields; the
    estate is_mailable gate must be injected at integration time)."""
    ses_on(monkeypatch)
    fake = FakeSes()
    n = SesNotifier(sender="s@x.com", ses_client=fake)   # default consent
    with caplog.at_level(logging.WARNING, logger="wgresponse.notify"):
        assert n.send(make_event(), make_client()) is False
    assert fake.calls == []
    assert n.ledger == []
    assert any("consent" in r.message.lower() for r in caplog.records)


def test_ses_consent_predicate_exception_fails_closed(monkeypatch, caplog):
    ses_on(monkeypatch)
    fake = FakeSes()

    def broken(client):
        raise ValueError("consumer DB unreachable")

    n, _ = make_ses(fake, consent=broken)
    with caplog.at_level(logging.ERROR, logger="wgresponse.notify"):
        assert n.send(make_event(), make_client()) is False
    assert fake.calls == []
    assert any("FAIL CLOSED" in r.message for r in caplog.records)


def test_ses_sends_email_when_enabled_and_consented(monkeypatch):
    """Happy path: consent granted, one ses:SendEmail with the estate shapes
    (Source, ToAddresses, subject/body carrying the alert), ledger entry
    recorded with the SES MessageId."""
    ses_on(monkeypatch)
    fake = FakeSes()
    n, _ = make_ses(fake)
    ev, cl = make_event(), make_client()
    assert n.send(ev, cl) is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["Source"] == "no-reply@thewildfireguardian.com"
    assert call["Destination"] == {"ToAddresses": ["owner@example.com"]}
    subject = call["Message"]["Subject"]["Data"]
    body = call["Message"]["Body"]["Text"]["Data"]
    assert "NOTICE 72H" in subject and "Casa Blanca" in subject
    assert ev.message in body
    assert "f1" in body                       # incident id
    assert "54.0 h" in body and "38.0 h" in body
    assert len(n.ledger) == 1
    entry = n.ledger[0]
    assert entry["client_id"] == "c1"
    assert entry["incident_id"] == "f1"
    assert entry["state"] == "notice_72h"
    assert entry["to"] == "owner@example.com"
    assert entry["message_id"] == "mid-1"
    assert entry["t_s"] == ev.t_s             # event time, never wall clock


def test_ses_missing_contact_email_returns_false(monkeypatch, caplog):
    ses_on(monkeypatch)
    fake = FakeSes()
    n, _ = make_ses(fake)
    with caplog.at_level(logging.WARNING, logger="wgresponse.notify"):
        assert n.send(make_event(), make_client(email="")) is False
    assert fake.calls == []
    assert any("contact_email" in r.message for r in caplog.records)


def test_ses_dedupe_key_client_incident_state(monkeypatch, caplog):
    """Second send of the same (client, incident, state) is suppressed —
    returns True (already delivered) with ZERO extra SES calls; a DIFFERENT
    state for the same pair still sends (at-least-once-no-dup)."""
    ses_on(monkeypatch)
    fake = FakeSes()
    n, _ = make_ses(fake)
    ev = make_event(state=AlertState.NOTICE_72H)
    assert n.send(ev, make_client()) is True
    with caplog.at_level(logging.INFO, logger="wgresponse.notify"):
        assert n.send(make_event(state=AlertState.NOTICE_72H),
                      make_client()) is True
    assert len(fake.calls) == 1
    assert any("dedupe" in r.message.lower() for r in caplog.records)
    # different state / different incident / different client each send
    assert n.send(make_event(state=AlertState.WATCH,
                             prev=AlertState.NOTICE_72H), make_client()) is True
    assert n.send(make_event(incident_id="f2"), make_client()) is True
    assert n.send(make_event(client_id="c2"), make_client()) is True
    assert len(fake.calls) == 4
    assert len(n.ledger) == 4


def test_ses_retry_twice_with_backoff_then_success(monkeypatch):
    """Two throttled attempts, third succeeds: send() is True, exactly 3
    ses:SendEmail calls, backoff sleeps 1.0 s then 2.0 s (base * 2**attempt),
    single ledger entry."""
    ses_on(monkeypatch)
    fake = FakeSes(fail_times=2)
    n, sleeps = make_ses(fake)
    assert n.send(make_event(), make_client()) is True
    assert len(fake.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert len(n.ledger) == 1
    assert n.ledger[0]["message_id"] == "mid-3"


def test_ses_all_attempts_fail_not_ledgered_and_retryable(monkeypatch, caplog):
    """Every attempt fails: send() is False after 1+2 attempts, nothing is
    ledgered (at-least-once: a later evaluation retries — the failure is
    never deduped)."""
    ses_on(monkeypatch)
    fake = FakeSes(fail_times=99)
    n, sleeps = make_ses(fake)
    with caplog.at_level(logging.ERROR, logger="wgresponse.notify"):
        assert n.send(make_event(), make_client()) is False
    assert len(fake.calls) == 3               # 1 attempt + retry x2
    assert sleeps == [1.0, 2.0]
    assert n.ledger == []
    assert any("FAILED after 3 attempts" in r.message for r in caplog.records)
    # the SAME alert retries on the next call — no dedupe of failures
    assert n.send(make_event(), make_client()) is False
    assert len(fake.calls) == 6


def test_ses_file_ledger_dedupe_survives_restart(monkeypatch, tmp_path):
    """Ledger file: successful sends append JSON lines; a NEW notifier on the
    same path (process restart) loads the keys and suppresses duplicates
    while still sending genuinely new states."""
    ses_on(monkeypatch)
    path = tmp_path / "ses_ledger.jsonl"
    fake1 = FakeSes()
    n1, _ = make_ses(fake1, ledger_path=str(path))
    assert n1.send(make_event(), make_client()) is True
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert (entry["client_id"], entry["incident_id"], entry["state"]) == \
        ("c1", "f1", "notice_72h")

    # restart: same path, fresh notifier + fresh fake client
    fake2 = FakeSes()
    n2, _ = make_ses(fake2, ledger_path=str(path))
    assert n2.send(make_event(), make_client()) is True    # deduped
    assert fake2.calls == []
    assert n2.send(make_event(state=AlertState.WATCH,
                              prev=AlertState.NOTICE_72H), make_client()) is True
    assert len(fake2.calls) == 1
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_ses_corrupt_ledger_lines_skipped_loudly(monkeypatch, tmp_path, caplog):
    """A corrupt ledger line neither crashes construction nor poisons the
    valid keys around it."""
    ses_on(monkeypatch)
    path = tmp_path / "ses_ledger.jsonl"
    good = {"t_s": 1.0, "client_id": "c1", "incident_id": "f1",
            "state": "notice_72h", "to": "x@y.z", "message_id": "m"}
    path.write_text(json.dumps(good) + "\nnot-json{{{\n\n", encoding="utf-8")
    fake = FakeSes()
    with caplog.at_level(logging.WARNING, logger="wgresponse.notify"):
        n, _ = make_ses(fake, ledger_path=str(path))
    assert any("corrupt" in r.message for r in caplog.records)
    assert n.send(make_event(), make_client()) is True     # deduped via file
    assert fake.calls == []
    assert n.send(make_event(incident_id="f9"), make_client()) is True
    assert len(fake.calls) == 1


def test_ses_missing_ledger_file_is_fresh_start(monkeypatch, tmp_path):
    ses_on(monkeypatch)
    path = tmp_path / "does_not_exist_yet.jsonl"
    fake = FakeSes()
    n, _ = make_ses(fake, ledger_path=str(path))
    assert n.ledger == []
    assert n.send(make_event(), make_client()) is True
    assert path.is_file()


def test_ses_boto3_missing_fails_loud_not_crash(monkeypatch, caplog):
    """Enabled + consented but NO injected client and boto3 unimportable:
    send() returns False with a loud error — never an exception, never a
    silent drop. (sys.modules[boto3]=None forces ImportError even where
    boto3 is installed; no AWS is ever touched.)"""
    ses_on(monkeypatch)
    monkeypatch.setitem(sys.modules, "boto3", None)
    n = SesNotifier(sender="s@x.com", consent=lambda c: True)
    with caplog.at_level(logging.ERROR, logger="wgresponse.notify"):
        assert n.send(make_event(), make_client()) is False
    assert any("boto3" in r.message for r in caplog.records)
    assert n.ledger == []


def test_ses_disabled_send_never_touches_ledger_file(monkeypatch, tmp_path):
    """Flag OFF is inert: no file is created even with a ledger path wired."""
    monkeypatch.delenv(ENV, raising=False)
    path = tmp_path / "ledger.jsonl"
    fake = FakeSes()
    n, _ = make_ses(fake, ledger_path=str(path))
    assert n.send(make_event(), make_client()) is False
    assert fake.calls == []
    assert not path.exists()


# ---------------------------------------------------------------------------
# SMS stub contract + protocol shape
# ---------------------------------------------------------------------------

def test_sms_notifier_constructs_but_send_raises():
    n = SmsNotifier(provider="sns", from_number="+14155550100")
    assert n.provider == "sns"
    with pytest.raises(RuntimeError, match="not wired: see docs/INTEGRATION.md"):
        n.send(make_event(), make_client())


def test_no_channel_ever_silently_noops(monkeypatch, caplog):
    """Nothing silently no-ops: the SMS stub refuses loudly (raises), and the
    default-OFF SesNotifier is loud too — send() is False AND logs 'disabled'
    (a dropped fire-arrival alert must never be invisible)."""
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(RuntimeError):
        SmsNotifier().send(make_event(), make_client())
    with caplog.at_level(logging.WARNING, logger="wgresponse.notify"):
        assert SesNotifier().send(make_event(), make_client()) is False
    assert any("disabled" in r.message for r in caplog.records)


def test_all_impls_satisfy_notifier_protocol():
    # contracts.Notifier is a non-runtime Protocol, so check the shape:
    # a callable send accepting (event, client) positionally.
    import inspect

    for impl in (LogNotifier(), SesNotifier(), SmsNotifier()):
        send = getattr(impl, "send")
        assert callable(send)
        params = list(inspect.signature(send).parameters)
        assert params[:2] == ["event", "client"]
