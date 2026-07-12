"""wgresponse.notify — Notifier implementations (contracts.Notifier protocol).

LogNotifier is the default everywhere in tests, simulation and the live loop:
it records (event, client) pairs in memory and logs them.

SesNotifier (Sprint 2 C6, Council-1 OPS-9) is a REAL email channel behind the
default-OFF env flag ``WGRESP_NOTIFY_SES=1``:

* **Default OFF (fail closed):** the constructor always works (so config can
  be threaded through today), but until the env flag is exactly ``"1"``,
  send() returns False and logs 'disabled' — loud, never silent, no I/O.
* **Direct ses:SendEmail:** docs/INTEGRATION.md section 3 documents the estate
  end-state (producer enqueues on the SQS BackgroundJobsQueue, the
  wg-assess-worker Lambda holding the SES IAM performs the send). That
  enqueue-style delivery is Phase 2; a direct boto3 ``ses:SendEmail`` is the
  accepted interim. ``queue_url`` is stored for the Phase-2 switch. boto3 is
  an OPTIONAL import (module imports fine without it; a real send without
  boto3 fails loudly and returns False). SES sandbox caveat unchanged:
  verified recipients only until production access lands (estate item O-2).
* **Consent gate (FAIL CLOSED):** an injected callable predicate
  ``consent(client) -> bool`` guards every send. The DEFAULT predicate DENIES
  everything — deny-without-explicit-consent — because ClientProperty carries
  no consent/suppression fields; at integration time the operator injects the
  estate's ``firerisk/consumer/consent.py::is_mailable(profile, purpose)``
  with a SERVICE purpose (fire-arrival alerts are operational: allowed under
  marketing unsubscribe, still blocked by BLOCK-ALL suppressions —
  hard_bounce, complaint, manual, ccpa_delete). A predicate that RAISES also
  fails closed.
* **Send ledger + dedupe (at-least-once, no dup):** successful sends append
  to an in-memory ledger and optionally to a JSON-lines file
  (``ledger_path``, loaded at construction so dedupe survives restart). The
  dedupe key is ``(client_id, incident_id, state)``: a FAILED send is NOT
  recorded, so the next alert evaluation may retry (at-least-once), while a
  recorded key is never re-emailed (no dup — restart re-fires nothing,
  matching the persist-seam discipline in contracts). Note the key is
  state-level by design (per ticket C6): a pair that legally re-enters a
  previously-notified state (e.g. EXTREME -> WATCH -> EXTREME) is deduped.
* **Retry x2 with backoff:** each send attempts up to 1 + ``retries`` (=2)
  times, sleeping ``backoff_s * 2**attempt`` between attempts (sleep is
  injectable for tests; no wall-clock reads — ledger timestamps use
  event.t_s).

Sender identity: env SES_SENDER convention in the estate; the live identity
is pending no-reply@thewildfireguardian.com DKIM (the dev.wildfireguardian.io
default in the estate is stale).

SmsNotifier stays a deliberate STUB: no SMS channel exists anywhere in the
estate ("Phase 2" per firerisk/integrations/ses.py); provider choice is still
open (SNS, Pinpoint, or Twilio would all slot into the same
enqueue->SQS->worker pattern). EXTREME_24H alerts are the driving requirement
for this channel. Constructor works; send() raises RuntimeError so nothing
silently no-ops.

Units: AlertEvent timestamps are unix seconds; ETA fields are hours.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Callable, List, Optional, Tuple

from .contracts import AlertEvent, ClientProperty

logger = logging.getLogger(__name__)

_NOT_WIRED = "not wired: see docs/INTEGRATION.md"


class LogNotifier:
    """In-memory + logging notifier; the default in tests and simulation.

    send() appends (event, client) to .sent (in call order) and logs at INFO.
    Always returns True. No I/O, no network.
    """

    def __init__(self) -> None:
        self.sent: List[Tuple[AlertEvent, ClientProperty]] = []

    def send(self, event: AlertEvent, client: ClientProperty) -> bool:
        """Record and log one alert event (event.t_s unix s; ETA fields hours)."""
        self.sent.append((event, client))
        logger.info(
            "NOTIFY %s->%s client=%s incident=%s: %s",
            event.prev.value,
            event.state.value,
            event.client_id,
            event.incident_id,
            event.message,
        )
        return True


def _deny_all_consent(client: ClientProperty) -> bool:
    """Default SesNotifier consent predicate: DENY (fail closed).

    ClientProperty carries no consent/suppression fields, so without an
    injected predicate there is no explicit consent to honor — and a fire
    alert channel must never guess. Operators inject the estate's
    is_mailable(profile, purpose='service') gate at integration time
    (docs/INTEGRATION.md section 3)."""
    return False


class SesNotifier:
    """Email notifier over AWS SES, default-OFF behind env WGRESP_NOTIFY_SES.

    See the module docstring for the full behavior contract (flag gate,
    consent gate, ledger/dedupe, retry). send() return semantics:

    * flag not "1"          -> False (logs 'disabled'; nothing touched)
    * dedupe hit            -> True  (already delivered for this
                                      (client, incident, state); no resend)
    * consent denied/raised -> False (FAIL CLOSED; no send attempted)
    * no contact_email      -> False (nowhere to deliver)
    * SES client unavailable-> False (boto3 missing and none injected)
    * all attempts failed   -> False (NOT ledgered; later calls may retry)
    * delivered             -> True  (ledgered in memory + optional file)

    Constructor never performs AWS work; ``ses_client`` is injectable (tests
    MUST inject a fake — never call live AWS from the suite).
    """

    ENV_FLAG = "WGRESP_NOTIFY_SES"

    def __init__(
        self,
        sender: str = "",
        queue_url: str = "",
        region: str = "us-west-2",
        consent: Optional[Callable[[ClientProperty], bool]] = None,
        ledger_path: Optional[str] = None,
        ses_client=None,
        retries: int = 2,
        backoff_s: float = 1.0,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.sender = sender          # SES verified identity (env SES_SENDER)
        self.queue_url = queue_url    # BackgroundJobsQueue URL (Phase-2 enqueue path)
        self.region = region
        self._consent = consent if consent is not None else _deny_all_consent
        self.ledger_path = ledger_path
        self._ses_client = ses_client
        self.retries = max(0, int(retries))
        self.backoff_s = float(backoff_s)
        self._sleep = sleep if sleep is not None else time.sleep
        self.ledger: List[dict] = []          # successful sends, in call order
        self._sent_keys: set = set()          # {(client_id, incident_id, state)}
        if ledger_path:
            self._load_ledger(ledger_path)

    # ------------------------------------------------------------------ send

    @property
    def enabled(self) -> bool:
        """True iff env WGRESP_NOTIFY_SES is exactly '1' (read per call so
        the flag can be flipped without reconstructing the notifier)."""
        return os.environ.get(self.ENV_FLAG, "") == "1"

    def send(self, event: AlertEvent, client: ClientProperty) -> bool:
        """Deliver one alert email per the contract in the class docstring."""
        key = (event.client_id, event.incident_id, event.state.value)
        if not self.enabled:
            logger.warning(
                "SES notifier disabled (%s != '1'): alert %s/%s (%s) NOT "
                "emailed", self.ENV_FLAG, event.client_id, event.incident_id,
                event.state.value)
            return False
        if key in self._sent_keys:
            logger.info(
                "SES dedupe: %s/%s (%s) already in the send ledger; duplicate "
                "suppressed (at-least-once-no-dup)", event.client_id,
                event.incident_id, event.state.value)
            return True
        try:
            allowed = bool(self._consent(client))
        except Exception:
            logger.exception(
                "SES consent predicate raised for client %s; FAIL CLOSED — "
                "alert %s/%s (%s) NOT emailed", event.client_id,
                event.client_id, event.incident_id, event.state.value)
            return False
        if not allowed:
            logger.warning(
                "SES consent DENIED for client %s (fail closed; default "
                "predicate denies without explicit consent — inject the "
                "estate is_mailable gate): alert %s/%s (%s) NOT emailed",
                event.client_id, event.client_id, event.incident_id,
                event.state.value)
            return False
        to_addr = (client.contact_email or "").strip()
        if not to_addr:
            logger.warning(
                "SES: client %s has no contact_email; alert %s/%s (%s) NOT "
                "emailed", event.client_id, event.client_id,
                event.incident_id, event.state.value)
            return False
        try:
            ses = self._client()
        except Exception as exc:
            logger.error(
                "SES client unavailable (%s): alert %s/%s (%s) NOT emailed — "
                "install boto3 or inject ses_client", exc, event.client_id,
                event.incident_id, event.state.value)
            return False

        subject, body = self._render(event, client)
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                resp = ses.send_email(
                    Source=self.sender,
                    Destination={"ToAddresses": [to_addr]},
                    Message={
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                    },
                )
                message_id = ""
                if isinstance(resp, dict):
                    message_id = str(resp.get("MessageId", ""))
                self._record(key, event, to_addr, message_id)
                logger.info(
                    "SES sent %s/%s (%s) to %s (MessageId=%s)",
                    event.client_id, event.incident_id, event.state.value,
                    to_addr, message_id)
                return True
            except Exception as exc:
                if attempt < attempts - 1:
                    delay = self.backoff_s * (2.0 ** attempt)
                    logger.warning(
                        "ses:SendEmail attempt %d/%d failed for %s/%s (%s): "
                        "%s — retrying in %.1f s", attempt + 1, attempts,
                        event.client_id, event.incident_id,
                        event.state.value, exc, delay)
                    self._sleep(delay)
                else:
                    logger.error(
                        "ses:SendEmail FAILED after %d attempts for %s/%s "
                        "(%s): %s — NOT ledgered; a later evaluation may "
                        "retry (at-least-once)", attempts, event.client_id,
                        event.incident_id, event.state.value, exc)
        return False

    # ------------------------------------------------------------- internals

    def _client(self):
        """Injected SES client, else a lazily-built boto3 one (optional dep:
        the module must import — and the disabled path must run — without
        boto3 installed)."""
        if self._ses_client is None:
            import boto3  # deliberate lazy import (see docstring)
            self._ses_client = boto3.client("ses", region_name=self.region)
        return self._ses_client

    @staticmethod
    def _render(event: AlertEvent, client: ClientProperty) -> Tuple[str, str]:
        """Deterministic (subject, plain-text body) for one AlertEvent."""
        name = client.name or client.client_id
        level = event.state.value.replace("_", " ").upper()
        subject = f"[Wildfire Guardian] {level} — {name}"
        lines = [
            event.message
            or f"Alert level for {name} is now {event.state.value}.",
            "",
            f"Incident: {event.incident_id}",
            f"Alert level: {event.prev.value} -> {event.state.value}",
        ]
        if math.isfinite(event.eta_p50_h):
            lines.append(
                f"Projected fire arrival: ~{event.eta_p50_h:.1f} h (median), "
                f"{event.eta_p10_h:.1f} h (pessimistic)")
        lines += ["", "Wildfire Guardian — GraphGel Events",
                  "This is an operational fire-safety alert for a protected "
                  "property."]
        return subject, "\n".join(lines)

    def _record(self, key, event: AlertEvent, to_addr: str,
                message_id: str) -> None:
        """Ledger a successful send (memory + optional file append). A file
        failure is logged but never fails the send — the email went out; only
        restart-dedupe durability for this key is lost."""
        entry = {
            "t_s": event.t_s,             # event time, never wall clock
            "client_id": event.client_id,
            "incident_id": event.incident_id,
            "state": event.state.value,
            "to": to_addr,
            "message_id": message_id,
        }
        self._sent_keys.add(key)
        self.ledger.append(entry)
        if self.ledger_path:
            try:
                with open(self.ledger_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, sort_keys=True) + "\n")
            except OSError:
                logger.exception(
                    "SES send-ledger append failed (%s); email WAS sent — "
                    "dedupe for %s will not survive restart",
                    self.ledger_path, key)

    def _load_ledger(self, path: str) -> None:
        """Load dedupe keys from a JSON-lines ledger file; missing file is a
        fresh ledger, corrupt lines are skipped loudly (a bad line must never
        block alerting)."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            return
        except OSError:
            logger.exception(
                "SES send-ledger unreadable (%s); starting with an EMPTY "
                "dedupe set — previously-notified states may re-send once",
                path)
            return
        bad = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                key = (str(entry["client_id"]), str(entry["incident_id"]),
                       str(entry["state"]))
            except Exception:
                bad += 1
                continue
            self._sent_keys.add(key)
            self.ledger.append(entry)
        if bad:
            logger.warning("SES send-ledger %s: skipped %d corrupt line(s)",
                           path, bad)


class SmsNotifier:
    """STUB SMS notifier; provider choice (SNS/Pinpoint/Twilio) still open.

    No SMS channel exists in the estate yet ("Phase 2"). When chosen, the
    provider send slots into the same enqueue->SQS->worker pattern as SES.
    Constructor works; send() raises RuntimeError so nothing silently no-ops.
    """

    def __init__(self, provider: str = "", from_number: str = "") -> None:
        self.provider = provider        # "sns" | "pinpoint" | "twilio" | ""
        self.from_number = from_number  # E.164 sender, e.g. "+14155550100"

    def send(self, event: AlertEvent, client: ClientProperty) -> bool:
        """Not wired. Raises RuntimeError until an SMS provider is selected."""
        raise RuntimeError(_NOT_WIRED)
