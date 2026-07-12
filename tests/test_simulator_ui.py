"""Structural conformance tests for the FOUNDER ROUND 3 simulator UI laws.

These assert on console/simulator.html source only (no server needed) — the
four founder laws the UI must make visible, built against the EXACT state
schema wgresponse.simui emits (contracts.py "FOUNDER ROUND 3"):

  LAW 1  TIME-SCRUB  — a replay timeline scrubs history.first_tick..last_tick,
                       fetching GET /api/sim/<id>/state?tick=k and rendering
                       that recorded snapshot READ-ONLY; a ● LIVE button snaps
                       back to now; scenario commands are blocked while scrubbed.
  LAW 2  ROADS       — crews[i].route_segments drawn per kind (road SOLID,
                       spur SHORT-DASH thin, chord dashed RED-ish), same for
                       egress_segments; falls back to route_polyline.
  LAW 4  TRIAGE      — scoreboard.unserved_by_reason.triaged gets its own
                       distinctly-styled "triaged (abandoned to save more)" row.
  LAW 5  GEL RING    — clients[i].gel_applied gets a hollow BLUE outer ring
                       that persists through later alert-state changes.

Everything must keep working in the ?nomap=1 offline SVG shim, and the page may
reference no external host beyond the console's existing unpkg/OSM pattern.

A sibling owns the backend edits to tests/test_server.py; these UI-structural
checks live here to avoid a collision.
"""
import re
from pathlib import Path

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
SIMULATOR_HTML = CONSOLE_DIR / "simulator.html"


def _html():
    return SIMULATOR_HTML.read_text(encoding="utf-8")


def _shim(html):
    """The offline SVG-shim slice (installed as window.L only when Leaflet's
    CDN is unreachable) — must render every new layer with no external host."""
    return html[html.index("installFallbackLeaflet"):
                html.index("const SIM_LOCATIONS")]


# --- LAW 1 TIME-SCRUB ---------------------------------------------------------

def test_law1_scrub_slider_and_live_button_present():
    """A timeline slider + a prominent ● LIVE snap-back button, both wired."""
    html = _html()
    for marker in ('id="timelineblock"', 'id="timeline"', 'id="live"',
                   'id="tlcur"', 'id="tlmax"', 'id="replaybanner"'):
        assert marker in html, marker
    # the slider is a range input, disabled until history exists
    tag = re.search(r'<input[^>]*id="timeline"[^>]*>', html).group(0)
    assert 'type="range"' in tag
    assert "disabled" in tag
    # the LIVE button starts active (we boot live)
    assert re.search(r'<button[^>]*id="live"[^>]*class="active"', html)
    # wiring: grabbing the slider enters scrub; LIVE exits it
    assert '$("timeline").addEventListener("input"' in html
    assert 'enterScrub(+$("timeline").value)' in html
    assert '$("live").addEventListener("click", exitScrub)' in html


def test_law1_scrub_fetches_recorded_tick_snapshot():
    """Dragging fetches GET /api/sim/<id>/state?tick=k (the replay contract)."""
    html = _html()
    assert "state?tick=" in html
    # the coalesced replay reader + its helpers
    for frag in ("async function scrubTo", "function enterScrub",
                 "function exitScrub", "function updateTimeline",
                 "function setReplayBanner"):
        assert frag in html, frag
    # reads the history contract block {first_tick, last_tick, current_tick}
    assert "updateTimeline(st.history)" in html
    for frag in ("first_tick", "last_tick", "current_tick", "headTick"):
        assert frag in html, frag


def test_law1_replay_is_read_only_with_live_return():
    """Scrubbed view is READ-ONLY: a loud REPLAY banner, polling paused, and
    every scenario-mutating command rejected with a 'return to LIVE' nudge."""
    html = _html()
    # loud read-only banner naming the tick and the head
    assert "REPLAY tick" in html
    assert "viewing history" in html
    # scrubbing gates the live poll so it can't fight the drag / clobber replay
    assert re.search(r"if \(!sid \|\| busy \|\| scrubbing", html)
    assert "if (scrubbing) return;" in html
    # mutations are no-ops while scrubbing — the exact founder-facing nudge
    assert "function mutationBlocked" in html
    assert "return to LIVE to change the scenario" in html
    # the nudge guards ctl / config / ignite / new-session (never mutate the past)
    assert html.count("mutationBlocked()") >= 4
    # LIVE re-enables the controls (disabled toggled off) + resumes polling
    assert "function applyScrubUI" in html
    assert "scrubbing" in html and "el.disabled = scrubbing" in html


def test_law1_never_sends_goto_or_mutation_while_scrubbing():
    """The scrub read path is a GET; no scenario command is issued mid-scrub."""
    html = _html()
    # scrubTo only GETs the snapshot — it must not POST a ctl/ignite/config
    start = html.index("async function scrubTo")
    end = html.index("function applyScrubUI")
    body = html[start:end]
    assert 'api("GET"' in body
    assert 'api("POST"' not in body


# --- LAW 5 GEL RING -----------------------------------------------------------

def test_law5_blue_gel_ring_keyed_on_gel_applied():
    """Every gel_applied home gets a hollow BLUE outer ring, drawn under the
    alert-state dot so it persists as the state fill later changes color."""
    html = _html()
    # keyed off the persistent gel_applied flag (not the transient alert state)
    assert "if (c.gel_applied)" in html
    assert "gelled_at_s" in html
    # a blue, HOLLOW (fill:false) ring wider than the 7.5px state dot
    assert "#2196f3" in html
    assert "fill: false" in html
    assert re.search(r"radius:\s*12", html)   # outer halo > the 7.5 state dot
    # legend calls it out for the founder
    assert "gel applied (protected)" in html


def test_law5_gel_ring_renders_in_offline_shim():
    """The hollow ring must read as a ring in the air-gapped SVG shim too —
    the circle branch honors fill:false instead of a solid default-red disc."""
    shim = _shim(_html())
    assert "o.fill === false" in shim
    assert 'setAttribute("fill", o.fill === false ? "none"' in shim


# --- LAW 2 ROADS RENDER -------------------------------------------------------

def test_law2_route_segments_consumed_per_kind():
    """route_segments drawn per kind: road SOLID crew-color, spur SHORT-DASH
    thin, chord dashed RED-ish; a defensive decode + a fallback to
    route_polyline when segments are absent."""
    html = _html()
    for frag in ("route_segments", "egress_segments", "function decodeSegments",
                 "function segStyle", "function egressSegStyle",
                 "function syncCrewSegs"):
        assert frag in html, frag
    # the three kinds are all handled
    for kind in ('"road"', '"spur"', '"chord"'):
        assert kind in html, kind
    # road = SOLID (dashArray null), spur = SHORT dash + thin, chord = red warn
    assert "dashArray: null" in html                       # road solid
    assert 'dashArray: "3 4"' in html                      # spur short-dash
    assert "#ff5252" in html and 'dashArray: "6 5"' in html   # chord red dashed
    # spur is thinner than the road weight
    assert re.search(r"weight:\s*3\.5", html)              # road
    assert re.search(r"weight:\s*2\.0,\s*opacity:\s*0\.9,\s*dashArray:\s*\"3 4\"",
                     html)                                  # spur thin
    # falls back to route_polyline (older backend) then a straight chord
    assert "ptList(c.route_polyline)" in html
    assert 'kind: "chord"' in html


def test_law2_egress_keeps_amber_dash_with_spur_split():
    """Egress keeps its amber long-dash identity for the road portion but
    honors the spur/chord split (spur tightens, chord goes red)."""
    html = _html()
    # amber road-egress dash preserved (the founder already reads 8 5 as egress)
    assert "#ffb300" in html
    assert 'dashArray: "8 5"' in html
    # egress consumed through the same per-segment renderer
    assert "R.egressSegs" in html
    assert "decodeSegments(c.egress_segments)" in html


def test_law2_segments_render_in_offline_shim():
    """Per-segment polylines (road/spur/chord) render in the SVG shim: it
    draws polylines with stroke-dasharray and reuses layers via setLatLngs."""
    html = _html()
    shim = _shim(html)
    # syncCrewSegs builds L.polyline segments; the shim supports polyline + dash
    assert "polyline:" in shim
    assert "stroke-dasharray" in shim
    # emptied surplus segment slots (setLatLngs([])) draw nothing, never error
    assert "!sp.latlngs || !sp.latlngs.length" in shim


# --- LAW 4 TRIAGE -------------------------------------------------------------

def test_law4_triaged_scoreboard_line():
    """unserved_by_reason.triaged gets its own labeled, distinctly-styled row —
    split out of the generic capacity/infeasible line and from withdrawn."""
    html = _html()
    assert 'id="sb_triaged_row"' in html
    assert 'id="sb_triaged"' in html
    assert "triaged (abandoned to save more)" in html
    # sourced from the scoreboard reason dict + split out of the generic line
    assert "reasons.triaged" in html
    assert 'filter(k => k !== "triaged")' in html
    # distinct styling (violet), separate from withdrawn amber / violations red
    assert "#sb_triaged_row.hot" in html
    assert "#c792ea" in html


# --- offline shim + external-host discipline (all four laws) -------------------

def test_ui_no_external_hosts_beyond_console():
    """The simulator may reference exactly the console's external hosts and
    nothing else — the four new laws add no new host."""
    sim_html = _html()
    con_html = (CONSOLE_DIR / "index.html").read_text(encoding="utf-8")
    hosts = lambda h: set(re.findall(r"https?://[A-Za-z0-9.-]+", h))
    assert hosts(sim_html) == hosts(con_html)
    assert hosts(sim_html) == {"https://unpkg.com",
                               "https://tile.openstreetmap.org",
                               "http://www.w3.org"}
    # the shim itself stays self-contained (only the inert SVG xmlns)
    assert re.findall(r"https?://[A-Za-z0-9.-]+", _shim(sim_html)) == \
        ["http://www.w3.org"]


def test_ui_offline_shim_still_matches_console_pattern():
    """?nomap=1 rehearses the air-gapped shim; the new layers don't disturb the
    console's install-window-L-only discipline."""
    html = _html()
    assert 'get("nomap")' in html
    assert 'typeof window.L === "undefined"' in html
    assert "_fallback: true" in html
    # the new gel ring + route segments reuse existing shim primitives
    for api in ("circleMarker:", "polyline:", "marker:"):
        assert api in html, api


# --- LAW 8 OUTCOME TAG (FOUNDER ROUND 4) ---------------------------------------
# state.clients[i].outcome + scoreboard.outcomes explain WHY each home is/isn't
# protected: the map draws a distinct affordance ring for abandoned_unsavable
# and triaged (clearly different from a plain pending home), and a scoreboard
# "Outcomes" block lists the counts with a one-line legend.

def test_law8_outcomes_scoreboard_block_present_and_sourced():
    """A dedicated 'Outcomes' block lists the per-home outcome counts, each
    sourced from scoreboard.outcomes {tag: count} (saved / protected-then-burned
    / abandoned-unsavable / triaged / not-threatened + in-progress/pending)."""
    html = _html()
    # the block + its h2 title
    assert 'id="outcomes"' in html
    assert re.search(r'<h2>\s*Outcomes', html)
    # a row per outcome the founder must see
    for marker in ('id="oc_saved"', 'id="oc_protected"', 'id="oc_abandoned"',
                   'id="oc_triaged"', 'id="oc_inflight"', 'id="oc_notthreat"',
                   'id="oc_abandoned_row"'):
        assert marker in html, marker
    # each count is read from the scoreboard.outcomes breakdown by its tag
    assert "sb.outcomes" in html
    for frag in ("oc.saved", "oc.protected_then_impacted", "oc.abandoned_unsavable",
                 "oc.triaged", "oc.in_progress", "oc.pending", "oc.not_threatened"):
        assert frag in html, frag
    # abandoned losses are hot-highlighted so they read as a deliberate call
    assert '$("oc_abandoned_row").classList.toggle("hot"' in html
    assert "#outcomes #oc_abandoned_row.hot" in html


def test_law8_outcomes_one_line_legend_explains_why():
    """A one-line legend under the Outcomes block explains WHY homes went
    unprotected (abandoned = fire beat every feasible crew; triaged =
    deprioritized to save more homes)."""
    html = _html()
    assert 'id="oc_legend"' in html
    leg = html[html.index('id="oc_legend"'):]
    leg = leg[:leg.index("</div>")]
    assert "gel applied" in leg
    assert "fire beat every feasible crew" in leg
    assert "save more homes" in leg


def test_law8_abandoned_and_triaged_get_distinct_map_markers():
    """Client dots keep their alert-state fill + blue gel ring; ADD an outcome
    affordance: abandoned_unsavable a grey DASHED hollow ring (clearly distinct
    from a plain pending dot), triaged a muted VIOLET dashed hollow ring. Both
    reuse circleMarker (a shim primitive) and are hollow (fill:false) so they
    read as rings in the offline shim too."""
    html = _html()
    # keyed off the per-client outcome tag
    assert 'c.outcome === "abandoned_unsavable"' in html
    assert 'c.outcome === "triaged"' in html
    # abandoned = grey dashed hollow ring; triaged = violet dashed hollow ring
    assert "#9aa4b2" in html                       # abandoned grey
    assert "#c792ea" in html                       # triaged violet (shared w/ scoreboard)
    assert 'dashArray: "2 3"' in html              # abandoned dash
    assert 'dashArray: "3 4"' in html              # triaged dash
    # hollow rings (fill:false) -> render as rings, not discs, incl. the shim
    assert html.count("fill: false") >= 2          # gel ring + outcome ring(s)
    # each outcome ring is its OWN hollow circleMarker keyed to its tag/color,
    # drawn before the 7.5px state dot (which sits on top, unfilled ring under)
    ab = html[html.index('c.outcome === "abandoned_unsavable"'):
              html.index('c.outcome === "triaged"')]
    assert "L.circleMarker(" in ab and "fill: false" in ab and "#9aa4b2" in ab
    tr = html[html.index('c.outcome === "triaged"'):]
    tr = tr[:tr.index("radius: 7.5")]              # up to the state-dot draw
    assert "L.circleMarker(" in tr and "fill: false" in tr and "#c792ea" in tr
    # protected_then_impacted needs no new ring: it reuses the gel ring on a
    # burned dot — assert the intent is documented so it isn't "missing"
    assert "protected_then_impacted needs no extra ring" in html


def test_law8_outcome_surfaced_in_client_tooltip():
    """Each client's outcome is spelled out in its tooltip via a readable
    label map, so hovering a home explains its outcome in words."""
    html = _html()
    assert "OUTCOME_LABEL" in html and "outcomeLabel" in html
    assert "outcome: ${outcomeLabel(c.outcome)}" in html
    # the label map covers every tag the backend emits
    for tag in ("saved", "protected_then_impacted", "abandoned_unsavable",
                "triaged", "in_progress", "pending", "not_threatened"):
        assert re.search(rf"\b{tag}:", html), tag


def test_law8_legend_lists_outcome_rings():
    """The map legend calls out the two new outcome affordance rings so the
    founder can decode them (dashed grey = abandoned, dashed violet = triaged)."""
    html = _html()
    leg = html[html.index('id="legend"'):html.index("</div>\n<div id=\"synthbanner\"")] \
        if '</div>\n<div id="synthbanner"' in html else html[html.index('id="legend"'):]
    assert "abandoned — unsavable (fire beat crews)" in html
    assert "triaged — saved more homes instead" in html
    # dashed rings in the legend swatches (border dashed in the outcome colors)
    assert "border:2px dashed #9aa4b2" in html
    assert "border:2px dashed #c792ea" in html


def test_law8_outcome_markers_are_offline_shim_safe():
    """The outcome rings add no new external host and reuse the shim's existing
    circleMarker + fill:false ring support (LAW 5 already pins the shim honors
    fill:false), so they render air-gapped just like the gel ring."""
    html = _html()
    # no new host beyond the console's three (belt-and-suspenders w/ the global
    # host test) — the whole outcome feature is pure inline JS/CSS/markup
    hosts = set(re.findall(r"https?://[A-Za-z0-9.-]+", html))
    assert hosts == {"https://unpkg.com", "https://tile.openstreetmap.org",
                     "http://www.w3.org"}
    # the shim's circle branch honors fill:false (the ring primitive the outcome
    # markers rely on) — the same guarantee LAW 5's gel ring leans on
    shim = _shim(html)
    assert "o.fill === false" in shim


# ---------------------------------------------------------------------------
# Draggable / collapsible legend window (founder UX 2026-07-10): the fixed
# bottom-left legend overlapped the controls, so it became a moveable window.
# ---------------------------------------------------------------------------

def test_legend_is_a_draggable_collapsible_window():
    html = _html()
    # a title-bar drag handle + a collapse button
    assert 'id="lg-head"' in html, "legend needs a drag-handle header (#lg-head)"
    assert 'id="lg-collapse"' in html, "legend needs a collapse button (#lg-collapse)"
    # the header is a move handle and the window is positioned for free placement
    assert re.search(r"#legend\s*\.lg-head\b[^}]*cursor:\s*move", html, re.S), \
        "the legend header must be a move handle (cursor: move)"
    assert re.search(r"#legend\s*\{[^}]*position:\s*fixed", html, re.S), \
        "the legend window must be free-floating (position: fixed) so it can be moved"
    # collapse actually hides the body
    assert re.search(r"#legend\.collapsed\s*\.lg-body\s*\{[^}]*display:\s*none", html, re.S), \
        "collapsing the legend must hide its body"
    # all legend rows survive, now inside the collapsible body
    assert 'class="lg-body"' in html and "gel applied (protected)" in html, \
        "the legend rows must remain, wrapped in the collapsible body"
    # drag wiring + persistence, pure-DOM (no Leaflet dependency, shim-safe)
    assert "pointerdown" in html and "setPointerCapture" in html, \
        "the legend must be draggable via pointer events"
    assert "wg_legend_pos" in html and "wg_legend_collapsed" in html, \
        "legend position + collapsed state must persist in localStorage"
    # dragging/scrolling the legend must not pan the map underneath
    assert "stopPropagation" in html


def test_legend_window_adds_no_external_host():
    # the drag/collapse script is self-contained; no new external references
    html = _html()
    hosts = set(re.findall(r"https?://([a-z0-9.\-]+)", html))
    allowed = {"unpkg.com", "tile.openstreetmap.org", "www.w3.org"}
    assert hosts <= allowed, f"unexpected external host(s): {hosts - allowed}"


# ===========================================================================
# FOUNDER ROUND 5 — regional / mutual-aid crews (SEMANTICS 9), continuous crew
# safety (SEMANTICS 10) and the ember-spotting / grass-realism physics toggles
# (SEMANTICS 11). Built against the EXACT state schema wgresponse.simui emits:
#   crews[i]: region ('in_zone'|'reg_1h'|'reg_3h'), inbound (bool),
#             available_at_s, arrival_eta_s, relocating (bool), relocate_reason
#   scoreboard: relocations, crew_overruns (count), violations (folds overruns)
#   top-level crew_overruns: [{crew_id, kind:'crew_overrun', reason, lat, lon}]
#   outcomes: adds a 'monitoring' tag (beyond 72 h notice horizon but with ETA)
#   /api/sim/new: crews / crews_1h / crews_3h + spotting + grass_realism
# All structural (assert on console/simulator.html source), like the laws above,
# and all offline-shim safe with no new external host.
# ===========================================================================

# --- SEMANTICS 9: three crew tiers -------------------------------------------

def test_sem9_three_labelled_crew_tier_inputs():
    """The single crews control becomes THREE labelled inputs: Crews in-zone
    (1-12), +1h away (0-8), +3h away (0-8)."""
    html = _html()
    for marker in ('id="crews"', 'id="crews_1h"', 'id="crews_3h"'):
        assert marker in html, marker
    # in-zone keeps the 1..12 range (test_server also pins this literal); the
    # two out-of-region reinforcement tiers are 0..8
    assert 'id="crews" min="1" max="12"' in html
    assert 'id="crews_1h" min="0" max="8"' in html
    assert 'id="crews_3h" min="0" max="8"' in html
    # each tier is labelled for the founder
    assert "Crews in-zone" in html
    assert "+1h away" in html and "+3h away" in html


def test_sem9_crew_tiers_wired_to_session_create():
    """crews_1h / crews_3h ride on /api/sim/new and any tier change re-creates
    the session (same UX as a location change), and is read-only while scrubbing."""
    html = _html()
    # the new-session body carries all three tiers
    assert 'crews: +$("crews").value' in html
    assert 'crews_1h: +$("crews_1h").value' in html
    assert 'crews_3h: +$("crews_3h").value' in html
    # every tier re-creates the session on change
    for tier in ("crews", "crews_1h", "crews_3h"):
        assert f'$("{tier}").addEventListener("change", () => guard(newSession))' in html, tier
    # the reinforcement tiers join the scrub-disable set (read-only replay)
    scrub = html[html.index("function applyScrubUI"):html.index("function enterScrub")]
    assert '"crews_1h"' in scrub and '"crews_3h"' in scrub


def test_sem9_inbound_marker_and_arrival_countdown():
    """Inbound reinforcements render at their edge marker as a distinct inbound
    icon (greyed/hollow square + a chevron pointing IN from the region edge) with
    a live 'arrives in Xm' countdown consuming crews[i].arrival_eta_s; region is
    shown in the tooltip, and the itinerary lists inbound crews with their ETA."""
    html = _html()
    # a per-render styler paints the persistent marker element for the crew state
    assert "function styleCrewMarker" in html
    assert 'el.classList.toggle("inbound", !!c.inbound)' in html
    assert ".crew-icon.inbound" in html            # the distinct greyed/hollow icon
    assert 'querySelector(".cinb")' in html         # the countdown chip element
    # the countdown is derived from arrival_eta_s (seconds -> minutes, counts down)
    assert "c.arrival_eta_s" in html
    assert "Math.ceil(c.arrival_eta_s / 60)" in html
    # the chevron points IN from the region edge (reg_1h EAST edge -> left)
    assert 'c.region === "reg_1h" ?' in html
    # region shown in the crew tooltip via a readable label map
    assert "REGION_LABEL" in html
    for reg in ('in_zone: "in-zone"', 'reg_1h: "1h away"', 'reg_3h: "3h away"'):
        assert reg in html, reg
    # the itinerary lists inbound crews with their ETA (not the "idle" fallback)
    assert "not dispatch-eligible until arrival" in html


# --- SEMANTICS 10: continuous crew safety ------------------------------------

def test_sem10_relocating_crew_affordance():
    """relocating=true crews get a distinct pulsing-amber ring + a
    'repositioning — zone threatened' label from relocate_reason."""
    html = _html()
    # a pulsing amber ring toggled by the .relocating class
    assert 'el.classList.toggle("relocating", !!c.relocating)' in html
    assert ".crew-icon.relocating .creloc-ring" in html
    assert "@keyframes crewreloc" in html
    # the label comes from relocate_reason (zone_overrun vs zone_threatened)
    assert ('c.relocate_reason === "zone_overrun" ? "zone overrun" '
            ': "zone threatened"') in html
    assert "repositioning" in html


def test_sem10_crew_overrun_red_alarm_keyed_on_crew_overruns():
    """crew_overruns / scoreboard.crew_overruns > 0 = a CREW CAUGHT IN FIRE:
    a prominent RED alarm banner + flashing red crew markers, keyed on BOTH the
    top-level crew_overruns rows and the scoreboard.crew_overruns count."""
    html = _html()
    # the alarm reads BOTH the rows (for crew_ids) and the scoreboard count
    assert "st.crew_overruns" in html
    assert "sb.crew_overruns" in html
    assert "const nOverruns" in html
    # a dedicated RED banner element + the founder-facing copy
    assert 'id="crewoverrunbanner"' in html
    assert "CREW OVERRUN" in html
    assert 'crew${nOverruns === 1 ? "" : "s"} caught in fire' in html
    # the caught crew markers FLASH red (the .overrun class + burn keyframes)
    assert 'el.classList.toggle("overrun", !!caught)' in html
    assert ".crew-icon.overrun" in html
    assert "@keyframes crewburn" in html
    # overruns pinned to the TOP of the alert feed (above withdrawals), red
    assert 'class="chip caught"' in html
    assert "cos + wds +" in html


def test_sem10_scoreboard_relocations_line_and_violations_red():
    """The scoreboard gains a relocations line + a crews-caught breakout, and
    violations flip RED (reuse the existing viol styling) when positive."""
    html = _html()
    # relocations line, sourced from scoreboard.relocations
    assert 'id="sb_relocations"' in html
    assert "sb.relocations" in html
    assert "crew relocations" in html
    # crews-caught breakout row goes hot (red/blinking) when >0
    assert 'id="sb_overruns_row"' in html
    assert 'id="sb_overruns"' in html
    assert '$("sb_overruns_row").classList.toggle("hot", nOverruns > 0)' in html
    assert "#sb_overruns_row.hot #sb_overruns" in html
    # violations flips red on >0 (existing viol styling reused, folds overruns)
    assert '$("sb_violations").classList.toggle("viol", nv > 0)' in html
    assert "#sb_violations.viol" in html
    # both the banner and the caught scoreboard row reuse the vblink flash
    assert "#crewoverrunbanner" in html and "animation: vblink" in html


# --- SEMANTICS 11: physics toggles -------------------------------------------

def test_sem11_physics_toggles_default_checked_and_recreate_session():
    """Two checkboxes near the intensity control — Ember spotting + Realistic
    grass speed — default CHECKED, and on change RE-CREATE the session passing
    spotting / grass_realism (same UX as the fire-zone / location change)."""
    html = _html()
    # both checkboxes default checked
    assert 'id="spotting" checked' in html
    assert 'id="grass_realism" checked' in html
    assert "Ember spotting" in html and "Realistic grass speed" in html
    # a note that they model real fire behaviour
    assert "model real fire behaviour" in html
    # the new-session body passes both flags
    assert 'spotting: $("spotting").checked' in html
    assert 'grass_realism: $("grass_realism").checked' in html
    # changing either re-creates the session
    assert '$("spotting").addEventListener("change", () => guard(newSession))' in html
    assert ('$("grass_realism").addEventListener("change", () => '
            "guard(newSession))") in html
    # reflected from state defensively when the backend echoes them, and both
    # join the scrub-disable set
    assert "st.spotting" in html and "st.grass_realism" in html
    scrub = html[html.index("function applyScrubUI"):html.index("function enterScrub")]
    assert '"spotting"' in scrub and '"grass_realism"' in scrub


# --- SEMANTICS 9: monitoring outcome + new legend rows -----------------------

def test_monitoring_outcome_row_label_and_marker():
    """The outcomes breakdown gains a 'monitoring' tag (beyond the 72 h notice
    horizon but with a projected ETA — distinct from not_threatened), with its
    own scoreboard row, tooltip label and a distinct hollow slate map ring."""
    html = _html()
    # a dedicated Outcomes scoreboard row, sourced from scoreboard.outcomes.monitoring
    assert 'id="oc_monitoring"' in html
    assert "oc.monitoring" in html
    assert "monitoring (beyond 72 h, ETA projected)" in html
    # the human-readable tooltip label (OUTCOME_LABEL map)
    assert re.search(r"\bmonitoring:", html)
    assert "beyond the 72 h notice horizon" in html
    # a distinct hollow slate ring on the map, keyed on the outcome tag, hollow
    # (fill:false) so it reads as a ring in the offline shim too
    assert 'c.outcome === "monitoring"' in html
    assert "#78909c" in html
    mon = html[html.index('c.outcome === "monitoring"'):]
    mon = mon[:mon.index("addTo(layers.clients)")]
    assert "L.circleMarker(" in mon and "fill: false" in mon and "#78909c" in mon


def test_new_legend_rows_for_crew_states_and_monitoring():
    """The draggable legend gains rows for inbound crew, relocating crew,
    crew-overrun (caught) and the monitoring outcome — inside the legend body."""
    html = _html()
    body = html[html.index('class="lg-body"'):]
    assert "inbound crew (reinforcement en route)" in body
    assert "crew repositioning (zone threatened)" in body
    assert "crew OVERRUN — caught in fire" in body
    assert "monitoring — beyond 72 h, ETA projected" in body


# --- offline-shim + external-host discipline (SEMANTICS 9/10/11) -------------

def test_sem9_10_11_add_no_external_host():
    """None of the crew-tier / crew-safety / physics-toggle additions add an
    external host — the whole feature is inline JS/CSS/markup."""
    html = _html()
    hosts = set(re.findall(r"https?://[A-Za-z0-9.-]+", html))
    assert hosts == {"https://unpkg.com", "https://tile.openstreetmap.org",
                     "http://www.w3.org"}


def test_sem9_10_crew_state_styling_is_shim_safe():
    """The crew-state affordances stay offline-shim safe: styleCrewMarker no-ops
    without a per-marker element (the shim has none), the monitoring ring reuses
    the hollow-ring primitive the shim honors (fill:false), and the shim slice
    itself references no new host."""
    html = _html()
    # styleCrewMarker degrades gracefully when getElement is absent (offline shim)
    assert "R.marker.getElement ? R.marker.getElement() : null" in html
    assert "if (!el) return;" in html
    # the shim slice stays self-contained (only the inert SVG xmlns)
    assert re.findall(r"https?://[A-Za-z0-9.-]+", _shim(html)) == \
        ["http://www.w3.org"]
