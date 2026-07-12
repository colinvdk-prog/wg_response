# 40-Home HOA — Requirements & Field Protocol (GraphGel Response)

2026-07-11. Sizing and protocol grounded in the measured engine (1,149 tests;
60-scenario stress suite; 0 safety violations in every run). Placeholder-data
caveats flagged inline. Companion: `DISPATCH_OPTIMIZATION.md` §3–4.

---

## 1. The core design decision

Two regimes, very different math:

- **Reactive** (no-notice ignition nearby, crews race the fire): capped at
  **~42–59%** of threatened homes depending on crew count. This ceiling is
  physics — more crews saturate at ~5 homes/crew.
- **Alerted** (event visible on the 72h/24h ladder): **1–2 crews coat the
  entire community before arrival.** At 15 min/home + short hops, one 3-person
  crew ≈ 10.5 h for all 40 homes; two crews ≈ 5.3 h — comfortably inside an
  EXTREME_24H window, trivially inside WATCH. ENGINE-MEASURED (2026-07-11,
  extended-tile 60 h Camp-Fire-scale runs, 2 crews, `bench_alerted.py`):
  4.4–6.7 h of lead → **80–100% coated** (Boulder 100%, Santa Rosa 93.5%,
  Paradise 84.6%) vs ~42% reactive with the same 2 crews. Lead time is the
  multiplier; a fast-run canyon fire with <1 h lead (Malibu 0.8 h → 41%)
  stays reactive — which is what the evacuate-first protocol is for.

**Therefore: staff for the alerted case (2 crews), protocol for the reactive
case (evacuation-first + hardening), and buy lead time (detection) before
buying a third crew.** The reactive sizing curve (3-person/15-min crews,
measured): 2 crews 41.7% · 4 crews 50.6% · 6 crews 56.3% · 8 crews 58.8%.

## 1b. Interface geometry — why lead time is the product (2026-07-12)

WUI communities usually form a LINE along the wildland edge, and the fire
wall arrives PERPENDICULAR to it — every home's deadline lands in the same
narrow window. Engine benchmarks (Paradise tile, shipped config, 40 homes on
a WUI-snapped interface line):

| Scenario | 2 crews | 4 crews |
|---|---|---|
| Head-on interface, reactive (fire 6 km, high) | **5%** | 22% |
| Head-on interface, reactive, extreme | 5% | 8% |
| Flank/parallel line (staggered deadlines), reactive | 40% | 50% |
| Scattered WUI (reference), reactive | 46% | 50% |
| Second ignition at T+2h, reactive | 37% | 53% |
| **Head-on interface, ALERTED (detected ~20 km out, hold+preposition)** | **100%** | **100%** |

Capacity law for the head-on reactive case: served saturates at ~1-2 homes
per crew NO MATTER how many homes enroll (deadline-limited: unserved reasons
are `infeasible_deadline`, not access) — adding crews barely moves it.
**The same line is fully coverable with 2 crews when the fire is detected a
day out.** Operationally: for interface communities, detection + the alert
ladder + the 24h hold/preposition regime IS the product; reactive crews are
the backstop, not the plan. This is the quantitative case for the marketing
strategy below.

## 1c. Onboarding & marketing tool (`tools/onboard.py`)

Grades any candidate home (real coordinates) with the real engine:
`reactive%` across a compass ensemble of nearby ignitions, `alerted`
coverability (far detection, hold regime), grade A-D, and **fleet impact**
(home-scenarios the existing portfolio loses if the candidates join —
nonzero means add a crew or cap enrollment).

    py -3 tools/onboard.py --location paradise_ca --crews 2 \
        --portfolio "lat,lon;..." --candidates "lat,lon;..."

Marketing interpretation: grade C (alerted-only) is the NORMAL state of
interface-edge homes — do not decline them; sell the detection + alert-
ladder package that covers them 100%. Grade D = decline, require hardening/
access work, or price a dedicated crew. Use the tool neighborhood-by-
neighborhood to find where enrollment keeps response high (A/B clusters,
capacity-safe additions) — that is the target map for sales.

## 2. HOA requirements

### Staffing & equipment
- **2 × 3-person response crews** (6 trained responders; HOA residents or
  contracted), on-call rotation during fire season; target mobilize ≤30 min.
- Crew kit per team: application rig sized for **15 min/home** (two
  applicators + one prep-runner who stages the next property — this is what
  the 15-min figure assumes), PPE, radio + phone (GPS beacon app), printed
  map with safe zones.
- Deploy-speed program: pre-rigged manifolds/quick-connects targeting
  **10 min/home** (worth +4pp reactive; equals adding ~2 crews).

### Gel & water logistics
- Inventory: **40 × per-home dose + 25% reserve**, split across two storage
  points (so one blocked road can't strand all gel). GraphGel product data
  (2026-07-11): **Class A protection at 1/8" DRY film — the dried coating is
  the protection, so effectiveness is FLAT once applied** (no wet-gel decay).
  Still needed from the supplier: coverage rate (volume per home at 1/8" dry —
  gates the 15 min/home assumption and inventory math), drying time to rated
  film, and the outer weathering/rain wash-off horizon.
- Re-application rule: re-coat only if (a) rain has hit the coating since
  application (wash-off behavior unverified) or (b) application-to-projected-
  arrival would exceed the 72 h operational window. Age alone within 72 h is
  NOT a re-coat trigger — early application provides good protection.

### Site & infrastructure (per the defensibility analysis)
- **Density law: ≤5 homes per committed crew** for reactive odds ≥57%; at 40
  homes / 2 crews (20/crew), the HOA is explicitly in the
  "alerted-regime-or-evacuate" posture — say so in the governing documents.
- **Defensible space mandated per parcel** (the model treats a cleared parcel
  as un-burnable by surface fire — ember-only exposure; it is the single
  per-home lever). Annual inspection before fire season.
- **2+ verified safe zones** (staging/egress anchors) on opposite sides of the
  community, ground-verified, in the engine's catalog. No zones = the engine
  refuses to schedule (fail-closed) — this is a hard onboarding requirement.
- Two independent egress roads; if only one exists, the buffer rule below
  tightens (engine: raise egress_buffer_min).
- Detection: camera coverage (ALERTWildfire or equivalent) + feed monitoring;
  every minute of detection latency is a minute off every deadline.

## 3. Alert-ladder protocol (what each party does at each rung)

| Ladder state | Trigger (engine) | Homeowners | Responders |
|---|---|---|---|
| **NOTICE_72H** | P50 ≤ 72h, one-time | Confirm access (gates, combos), clear driveway, verify defensible space, charge phones, review evacuation plan | Verify gel inventory + rigs, confirm rotation, test beacons |
| **WATCH** | ongoing threat | Move vehicles out or nose-out, open gates, stage water, prep to leave | **COATING HOLD (FOUNDER ROUND 5, engine-enforced): while the fire is >24h out, do NOT coat — stand on alert, PREPOSITION at the safe zone nearest your assigned first stop.** The engine re-verifies every cycle that deferral still covers every home (with a 6h weather buffer); if that gate ever fails, dispatch releases the hold and coating begins immediately — follow it |
| **EXTREME_24H** | P50 ≤ 24h or P10 ≤ 12h | **Evacuate when told — do not wait to watch the coating.** Leave gates open. | **Hold releases automatically at the 24h line — begin full-community coat in dispatch-plan order** (~5.3h with 2 crews from prepositioned staging), ACK every stop. Engine-measured: the hold policy raised alerted-regime coverage to 79.5% and reactive throughput ~+5% (prepositioned crews strike closer) |
| **IMPACTED / no-notice ignition** | fire arrives / ignites nearby | **Evacuate immediately. Never wait for a crew.** | Engine goes reactive TOPTW: follow the itinerary EXACTLY — order, latest-depart times, egress zone. Expect triage: some homes are deliberately skipped to save more. |
| **CLEARED** | downgrade w/ hysteresis | Return only on official all-clear | Post-event report: what was coated when (feeds the freshness model), gel used, road blockages observed |

## 4. Responder safety rules (the engine's laws, in field terms)

These are the coded laws (SEMANTICS 2/3/7/10). They are why every simulated
run has 0 violations. They are not operator-overridable downward.

1. **The 30-minute rule**: you are off the property ≥30 min before the
   earliest credible fire arrival (P10, not the median). The dispatch message
   carries a **latest-depart time per stop — leave by it, finished or not.**
2. **15-minute slack floor**: if a stop's plan carries <15 min of slack it was
   never dispatched to you. If reality eats your slack (blocked road, rig
   trouble), call it in; the next tick's replan handles it — don't improvise.
3. **Corridors both ways**: your route in AND your route to the egress zone
   are checked against the eroded pessimistic fire surface. If dispatch says
   withdraw mid-coat, **withdraw mid-coat** (the home is recorded `withdrawn`,
   the gel unfinished — the engine already decided the alternative is worse).
4. **Relocation orders (SEM-10)**: if your idle/staging position becomes
   unsafe, you get a relocate order — comply immediately; acknowledge.
5. **ACK discipline**: un-ACKed dispatches escalate after 10 min and your crew
   is dropped from the plan (treated as unavailable). ACK everything, always.
6. **Beacon on** at shift start. A stale beacon bars you from dispatch
   (deliberately — the engine won't send a crew it can't see).

## 5. Homeowner enrollment packet (data the engine needs per home)

- Parcel geometry + structure location (lat/lon verified on imagery).
- Access: gate codes/keys arrangement, driveway clearance for the rig,
  turn-around, water spigot locations.
- Hazards: propane/LPG tanks, solar/battery cutoffs, animals, locked areas.
- Coating consent + surface inventory (what gets coated, what's excluded) —
  consent language must state the coating is OPAQUE and applied only under
  live threat, and describe the post-event wash-down/removal service.
- Contact ladder (SMS/email, secondary contact, evacuation-needs flag).
- Defensible-space inspection status (pass required for "protected" tier).

## 6. Drill & acceptance (before fire season)

1. **Tabletop**: run the simulator on the HOA's own tile (LFPS fetch), full
   board walk-through of one high + one extreme scenario.
2. **Field drill**: EXTREME_24H exercise — 2 crews, water-only application,
   measure actual min/home and mobilize time; feed both back into the engine
   config (if you measure 18 min/home, the plan uses 18, not 15).
3. **Acceptance numbers** (engine, HOA's own tile): reactive expected-save %
   at 2 crews; pre-coat completion hours at 1 and 2 crews; both on the
   community's real road graph. These become the HOA's published SLOs.

## 7. Open items blocking "best possible"

1. **Remaining gel supplier data** — decay question ANSWERED (Class A at 1/8"
   dry, flat effectiveness). Coverage is quoted WET — the dry film is much
   thinner — so inventory math uses WET volume per home (number still needed:
   wet gal/home; it also gates the 15 min/home deploy figure). Also still
   needed: drying time to rated film, rain wash-off behavior. NOTE (founder
   2026-07-11): the coating is OPAQUE — owners won't accept preventive
   application, so the WATCH trigger (threat-driven) is the EARLIEST viable
   coat and seasonal pre-coating is NOT offered. Budget post-event wash-down/
   removal into the service model (saved homes come back opaque).
2. Engine `precoat` planning mode (coverage scheduling vs reactive TOPTW) —
   ranked #1 in DISPATCH_OPTIMIZATION.
3. Detection subscription decision (cameras + Watch Duty enterprise).
4. MOU with local fire authority (crews operating during evacuations need
   recognized status; also unlocks better safe-zone options).
5. Insurance interplay (hardening + protocol → premium credits; estate
   strategy doc exists).
