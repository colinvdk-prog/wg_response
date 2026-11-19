"""tests/test_operator_paging.py — THE OPERATOR PAGE AND THE ON-CALL ROTA
(2026-11-19, founder: "build the operator page and on-call rota next";
contracts SAFETY SEMANTICS 63; cfg.operator_paging; wgresponse.paging).

  ROTA      people + shifts (primary, secondary) + season; the covering
            shift that started last is on call; problems are listed, never
            fatal; a re-read that fails keeps the last good rota.
  PAGE      a trigger pages the primary at once; no acknowledgement in 15
            min pages the secondary and the primary again; then both every
            15 min until repeat_max rounds -> UNANSWERED (still open).
  ONCE      one page per key: an open page gains homes without paging
            again; a key acknowledged within 6 h is noted, not paged.
  NOBODY    with nobody on call the page is still raised and audited.
  TRIGGERS  satellite_heat, new_fire_near_homes and alert step UPS to
            EXTREME_24H / IMPACTED (one page per fire and state); never a
            step down; the trigger list is honoured.
  ACK       the console (authed) or the signed link: GET shows the page and
            a button and acknowledges nothing; POST acknowledges; a forged or
            expired link is refused; the first acknowledgement stands.
  DURABLE   pages.json + the audit; a restored pager keeps escalating.
  PRIVATE   contact details never reach the log, the state block, the page
            store or the audit.
  WIRING    the daemon pages on its cycle and on between-cycle events; the
            flag, the note, config validation; knob off = no paging block."""
from __future__ import annotations

import argparse
import http.client
import json
import logging
import threading
import urllib.parse
from types import SimpleNamespace

import pytest

from wgresponse import cli, paging, server
from wgresponse.contracts import (AlertEvent, AlertState, OperatorEvent,
                                  ResponseConfig, validate_config)

T0 = 1_795_100_000.0
SECRET_SMS = "+15550001111"
SECRET_MAIL = "primary-person@example.invalid"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def rota_obj(**kw):
    obj = {"version": 1, "season": [],
           "people": {"a": {"name": "Alex", "sms": SECRET_SMS,
                            "email": SECRET_MAIL},
                      "b": {"name": "Blair", "sms": "+15550002222"},
                      "c": {"name": "Casey"}},
           "shifts": [{"start": "2026-11-01T00:00:00Z",
                       "end": "2027-01-01T00:00:00Z",
                       "primary": "a", "secondary": "b"}]}
    obj.update(kw)
    return obj


class FakeMirror:
    bucket = "bkt"

    def __init__(self):
        self.objects = {}

    def key_for(self, name):
        return "pfx/" + name

    def download(self, key):
        return self.objects.get(key)

    def upload(self, key, body, now_s=None):
        self.objects[key] = bytes(body)
        return True


class Recorder:
    """A channel that records what it was asked to send."""
    name = "recorder"

    def __init__(self):
        self.sent = []

    def send(self, person_id, person, page, tier, link):
        self.sent.append((person_id, tier, page["page_id"], link))
        return True, "recorded"


def make_pager(tmp_path, rota=None, cfg=None, clock=None, channel=None,
               mirror=None, path=True):
    clock = clock or Clock()
    tmp_path.mkdir(parents=True, exist_ok=True)
    rp = tmp_path / "rota.json"
    if rota is not None:
        rp.write_text(json.dumps(rota), encoding="utf-8")
    src = paging.RotaSource(path=str(rp) if path else None, mirror=mirror,
                            clock=clock)
    cfg = cfg or ResponseConfig(operator_paging=True)
    p = paging.Pager(cfg, rota_source=src, channel=channel or Recorder(),
                     state_dir=str(tmp_path / "state"), mirror=mirror,
                     clock=clock, key=b"k" * 32, base_url="https://watch.test")
    return p, clock, src


def heat_ev(iid="firms:1", km=3.2, client="c-1"):
    return OperatorEvent(t_s=T0, kind="satellite_heat",
                         message=f"satellite heat {km} km from Home One",
                         incident_id=iid,
                         detail={"nearest_m": km * 1000.0,
                                 "nearest_client": "Home One",
                                 "nearest_client_id": client})


# -- the rota ---------------------------------------------------------------

def test_rota_on_call_season_and_problems():
    obj = rota_obj(
        shifts=[{"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z",
                 "primary": "a", "secondary": "b"},
                {"start": "2026-11-10T00:00:00Z", "end": "2026-11-12T00:00:00Z",
                 "primary": "c"},
                {"start": "bad", "end": "2026-11-12T00:00:00Z", "primary": "a"},
                {"start": "2026-11-12T00:00:00Z", "end": "2026-11-11T00:00:00Z",
                 "primary": "a"},
                {"start": "2026-12-01T00:00:00Z", "end": "2026-12-02T00:00:00Z",
                 "primary": "zz"}],
        season=[{"start": "2026-05-01", "end": "2026-11-30"}])
    rota, problems = paging.Rota.parse(obj)
    t = paging._parse_time
    assert rota.on_call(t("2026-11-05T12:00:00Z")) == ("a", "b")
    assert rota.on_call(t("2026-11-11T00:00:00Z")) == ("c", None)  # newer shift
    assert rota.on_call(t("2027-02-01T00:00:00Z")) == (None, None)
    assert rota.on_call(t("2026-12-01T06:00:00Z")) == (None, None)  # unknown id
    assert rota.in_season(t("2026-11-30T23:00:00Z"))               # end inclusive
    assert not rota.in_season(t("2026-12-01T00:30:00Z"))
    assert len(problems) == 3 and any("zz" in p for p in problems)
    assert paging.Rota.parse(rota_obj(season=[]))[0].in_season(0.0)


def test_rota_source_reload_keeps_the_last_good_rota(tmp_path, caplog):
    clock = Clock()
    rp = tmp_path / "rota.json"
    src = paging.RotaSource(path=str(rp), clock=clock)
    assert src.rota() is None and src.status(T0)["error"] == "no rota file"
    rp.write_text(json.dumps(rota_obj()), encoding="utf-8")
    assert src.rota() is None                     # within the reload interval
    clock.t += 61
    assert src.rota().on_call(T0) == ("a", "b")
    rp.write_text("{not json", encoding="utf-8")
    clock.t += 61
    with caplog.at_level(logging.ERROR):
        assert src.rota().on_call(T0) == ("a", "b")
    st = src.status(T0)
    assert "not JSON" in st["error"] and st["on_call"]["primary_name"] == "Alex"
    assert SECRET_SMS not in json.dumps(st) and SECRET_MAIL not in json.dumps(st)


def test_rota_from_the_s3_state_prefix():
    m = FakeMirror()
    m.objects["pfx/oncall/rota.json"] = json.dumps(rota_obj()).encode()
    src = paging.RotaSource(mirror=m, clock=Clock())
    assert src.rota().on_call(T0) == ("a", "b")
    assert src.where == "s3://bkt/pfx/oncall/rota.json"


# -- the page and its escalation ---------------------------------------------

def test_primary_then_secondary_then_both_until_unanswered(tmp_path):
    cfg = ResponseConfig(operator_paging=True, paging_repeat_max=3)
    ch = Recorder()
    p, clock, _ = make_pager(tmp_path, rota_obj(), cfg=cfg, channel=ch)
    pid = p.observe_operator_event(heat_ev())
    assert [s[:2] for s in ch.sent] == [("a", "primary")]
    assert ch.sent[0][3].startswith("https://watch.test/page/ack?t=")
    clock.t += 14 * 60
    assert p.tick() == 0
    clock.t += 61
    assert p.tick() == 1
    assert [s[:2] for s in ch.sent[1:]] == [("a", "primary"), ("b", "secondary")]
    clock.t += 15 * 60
    assert p.tick() == 1 and len(ch.sent) == 5
    clock.t += 15 * 60
    p.tick()                                       # 3 rounds sent: unanswered
    page = next(x for x in p.status()["pages"] if x["page_id"] == pid)
    assert page["status"] == "unanswered" and page["sends"] == 3
    assert p.status()["open"] == 1                 # still open on the console
    clock.t += 60 * 60
    assert p.tick() == 0 and len(ch.sent) == 5     # no more rounds


def test_acknowledged_pages_stop_and_repeat_quietly(tmp_path):
    ch = Recorder()
    p, clock, _ = make_pager(tmp_path, rota_obj(), channel=ch)
    pid = p.observe_operator_event(heat_ev())
    # the same fire again while open: homes added, no new send
    assert p.observe_operator_event(heat_ev(client="c-2")) == pid
    assert len(ch.sent) == 1
    page = p.ack(pid, by="Alex", via="console")
    assert page["status"] == "acked" and page["acked_by"] == "Alex"
    assert page["homes"] == ["c-1", "c-2"]
    p.ack(pid, by="Someone else", via="console")   # the first one stands
    assert p.status()["pages"][0]["acked_by"] == "Alex"
    clock.t += 30 * 60
    assert p.tick() == 0 and len(ch.sent) == 1
    assert p.observe_operator_event(heat_ev()) is None      # within 6 h
    assert len(ch.sent) == 1
    clock.t += 7 * 3600
    assert p.observe_operator_event(heat_ev()) not in (None, pid)
    assert len(ch.sent) == 2


def test_nobody_on_call_still_raises_the_page(tmp_path):
    ch = Recorder()
    p, clock, _ = make_pager(tmp_path, None, channel=ch)     # no rota file
    pid = p.observe_operator_event(heat_ev())
    assert pid and ch.sent == []
    page = p.status()["pages"][0]
    assert page["status"] == "open"
    assert any("nobody on call" in h["what"] for h in page["history"])
    audit = (tmp_path / "state" / "oncall").glob("audit-*.jsonl")
    assert any("nobody on call" in f.read_text(encoding="utf-8")
               for f in audit)


# -- the triggers --------------------------------------------------------------

def _alert(cid, iid, prev, state, p10=3.0):
    return AlertEvent(t_s=T0, client_id=cid, incident_id=iid, prev=prev,
                      state=state, eta_p50_h=6.0, eta_p10_h=p10,
                      message=f"{state.value} for {cid}")


def test_alert_step_ups_page_once_per_fire_and_state(tmp_path):
    ch = Recorder()
    p, clock, _ = make_pager(tmp_path, rota_obj(), channel=ch)
    A = AlertState
    res = SimpleNamespace(operator_events=[heat_ev()], events=[
        _alert("h1", "F1", A.NONE, A.EXTREME_24H, 2.0),
        _alert("h2", "F1", A.WATCH, A.EXTREME_24H, 1.5),
        _alert("h3", "F1", A.EXTREME_24H, A.IMPACTED),
        _alert("h4", "F2", A.EXTREME_24H, A.WATCH),          # a step down
        _alert("h5", "F2", A.NONE, A.NOTICE_72H),            # not paged
        _alert("h6", "F3", A.EXTREME_24H, A.EXTREME_24H),    # no step
    ])
    ids = p.observe_cycle(res, names={"F1": "Ridge Fire"})
    pages = {x["key"]: x for x in p.status()["pages"]}
    assert set(pages) == {"satellite_heat:firms:1", "alert:F1:extreme_24h",
                          "alert:F1:impacted"}
    assert len(ids) == 3
    ext = pages["alert:F1:extreme_24h"]
    assert ext["homes"] == ["h1", "h2"]
    assert "Ridge Fire" in ext["title"] and "1.5 h" in ext["title"]


def test_the_trigger_list_is_honoured(tmp_path):
    cfg = ResponseConfig(operator_paging=True, paging_triggers="alert_step_up")
    p, _, _ = make_pager(tmp_path, rota_obj(), cfg=cfg)
    assert p.observe_operator_event(heat_ev()) is None
    sent = OperatorEvent(t_s=T0, kind="new_fire_near_homes", message="m",
                         incident_id="x", detail={"name": "N", "km": 2.0,
                                                  "client": "c1",
                                                  "source": "wfigs"})
    assert p.observe_operator_event(sent) is None
    cfg2 = ResponseConfig(operator_paging=True)
    p2, _, _ = make_pager(tmp_path / "two", rota_obj(), cfg=cfg2)
    pid = p2.observe_operator_event(sent)
    assert pid and "NEW AGENCY FIRE 'N'" in p2.status()["pages"][0]["title"]


# -- the link ------------------------------------------------------------------

def test_links_are_signed_and_expire(tmp_path):
    p, clock, _ = make_pager(tmp_path, rota_obj())
    pid = p.observe_operator_event(heat_ev())
    link = p.ack_link(pid, "b", clock.t)
    tok = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)["t"][0]
    view = p.check_link(tok)
    assert view["page_id"] == pid and view["link_person_name"] == "Blair"
    forged = tok[:-2] + ("00" if not tok.endswith("00") else "11")
    with pytest.raises(ValueError):
        p.check_link(forged)
    clock.t += paging.LINK_TTL_S + 1
    with pytest.raises(ValueError):
        p.check_link(tok)


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", "k-123")
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    p, clock, _ = make_pager(tmp_path, rota_obj())
    s = server.serve(0, state_provider=lambda: {"paging": p.status()})
    s.app.pager = p
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    yield SimpleNamespace(pager=p, clock=clock, port=s.server_address[1])
    s.shutdown()
    th.join(timeout=5)
    s.server_close()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8")
    finally:
        c.close()


def test_get_shows_the_page_and_only_post_acknowledges(srv):
    pid = srv.pager.observe_operator_event(heat_ev())
    link = srv.pager.ack_link(pid, "a", srv.clock.t)
    q = urllib.parse.urlparse(link).query
    tok = urllib.parse.parse_qs(q)["t"][0]
    st, body = _req(srv.port, "GET", "/page/ack?" + q)
    assert st == 200 and "Acknowledge as Alex" in body
    assert "<script" not in body
    assert srv.pager.status()["pages"][0]["status"] == "open"   # GET did not ack
    st, body = _req(srv.port, "POST", "/page/ack",
                    body=urllib.parse.urlencode({"t": tok}),
                    headers={"Content-Type":
                             "application/x-www-form-urlencoded"})
    assert st == 200 and "Acknowledged by Alex (a)" in body
    page = srv.pager.status()["pages"][0]
    assert page["status"] == "acked" and page["acked_via"] == "link"
    st, _ = _req(srv.port, "GET", "/page/ack?t=nonsense.00")
    assert st == 400
    st, body = _req(srv.port, "GET", "/healthz")
    assert json.loads(body)["pages_open"] == 0


def test_console_acknowledgement_is_authed(srv):
    pid = srv.pager.observe_operator_event(heat_ev())
    body = json.dumps({"page_id": pid, "by": "Ops desk"})
    st, _ = _req(srv.port, "POST", "/api/pages/ack", body=body,
                 headers={"Content-Type": "application/json"})
    assert st == 401
    st, _ = _req(srv.port, "GET", "/api/pages")
    assert st == 401
    st, out = _req(srv.port, "POST", "/api/pages/ack", body=body,
                   headers={"Content-Type": "application/json",
                            "X-Internal-API-Key": "k-123"})
    assert st == 200 and json.loads(out)["acked_by"] == "Ops desk"
    st, out = _req(srv.port, "POST", "/api/pages/ack",
                   body=json.dumps({"page_id": "nope"}),
                   headers={"Content-Type": "application/json",
                            "X-Internal-API-Key": "k-123"})
    assert st == 404


def test_paging_routes_404_without_a_pager(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", "k-123")
    s = server.serve(0, state_provider=lambda: {})
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    try:
        assert _req(s.server_address[1], "GET", "/page/ack?t=x")[0] == 404
        assert _req(s.server_address[1], "GET", "/api/pages",
                    headers={"X-Internal-API-Key": "k-123"})[0] == 404
    finally:
        s.shutdown()
        th.join(timeout=5)
        s.server_close()


# -- durable and private ---------------------------------------------------------

def test_open_pages_survive_a_restart_and_keep_escalating(tmp_path):
    m = FakeMirror()
    ch = Recorder()
    p, clock, _ = make_pager(tmp_path, rota_obj(), channel=ch, mirror=m)
    pid = p.observe_operator_event(heat_ev())
    assert "pfx/oncall/pages.json" in m.objects
    ch2 = Recorder()
    p2 = paging.Pager(ResponseConfig(operator_paging=True),
                      rota_source=paging.RotaSource(
                          path=str(tmp_path / "rota.json"), clock=clock),
                      channel=ch2, state_dir=str(tmp_path / "state"),
                      mirror=m, clock=clock, key=b"k" * 32)
    assert p2.restore() == 1
    clock.t += 16 * 60
    assert p2.tick() == 1
    assert [s[:2] for s in ch2.sent] == [("a", "primary"), ("b", "secondary")]
    assert p2.status()["pages"][0]["page_id"] == pid


def test_contact_details_never_leave_the_channel(tmp_path, caplog):
    m = FakeMirror()
    with caplog.at_level(logging.DEBUG):
        p, clock, _ = make_pager(tmp_path, rota_obj(),
                                 channel=paging.DryChannel(), mirror=m)
        pid = p.observe_operator_event(heat_ev())
        clock.t += 16 * 60
        p.tick()
        p.ack(pid, by="Alex", via="console")
    blobs = [caplog.text, json.dumps(p.status())]
    blobs += [b.decode("utf-8") for b in m.objects.values()]
    for f in (tmp_path / "state" / "oncall").iterdir():
        blobs.append(f.read_text(encoding="utf-8"))
    for b in blobs:
        assert SECRET_SMS not in b and SECRET_MAIL not in b
        assert "page/ack?t=" not in b              # no link in any log or store
    assert "PAGE (dry channel — nothing sent)" in caplog.text


# -- the daemon, the flag, the config -----------------------------------------

def test_flag_note_and_config():
    cfg, notes = cli._feeds_cfg(ResponseConfig(),
                                argparse.Namespace(operator_paging=True))
    assert cfg.operator_paging
    assert any("SAFETY SEMANTICS 63" in n and "DRY" in n for n in notes)
    assert not validate_config(cfg) or not any(
        "paging" in x for x in validate_config(cfg))
    for bad in (dict(paging_ack_min=2.0), dict(paging_repeat_max=0),
                dict(paging_triggers="satellite_heat,email_everyone"),
                dict(paging_triggers="")):
        assert any("paging" in x for x in validate_config(
            ResponseConfig(**bad)))
    cfg0, notes0 = cli._feeds_cfg(ResponseConfig(), argparse.Namespace())
    assert not cfg0.operator_paging and not any("63" in n for n in notes0)


def test_the_daemon_pages_between_cycles_and_after_each_cycle(tmp_path):
    from tests.test_cli import _daemon
    d, _, _, _, _ = _daemon(tmp_path)
    assert "paging" not in d.state()               # knob off: no block
    seen = []

    class FakePager:
        def observe_operator_event(self, ev):
            seen.append(("event", ev.kind))

        def observe_cycle(self, res, names):
            seen.append(("cycle", sorted(names)))

        def status(self):
            return {"open": 0, "pages": []}
    d.attach_pager(FakePager())
    d.add_operator_event(heat_ev())
    d.run_cycle()
    assert seen[0] == ("event", "satellite_heat")
    assert seen[1][0] == "cycle" and seen[1][1] == ["inc-1"]
    assert d.state()["paging"] == {"open": 0, "pages": []}


def test_the_template_rota_parses_cleanly():
    from pathlib import Path
    obj = json.loads((Path(__file__).resolve().parent.parent / "data" /
                      "oncall" / "rota.template.json").read_text("utf-8"))
    rota, problems = paging.Rota.parse(obj)
    assert problems == []
    assert rota.on_call(T0) == ("op-primary", "op-secondary")
    assert rota.in_season(T0)                      # [] = all year
    for p in obj["people"].values():
        assert not p.get("sms") and not p.get("email")
