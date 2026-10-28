"""tests/test_sim_run_report.py — THE RUN REPORT (founder 2026-10-27,
verbatim: "after simulation create a report - number of homes coated, number
of homes exposed to fire, number of homes not coated exposed to fire,
nearest time between crew and fire"; simui SimSession._report_state ->
state.report; docs/SIMULATOR_DESIGN.md "The run report (2026-10-27)").

  COUNTS   coated = the homes in the serviced ledger; reached = the homes the
           ACTUAL fire has reached (each fire's nominal member at the home's
           cell, or — ground that cannot burn — geo.erode_min of it at the
           ember-threat radius); reached_uncoated = reached homes not gelled
           before the fire got there: all recomputed here independently of
           the engine's bookkeeping.
  NEAREST  the nearest time between a crew and the fire equals a
           brute-force pass over every spot every crew was at (the path
           audit's samples of each tick's path_track, kept here tick by
           tick) against the final actual fire: closest ahead (the fire's
           arrival minus the moment the crew left), closest behind (the
           crew's arrival minus the fire's), and the spots the fire reached
           while a crew was there ('caught', 0 min — never hidden; the
           safety ledger reports the same crew).
  DISPLAY  the bookkeeping changes no decision: switched off, every tick's
  ONLY     state minus `report` is identical; a cmd_log replay reproduces
           the report byte for byte.
  PAGE     the real page renders in a headless Chromium (a LOUD skip when
           none is installed): the Report dialog shows state.report's
           numbers and its reached-homes table, and Pause on a running
           simulation opens it.

Logic tests run on the crew-placement tests' controlled grass tile and '+'
road graph (tests/test_sim_crew_placement.py)."""
from __future__ import annotations

import http.server
import json
import math
import subprocess
import threading

import pytest

from tests.test_sim_crew_placement import (
    CENTER,
    UPWIND,
    _hash,
    _on_ew_road,
    _plus_graph,
    _replay,
    _RoadsModule,
    _session,
    _tile_dir,
)
from tests.test_simulator_page_rendered import PAGE, _browser, _Dom, _Stub
from wgresponse import geo
from wgresponse.contracts import CrewStatus, LatLon
from wgresponse.simui import SimSession


def _fire_at(s, p) -> float:
    """When the actual fire reaches p — recomputed independently of
    SimSession._rpt_fire_at: each fire's nominal member at p's cell; for a
    cell it never reaches, geo.erode_min of the member at the ember-threat
    radius (the engine's own window filter, whole grid)."""
    r_m = s._cfg.ember_threat_radius_m
    best = math.inf
    for fid in s._fire_order:
        f = s._fires[fid]
        if f.ens is None:
            continue
        nr = f.ens.members[0]
        rc = geo.latlon_to_rc(nr.spec, p)
        if rc is None:
            continue
        i = rc[0] * nr.spec.ncols + rc[1]
        v = nr.data[i]
        if math.isinf(v) and r_m > 0.0:
            v = geo.erode_min(nr, r_m).data[i]
        best = min(best, v)
    return best


class _Visits:
    """Every spot every crew was at, kept tick by tick: the path audit's
    samples of each crew's path_track (each driven cell with the moments it
    entered and left it; each spot it stood at, from the step start)."""

    def __init__(self):
        self.rows = []                    # (crew_id, LatLon, t_in, t_out)

    def take(self, s):
        spec = next(f.ens.members[0].spec for f in s._fires.values()
                    if f.ens is not None)
        for cid in sorted(s._crew_sims):
            cs = s._crew_sims[cid]
            trk = cs.path_track
            if not trk or len(trk) < 2 or cs.status == CrewStatus.INBOUND:
                continue
            t0 = trk[0][0]
            for i in range(1, len(trk)):
                ta, pa = trk[i - 1][0], trk[i - 1][1]
                tb, pb = trk[i][0], trk[i][1]
                if (pa.lat, pa.lon) == (pb.lat, pb.lon):
                    if tb > ta:
                        self.rows.append((cid, pa, max(ta, t0), tb))
                    continue
                for t_in, t_out, q in s._path_cell_samples(pa, pb, ta, tb,
                                                           spec):
                    self.rows.append((cid, q, t_in, t_out))


def _brute(s, visits):
    """(closest ahead, closest behind, caught) over every visit against the
    final actual fire: ahead = (lead s, crew, lat, lon) of the least
    arrival - left; behind = (lag s, crew, lat, lon) of the least
    entered - arrival; caught = visits the fire reached while the crew
    was there (from the very moment it got there: a fire lit under it)."""
    now = s._now_s
    ahead = behind = None
    caught = 0
    for cid, q, t_in, t_out in visits.rows:
        a = _fire_at(s, q)
        if a > now:
            continue
        if a > t_out:
            k = (a - t_out, t_out, cid, round(q.lat, 6), round(q.lon, 6))
            ahead = k if ahead is None or k < ahead else ahead
        elif a >= t_in:
            caught += 1
        else:
            k = (t_in - a, t_in, cid, round(q.lat, 6), round(q.lon, 6))
            behind = k if behind is None or k < behind else behind
    return ahead, behind, caught


def _same_row(row, k):
    if k is None:
        return row is None
    lead_s, _t, cid, lat, lon = k
    return (row is not None and row["crew_id"] == cid
            and (row["lat"], row["lon"]) == (lat, lon)
            and row["minutes"] == round(lead_s / 60.0, 1))


def _relocation_run(tmp_path, ticks=16, seed="report"):
    """The crew-placement safety scenario: sim-g1 placed on the road 5 km
    upwind of the centre, a HIGH fire lit 8 km upwind; the sweep drives the
    crew out ahead of the front, which later burns where it stood."""
    s = _session(tmp_path, seed=seed)
    start = _on_ew_road(5_000.0)
    s.place_crew("sim-g1", start.lat, start.lon)
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    visits = _Visits()
    states = []
    for _ in range(ticks):
        states.append(s.tick())
        visits.take(s)
    return s, visits, states


# ---------------------------------------------------------------------------
# COUNTS
# ---------------------------------------------------------------------------

def test_report_counts_are_the_actual_fire_and_the_gel_ledger(tmp_path):
    s, _visits, states = _relocation_run(tmp_path, ticks=24)
    st = states[-1]
    rep = st["report"]
    now = st["sim_now_s"]
    gel = {c["client_id"]: c["gelled_at_s"] for c in st["clients"]
           if c["gel_applied"]}
    assert rep["homes"] == len(st["clients"])
    assert rep["coated"] == len(gel) > 0
    reached = []
    on_unburnable = 0
    for cl in s._clients:
        a = _fire_at(s, cl.loc)
        if a <= now:
            g = gel.get(cl.client_id)
            reached.append((cl.client_id, a, g is not None and g <= a))
            rc = geo.latlon_to_rc(s._tile.spec, cl.loc)
            on_unburnable += math.isinf(
                s._fires["sim-f1"].ens.members[0].data[
                    rc[0] * s._tile.spec.ncols + rc[1]])
    assert reached, "the scenario must burn at least one home"
    assert on_unburnable >= 1, "a home on ground that cannot burn is read"
    assert rep["reached"] == len(reached)
    assert rep["reached_coated"] == sum(1 for r in reached if r[2])
    assert rep["reached_uncoated"] == len(reached) - rep["reached_coated"]
    rows = rep["homes_reached"]
    assert [r["client_id"] for r in rows] == [
        r[0] for r in sorted(reached, key=lambda r: (r[1], len(r[0]), r[0]))]
    for r in rows:
        assert r["fire_s"] == pytest.approx(_fire_at(
            s, next(c.loc for c in s._clients
                    if c.client_id == r["client_id"])))
        assert r["coated_s"] == gel.get(r["client_id"])
    assert rep["cell_m"] == s._tile.spec.cell_m
    assert rep["ember_radius_m"] == s._cfg.ember_threat_radius_m


def test_no_fire_means_an_empty_report(tmp_path):
    s = _session(tmp_path, seed="quiet")
    st = s.tick()
    rep = st["report"]
    assert (rep["coated"], rep["reached"], rep["reached_uncoated"],
            rep["caught"]) == (0, 0, 0, 0)
    assert rep["nearest"] is None and rep["homes_reached"] == []


# ---------------------------------------------------------------------------
# NEAREST TIME
# ---------------------------------------------------------------------------

def test_nearest_time_is_the_closest_spot_any_crew_was_at(tmp_path):
    s, visits, states = _relocation_run(tmp_path)
    rep = states[-1]["report"]
    ahead, behind, caught = _brute(s, visits)
    assert ahead is not None, "the fire must burn a spot a crew had left"
    assert _same_row(rep["closest_ahead"], ahead)
    assert _same_row(rep["closest_behind"], behind)
    assert rep["caught"] == caught == 0
    n = rep["nearest"]
    best = min(k for k in (ahead, behind) if k is not None)
    assert n["kind"] == ("ahead" if best is ahead else "behind")
    assert n["minutes"] == round(best[0] / 60.0, 1)
    assert n["crew_name"] == s._crew_sims[n["crew_id"]].crew.name
    # the times on the row are the moments themselves
    assert n["fire_s"] == pytest.approx(_fire_at(s, LatLon(n["lat"], n["lon"])))
    if n["kind"] == "ahead":
        assert n["fire_s"] - n["left_s"] == pytest.approx(best[0])
    # the law agrees: a lawful relocation, no crew in the fire
    assert states[-1]["scoreboard"]["crew_overruns_total"] == 0


def test_a_crew_the_fire_reaches_is_caught_never_hidden(tmp_path):
    """The crew-placement worst case — a fire lit ON a placed crew: the
    report's nearest time is 0 ('caught', the crew named), the safety
    ledger reports the same crew, and the crew then resting on old black
    is the closest entry BEHIND the fire — all equal to the brute force."""
    s = _session(tmp_path, seed="worst")
    at_fire = _on_ew_road(8_000.0)
    s.place_crew("sim-g1", at_fire.lat, at_fire.lon)
    s.ignite(at_fire.lat, at_fire.lon, intensity="high")
    visits = _Visits()
    st = s.tick()
    visits.take(s)
    rep = st["report"]
    assert rep["caught"] >= 1
    n = rep["nearest"]
    assert (n["kind"], n["minutes"], n["crew_id"]) == ("caught", 0.0, "sim-g1")
    # lit under the crew: the fire arrived the moment the crew's stay began
    assert n["entered_s"] <= n["fire_s"] <= n["left_s"]
    assert st["scoreboard"]["crew_overruns_total"] >= 1
    assert any(r["crew_id"] == "sim-g1"
               for r in st["scoreboard"]["overrun_ledger"])
    for _ in range(3):
        st = s.tick()
        visits.take(s)
    rep = st["report"]
    ahead, behind, caught = _brute(s, visits)
    assert rep["caught"] == caught
    assert _same_row(rep["closest_ahead"], ahead)
    assert behind is not None and _same_row(rep["closest_behind"], behind)
    assert rep["nearest"]["kind"] == "caught"          # stays: never hidden


def test_settle_classifies_each_spot(tmp_path):
    """_rpt_settle on its own: left before the fire = ahead (the least lead
    kept), there when it arrived = caught (the first kept, all counted), got
    there after = behind (the least lag kept)."""
    s = _session(tmp_path, seed="settle")
    p = CENTER
    s._rpt_settle("sim-g1", "en_route", p, 100.0, 200.0, 1100.0)   # 15 min
    s._rpt_settle("sim-g2", "egressing", p, None, 500.0, 1100.0)   # 10 min
    s._rpt_settle("sim-g1", "deploying", p, 1000.0, 1300.0, 1200.0)
    s._rpt_settle("sim-g2", "available", p, 1400.0, 1500.0, 1200.0)  # 3.3
    s._rpt_settle("sim-g1", "available", p, 1500.0, 1600.0, 1200.0)  # 5
    s._rpt_settle("sim-g2", "deploying", p, 900.0, 1250.0, 1200.0)
    assert s._rpt_ahead[1]["crew_id"] == "sim-g2"
    assert s._rpt_ahead[1]["minutes"] == 10.0
    assert s._rpt_ahead[1]["left_s"] == 500.0
    assert s._rpt_behind[1]["crew_id"] == "sim-g2"
    assert s._rpt_behind[1]["minutes"] == round(200.0 / 60.0, 1)
    assert s._rpt_n_caught == 2
    assert s._rpt_caught["crew_id"] == "sim-g1"            # the first


# ---------------------------------------------------------------------------
# DISPLAY ONLY
# ---------------------------------------------------------------------------

def test_the_report_changes_no_decision(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _s, _v, normal = _relocation_run(tmp_path / "a", ticks=12)
    monkeypatch.setattr(SimSession, "_rpt_step", lambda self, t: None)
    monkeypatch.setattr(SimSession, "_report_state", lambda self: {})
    _s2, _v2, bare = _relocation_run(tmp_path / "b", ticks=12)
    assert len(normal) == len(bare) == 12
    for k, (x, y) in enumerate(zip(normal, bare)):
        x = {kk: v for kk, v in x.items() if kk != "report"}
        y = {kk: v for kk, v in y.items() if kk != "report"}
        assert _hash(x) == _hash(y), f"the report changed tick {k + 1}"


def test_the_report_replays_byte_identically(tmp_path):
    s, _v, live = _relocation_run(tmp_path, ticks=10)
    _s2, replayed = _replay(s.state()["cmd_log"], tile_dir=_tile_dir(tmp_path),
                            roads_module=_RoadsModule(_plus_graph()))
    assert len(replayed) == len(live)
    for k, (x, y) in enumerate(zip(live, replayed)):
        assert _hash(x["report"]) == _hash(y["report"]), k + 1
    assert live[-1]["report"]["nearest"] is not None


# ---------------------------------------------------------------------------
# PAGE (rendered)
# ---------------------------------------------------------------------------

#: opens the report once the page has rendered a state (or clicks Pause,
#: for a running state), then marks the DOM for the dump
REPORT_PROBE = r"""<script>
(function () {
  function wait() {
    if (typeof lastState !== "undefined" && lastState && lastState.report) {
      if (window.__viaPause) document.getElementById("pause").click();
      else openReport();
      setTimeout(function () {
        const pre = document.createElement("pre");
        pre.id = "__probe";
        pre.textContent = "ok";
        document.body.appendChild(pre);
      }, 100);
    } else setTimeout(wait, 50);
  }
  setTimeout(wait, 50);
})();
</script>"""


def _js_minutes(m):
    """The page's rptMin: whole minutes rounded half up (Math.round),
    hours and minutes from 120 min."""
    r = int(math.floor(m + 0.5))
    return f"{r // 60} h {r % 60:02d} min" if r >= 120 else f"{r} min"


def _render_report(state, prof_dir, via_pause=False):
    browser = _browser()
    if browser is None:
        pytest.skip("LOUD SKIP: no headless Chromium (Chrome / Edge) found — "
                    "set WG_TEST_BROWSER to render the simulator page")
    page = PAGE.read_text(encoding="utf-8")
    anchor = "guard(newSession);\n</script>"
    i = page.index(anchor) + len(anchor)
    flag = "<script>window.__viaPause = %s;</script>" % (
        "true" if via_pause else "false")
    page = page[:i] + flag + REPORT_PROBE + page[i:]
    handler = type("H", (_Stub,), {"page": page.encode("utf-8"),
                                   "state": state})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = (f"http://127.0.0.1:{srv.server_address[1]}"
               f"/simulator.html?nomap=1")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--disable-background-networking", "--disable-sync",
               "--mute-audio", f"--user-data-dir={prof_dir}",
               "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
               "--virtual-time-budget=20000", "--dump-dom", url]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    finally:
        srv.shutdown()
        srv.server_close()
    dom = _Dom(p.stdout)
    assert "__probe" in dom.ids, (p.returncode, p.stderr[-3000:])
    return dom


@pytest.fixture(scope="module")
def report_state(tmp_path_factory):
    _s, _v, states = _relocation_run(tmp_path_factory.mktemp("rpt"), ticks=24)
    st = json.loads(json.dumps(states[-1]))
    assert st["report"]["reached"] >= 1 and st["report"]["nearest"]
    return st


def test_the_report_dialog_renders_the_run(report_state, tmp_path):
    rep = report_state["report"]
    dom = _render_report(report_state, tmp_path / "p")
    assert "open" in dom["reportdlg"].classes
    assert dom["rpt_coated"].text() == str(rep["coated"])
    assert dom["rpt_reached"].text() == str(rep["reached"])
    assert dom["rpt_uncoated"].text() == str(rep["reached_uncoated"])
    n = rep["nearest"]
    assert dom["rpt_nearest"].text() == _js_minutes(n["minutes"])
    detail = dom["rpt_detail"].text()
    assert n["crew_name"] in detail
    assert (f"The fire reached {rep['reached']} of {rep['homes']} homes"
            in detail)
    body = dom["rpt_table"].find(tag="tbody")[0]
    assert len(body.find(tag="tr")) == len(rep["homes_reached"])
    assert f"{round(rep['cell_m'])} m" in dom["rpt_defs"].text()


def test_pause_on_a_running_simulation_opens_the_report(report_state,
                                                        tmp_path):
    st = dict(report_state, running=True)
    dom = _render_report(st, tmp_path / "p", via_pause=True)
    assert "open" in dom["reportdlg"].classes
    assert dom["rpt_coated"].text() == str(st["report"]["coated"])
