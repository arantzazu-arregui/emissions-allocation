"""Generate notebooks/01_methodology_walkthrough.ipynb.

The notebook is generated rather than hand-written so it stays in step with the
modules: every code cell calls into ``src/emissions_allocation``, and none of the
model logic is reimplemented here.

Cells read the cached tables in ``data/interim/``, so the notebook executes in
seconds. Regenerate them with ``python scripts/run_pipeline.py --all``.

    python notebooks/build_notebook.py
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "notebooks" / "01_methodology_walkthrough.ipynb"

cells: list = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip()))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip()))


# ===========================================================================
md(r"""
# National allocation of international shipping CO₂

A two-vessel replication of **Selin et al. (2021)**, *Mitigation of CO₂ emissions from
international shipping through national allocation* (Environ. Res. Lett. **16**, 045009),
built entirely from public data.

This notebook walks through `docs/METHODOLOGY.md` section by section, §0 to §8. It
**demonstrates** the pipeline; it does not reimplement it. Every computation calls into
`src/emissions_allocation/`, and every physical formula is cited to its source inline.

## How to read the numbers

Two of the parameters this model needs — **installed power** and **design speed** — cannot
be observed from any free source. Selin et al. used IHS World Register of Shipping, a paid
commercial register. The IMO's own fallback regressions are mutually circular. So three
estimates are carried **in parallel with no primary**, and the spread between them is a
reported result rather than an error to be resolved.

Anything derived rather than observed is marked **[estimated]**. A reader should never be
unsure which is which.

## Scope, honestly

Two vessels demonstrate the *machinery* of national allocation. They cannot reproduce the
paper's *findings*, which are distributional. Vessel B has not yet been selected, so what
follows is n = 1 — and vessel A's allocation is **degenerate**: all four options resolve to
one national budget. That is not a poorly chosen ship. It is a *typical* one, and it is the
point Selin et al. make: allocation choice is immaterial for the co-located majority and
decisive for open-registry ships.
""")

code("""
import sys
from pathlib import Path

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from emissions_allocation import Database, load_config
from emissions_allocation import activity, allocation, baselines, impacts, specs, validate

pd.set_option("display.width", 120)
plt.rcParams.update({"figure.figsize": (11, 4.5), "axes.grid": True,
                     "grid.alpha": 0.3, "font.size": 10})

cfg = load_config()
INTERIM = cfg.path("interim")
vessel = next(iter(cfg))

print(f"study period   {cfg.start_date} to {cfg.end_date} ({cfg.elapsed_hours:,} hours)")
print(f"vessels        {[v.imo for v in cfg]}")
print(f"scenarios      {len(cfg.scenarios())}")
""")

# ---------------------------------------------------------------- §0
md(r"""
---
# §0 — Select vessels

**What this step does.** Chooses the study vessels by reproducible criteria rather than by
hand, so a researcher extending this work applies the same filter to any number of ships.

**Inputs** — GFW 4Wings presence at world extent, filtered by flag and vessel type.
**Outputs** — `config/pilot.yaml`, with the criteria values that justified each selection.

Candidate discovery cannot use the ship-name filter, because names are what we are searching
*for*. It uses `flag` and `vessel_type` instead — the other two presence filters that bind.

### Status: vessel A fixed, vessel B not selected

`selection.py` implements §0.2 steps 1–4. **Step 5 cannot be automated**: criterion 7
(registered-owner country ≠ flag country) requires Equasis, which needs a logged-in account
and publishes no API. The stage produces a ranked shortlist for manual completion.

The consequence is visible throughout §5–§7 below and is stated rather than worked around.
""")

code("""
for v in cfg:
    print(f"IMO {v.imo}  ({v.label})  names={list(v.shipnames)}")
    print(f"  former identities outside the study period: "
          f"{list(v.former_shipnames_outside_period) or 'none'}")

print("\\nAllocation keys, both Hong Kong treatments:")
for treatment in cfg.run["hk_treatments"]:
    summary = allocation.summarise_options(cfg, treatment)
    for row in summary.itertuples():
        keys = "  ".join(f"{o}={getattr(row, o)}" for o in allocation.ALLOCATION_OPTIONS)
        verdict = "DEGENERATE" if row.is_degenerate else f"{row.n_distinct_countries} budgets"
        print(f"  {treatment:18s} {keys}   -> {verdict}")
""")

# ---------------------------------------------------------------- §1
md(r"""
---
# §1 — Obtain ship activity data

**What this step does.** Produces a continuous, ordered, hourly position-and-speed series
across the study period, plus the sequence of port calls that defines the vessel's voyages.

**Inputs** — GFW 4Wings presence (`public-global-presence`), GFW Events
(`public-global-port-visits-events`), GFW Vessels identity.
**Outputs** — `vessel_hour`, `port_call`, `voyage_leg`.

### §1.5 Derived speed over ground

Great-circle distance between consecutive hourly positions, over elapsed time:

$$a = \sin^2\!\left(\tfrac{\Delta\varphi}{2}\right) + \cos\varphi_1\cos\varphi_2\,
      \sin^2\!\left(\tfrac{\Delta\lambda}{2}\right)$$
$$d = 2R\arcsin\sqrt{a}, \qquad R = 6371.0088\ \text{km}$$
$$SOG_i = \frac{d}{1.852\,\Delta t}\quad[\text{knots}]$$

*Source: haversine formula; R is the IUGG mean Earth radius. `docs/METHODOLOGY.md` §1.5.*

`SOG` is the IMO's term, but note the provenance differs: in the Fourth GHG Study it is
*transmitted* by the vessel, whereas here it is *derived* from consecutive cell centroids.
§1.6 exists because of that difference.

### §1.6 Smoothing — not cosmetic

GFW credits each vessel-hour to a single 0.01° cell. A ship crossing ~22 cells per hour
lands unpredictably within them, so consecutive-centroid speeds oscillate. Because
propulsion power scales as $v^3$, the error **does not average out**:

$$\overline{SOG}_i = \frac{1}{w}\sum_{k=-(w-1)/2}^{+(w-1)/2} SOG_{i+k}$$

*Source: `docs/METHODOLOGY.md` §1.6. The window $w$ is a sensitivity axis, not a fixed choice.*
""")

code("""
spine = pd.read_parquet(INTERIM / f"vessel_hour_{vessel.imo}.parquet")
port_calls = pd.read_parquet(INTERIM / f"port_call_{vessel.imo}.parquet")
legs = pd.read_parquet(INTERIM / f"voyage_leg_{vessel.imo}.parquet")

print(f"vessel_hour  {len(spine):,} rows")
print(f"port_call    {len(port_calls):,} rows, confidence {sorted(set(port_calls.confidence))}, "
      f"{port_calls.port_iso3.nunique()} countries")
print(f"voyage_leg   {len(legs):,} rows, {int(legs.is_eu_eu.sum())} EU->EU, "
      f"{int(legs.is_international.sum())} international")
port_calls.port_iso3.value_counts().head(8).to_frame("port calls").T
""")

md(r"""
### Chart 1 — the oscillation smoothing corrects

Raw derived speed against the smoothed series, over a week of open-ocean cruising. The raw
trace swings wildly while the vessel is in fact holding a steady speed; that swing is
cell-centroid quantisation, not the ship.
""")

code("""
window_days = 7
at_sea = spine[(~spine.is_inactive) & (spine.sog_raw > 8)]
start = at_sea.ts.iloc[len(at_sea) // 2]
sample = spine[(spine.ts >= start) & (spine.ts < start + pd.Timedelta(days=window_days))]

fig, ax = plt.subplots()
ax.plot(sample.ts, sample.sog_raw, lw=0.8, alpha=0.55, label="raw (w=1)")
for w in [w for w in cfg.run["smoothing_windows"] if w > 1]:
    ax.plot(sample.ts, sample[f"sog_w{w}"], lw=1.6, label=f"smoothed w={w}")
ax.set_ylabel("speed over ground (kn)")
ax.set_title(f"§1.6  derived speed, raw vs smoothed — {start:%Y-%m-%d} + {window_days} d")
ax.legend(ncol=4, fontsize=9)
fig.autofmt_xdate()
plt.show()

active = spine[~spine.is_inactive]
bias = pd.DataFrame({
    "window": cfg.run["smoothing_windows"],
    "mean(v^3)/(mean v)^3": [activity.cubic_bias(active[f"sog_w{w}"])
                             for w in cfg.run["smoothing_windows"]],
})
print("The v^3 bias the smoothing exists to reduce:")
print(bias.to_string(index=False))
""")

md(r"""
### §1.7 Coverage — and a finding the specification did not anticipate

`docs/METHODOLOGY.md` §1.7 and §4.5 treat the coverage correction as negligible, on the
strength of 2024 alone (99.98%). Measured across the full period it is not.

More importantly, the gaps are **two different phenomena** and they need opposite treatment:

* **Scattered short gaps** are thin AIS reception. The vessel was trading and hours were
  missed, so $E/\text{coverage}$ correctly recovers them.
* **Contiguous absences** are the hull out of service. Zero presence hours *and* zero port
  calls, from two independent endpoints. Scaling those up would fabricate voyages.

Applied uniformly, the §4.5 correction would multiply 2019 by 2.77× **for a ship that was
laid up for 282 days**. So `coverage_active` — observed over *in-service* hours — is what
the correction divides by.
""")

code("""
coverage = activity.coverage_by_year(spine)
display(coverage.style.format({
    "coverage_raw": "{:.2%}", "coverage_active": "{:.2%}",
}).set_caption("§1.7 coverage: raw counts a lay-up as missing data; active does not"))

gaps = activity.find_gaps(spine, min_hours=cfg.run["inactivity_gap_days"] * 24)
print("Out-of-service windows (no presence AND no port calls):")
for g in gaps.itertuples():
    print(f"  {g.start_ts:%Y-%m-%d} -> {g.end_ts:%Y-%m-%d}   "
          f"{int(g.hours):,} h ({int(g.hours)//24} d)")
""")

# ---------------------------------------------------------------- §2
md(r"""
---
# §2 — Ship specifications and parameters

**What this step does.** Assembles the technical parameters the emission model needs, and is
explicit that two of them cannot be observed from any free source.

**Inputs** — Equasis, public vessel registers, published regressions.
**Outputs** — `config/vessel_specs.yaml`, every estimated field carrying `value`, `source`
and `method`.

### §2.1 TEU capacity

IMO Table 17 indexes container ships by TEU; Equasis does not carry it. Invert the beam
relation:

$$B = 3.27\,\mathrm{TEU}^{0.29} \quad\Longrightarrow\quad
  \mathrm{TEU} = \left(\frac{B}{3.27}\right)^{1/0.29}$$

*Source: Cepowski & Chorab (2021), Ocean Engineering 238, 109727 — beam relation, fitted on
215 container designs built 2015–2020.*

Beam is the better proxy for container ships because it sets the on-deck row count.

### §2.2 Design speed and installed power — three estimates

**Estimate A — IMO EEXI curve fit**

$$V = A\cdot \mathrm{DWT}^{B}, \qquad P_{ME} = C\cdot \mathrm{DWT}^{D}$$

*Source: IMO Resolution MEPC.333(76) (2021), Table 1. Containership: A=3.240, B=0.183,
C=0.504, D=1.030.* The resolution writes $P_{ME}$ for **installed** power; this project calls
it `MCR` throughout, because the Fourth GHG Study uses that symbol for instantaneous demand.

**Estimate B — Admiralty coefficient, calibrated**

$$Fn = \frac{v}{\sqrt{g\,L_{BP}}} \quad\Longrightarrow\quad
  V = \frac{Fn\sqrt{g\,L_{BP}}}{0.5144}\ [\text{kn}]$$
$$\mathrm{MCR} = \frac{\Delta^{2/3}\,V^{3}}{C_{adm}}$$

*Sources: Froude number, standard naval architecture. $C_{adm}$ calibrated on Charchalis
(2014), Journal of KONES 21(2), Table 1 — 17 container ships with matched speed, power and
displacement: median 482.*

Displacement by two routes, which bracket a real convention difference:

$$\Delta = C_B\,L_{BP}\,B\,T\,\rho \qquad\text{and}\qquad \Delta = \mathrm{DWT}/0.80$$

**Estimate C — sourced specification.** Open item 4. Not found; raises rather than guessing.
""")

code("""
teu = specs.resolve_teu(vessel, cfg)
print(f"TEU inverted from beam {vessel.require_spec('beam_m')} m: {teu:,.0f}  [estimated]")

print("\\nCepowski & Chorab DWT relations against the observed hull (why we trust the inversion):")
val = pd.DataFrame(specs.validate_hull_relations(vessel, cfg.defaults)).T
display(val.style.format("{:.1f}"))

estimates = specs.build_estimates(vessel, cfg)
rows = []
for name, e in estimates.items():
    rows.append({"estimate": e.label, "design speed (kn)": e.design_speed_kn,
                 "MCR (kW)": e.mcr_kw, "within fleet envelope": e.within_fleet_envelope,
                 "estimated": e.estimated})
display(pd.DataFrame(rows).style.format({"design speed (kn)": "{:.2f}", "MCR (kW)": "{:,.0f}"})
        .set_caption("§2.2  three estimates, no primary — the spread IS the result"))

try:
    specs.estimate_c_sourced(vessel, cfg.defaults)
except Exception as exc:
    print(f"\\nEstimate C — OPEN ITEM 4:\\n  {type(exc).__name__}: {str(exc)[:220]}...")
""")

md(r"""
### Chart 2 — estimate A falls outside the modern container fleet

`D = 1.030` makes power nearly *linear* in deadweight, which breaks at the top of the
container range where modern designs are deliberately under-powered for slow steaming.

The predicted 28.92 kn exceeds **24.5 kn, the maximum service speed among 215 distinct
container designs built since 2015** (Cepowski & Chorab, Table 1). This is a reported
validation failure, not something corrected — the estimate is carried through to the CO₂
result so a reader can see what an out-of-envelope power assumption does.
""")

code("""
env = cfg.defaults["container_fleet_speed_envelope"]
fig, ax = plt.subplots(figsize=(11, 4))
ax.axhspan(env["min_kn"], env["max_kn"], color="tab:green", alpha=0.12,
           label=f"observed fleet envelope {env['min_kn']}–{env['max_kn']} kn")
ax.axhline(env["max_kn"], color="tab:green", ls="--", lw=1)

for i, (name, e) in enumerate(estimates.items()):
    ok = e.within_fleet_envelope
    ax.scatter([i], [e.design_speed_kn], s=180, zorder=3,
               color="tab:blue" if ok else "tab:red")
    ax.annotate(f"{e.design_speed_kn:.2f} kn\\n{e.mcr_kw:,.0f} kW",
                (i, e.design_speed_kn), textcoords="offset points", xytext=(14, -6))
    if not ok:
        ax.annotate("OUTSIDE ENVELOPE", (i, e.design_speed_kn),
                    textcoords="offset points", xytext=(14, 20), color="tab:red",
                    fontweight="bold")

if "B" in estimates:
    lo, hi = estimates["B"].variants["speed_kn_range"]
    ax.vlines(list(estimates).index("B"), lo, hi, color="tab:blue", lw=2, alpha=0.5)

ax.set_xticks(range(len(estimates)))
ax.set_xticklabels([estimates[k].label for k in estimates])
ax.set_ylabel("design speed (kn)")
ax.set_ylim(0, 32)
ax.set_title("§2.2 / §8.2  design-speed estimates against the modern container fleet")
ax.legend(loc="lower right", fontsize=9)
plt.show()
""")

# ---------------------------------------------------------------- §3
md(r"""
---
# §3 — Fuel type and emission factors

**What this step does.** Assigns a fuel to every vessel-hour and attaches the corresponding
emission factor and specific fuel consumption.

**Inputs** — IMO Fourth GHG Study Tables 19 and 21; Marine Regions ECA polygons (MARPOL
Annex VI Reg. 14); `voyage_leg` from §1.
**Outputs** — `fuel_assignment` at vessel-hour grain.

### §3.1 Assignment rule

A vessel-hour burns **distillate** (MDO/MGO) when *any* of the following holds, and
**residual** (HFO) otherwise:

1. the main engine is high-speed — not applicable here (SSD);
2. the position falls inside an ECA polygon — point-in-polygon;
3. the hour belongs to a voyage leg between two EU ports.

*Source: Selin et al. (2021), following the IMO Fourth GHG Study.*

Condition 3 is read from an actual port-call sequence, which is a genuine improvement on the
EEZ proxy that gridded-only data would have forced.

### §3.2 The IMO 2020 sulphur cap is immaterial to CO₂

The study period straddles the 0.50% global sulphur cap of 1 January 2020. For CO₂ this
changes nothing: the Fourth GHG Study assigns low-sulphur HFO the **same carbon content and
emission factor** as HFO (Table 21, LSHFO 1.0% → 3.114). Scrubber fitting affects SOx only.
There is deliberately no fuel-switch date branch anywhere in the code.

*Source: IMO Fourth GHG Study 2020, Table 21 (printed p.74).*
""")

code("""
fuel_assign = pd.read_parquet(INTERIM / f"fuel_assignment_{vessel.imo}.parquet")
total = len(fuel_assign)
print(f"{total:,} vessel-hours")
print(f"  inside an ECA:    {int(fuel_assign.in_eca.sum()):,} "
      f"({fuel_assign.in_eca.mean():.1%})")
print(f"  on an EU->EU leg: {int(fuel_assign.is_eu_eu_leg.sum()):,} "
      f"({fuel_assign.is_eu_eu_leg.mean():.1%})")

ef = cfg.factors["emission_factors"]["fuels"]
split = (fuel_assign.fuel_type.value_counts().rename("hours").to_frame()
         .assign(share=lambda d: d.hours / total,
                 ef_f=lambda d: [ef[f]["ef_f"] for f in d.index]))
display(split.style.format({"hours": "{:,}", "share": "{:.1%}"})
        .set_caption("§3.3  fuel split and Table 21 emission factors (g CO2 / g fuel)"))

print("\\nECA hours by area (corroborates the port-call pattern independently):")
print(fuel_assign[fuel_assign.in_eca].eca_area.value_counts().to_string())
""")

# ---------------------------------------------------------------- §4
md(r"""
---
# §4 — Calculate CO₂ emissions

**What this step does.** Converts the hourly activity series into CO₂ mass per hour, summed
to ship-year, for every scenario.

**Inputs** — `vessel_hour`, `vessel_specs`, `fuel_assignment`, IMO Tables 16, 17, 19, 21.
**Outputs** — `emissions_hour`, `emissions_year`, both scenario-keyed.

### §4.2 Main-engine power demand

$$Load_i = \left(\frac{\overline{SOG}_i}{V}\right)^{3}\!\!,\ \text{capped at }1.0
\qquad \dot W_{ME,i} = \mathrm{MCR}\cdot Load_i$$

*Source: `docs/METHODOLOGY.md` §4.2; the cubic law is standard propulsion theory.*

with $\dot W_{ME,i}=0$ below 7% MCR and in the At berth / Anchored modes.
*Source: IMO Fourth GHG Study 2020, printed p.70 — "At engine loads below 7%, fuel
consumption and all the emissions derived from the main engine are assumed to be zero."*

### §4.4 Load-corrected specific fuel consumption — IMO equation (10), verbatim

$$SFC_{ME,i} = SFC_{base}\cdot\underbrace{\left(0.455\,Load_i^{2} - 0.710\,Load_i + 1.280\right)}_{CF_L}$$

*Source: IMO Fourth GHG Study 2020, equation (10), printed p.71.*

The quadratic minimises at $Load = 0.710/(2\times0.455) = 0.78$, matching the study's stated
~80% MCR optimum — an internal check that the coefficients are transcribed correctly, and one
the test suite asserts.

Auxiliary engines and boilers are **not** corrected — IMO equation (11):

$$FC_{AE|BO,i} = SFC_{base}\cdot \dot W_{AE|BO,i}$$

### §4.5 Hourly and annual CO₂

$$FC_i = \left[\dot W_{ME,i}SFC_{ME,i} + \dot W_{AE,i}SFC_{base,AE}
        + \dot W_{BO,i}SFC_{base,BO}\right]\Delta t \quad[\text{g fuel}]$$
$$E_{CO_2,i} = FC_i\cdot EF_f / 10^{6}\quad[\text{t}], \qquad
  E_{ship,y} = \sum_i E_{CO_2,i}$$

*Source: IMO Fourth GHG Study 2020 §2.2; emission factors from Table 21.*

$LLF$ does not appear: CO₂'s low-load factor is 1.00 at every load (Table 20), because CO₂
varies directly with fuel consumption, which is already load-dependent.
""")

code("""
c = cfg.factors["load_correction"]
loads = np.linspace(0.01, 1.0, 400)
cf_l = c["a"] * loads**2 + c["b"] * loads + c["c"]

fig, ax = plt.subplots(figsize=(7, 3.6))
ax.plot(loads, cf_l, lw=2)
ax.axvline(c["minimises_at"], color="tab:red", ls="--",
           label=f"minimum at Load = {c['minimises_at']:.3f}")
ax.axvline(c["main_engine_cutoff_load"], color="tab:grey", ls=":",
           label=f"main engine off below {c['main_engine_cutoff_load']:.0%} MCR")
ax.set_xlabel("main engine load"); ax.set_ylabel("$CF_L$")
ax.set_title("IMO equation (10) — load correction factor")
ax.legend(fontsize=9)
plt.show()

print(f"analytic minimum -b/2a = {-c['b'] / (2 * c['a']):.4f}"
      f"   (the study states ~80% MCR)")
""")

md(r"""
### §4.1 Operating mode — IMO Table 16, and one documented departure

Table 16 assigns one of five phases from speed, main-engine load, distance to port and
distance to coast. The five columns are an **ordered ladder**; the first applicable wins.

*Source: IMO Fourth GHG Study 2020, Table 16, printed p.66.*

Two things about this table are worth stating, because both differ from
`docs/METHODOLOGY.md` §4.1:

**The 'Port 1–5 nm' column is tanker-only.** The source footnotes it: *"Applicable to
chemical tankers, liquified gas tankers, oil tankers and other liquids tankers only"* —
liquid tankers are lightered offshore and so can berth within 5 nm of port. The methodology
document reproduces the column without that restriction, which would let a container ship
count as At berth up to 5 nm out. **The source is followed here.**

**At berth is determined from port-visit intervals, not distance.** The source separates
berth from anchor by distance because it had no better signal, and §4.1 flags the exposure.
We have a better one: a GFW port-visit event asserts, from a different endpoint and with a
confidence score, that the vessel was in port between two timestamps. Using distance to
anchorage coordinates instead put only 1,143 h at berth against 17,427 h actually in port.
`run.use_port_visit_intervals: false` restores strict Table 16 for comparison.
""")

code("""
hourly = pd.read_parquet(INTERIM / f"emissions_hour_{vessel.imo}.parquet")
yearly = pd.read_parquet(INTERIM / f"emissions_year_{vessel.imo}.parquet")

ref = hourly[hourly.scenario_id == f"{cfg.run['power_estimates'][0]}_w3"]
mode_split = (ref.operating_mode.value_counts().rename("hours").to_frame()
              .assign(share=lambda d: d.hours / len(ref)))
display(mode_split.style.format({"hours": "{:,}", "share": "{:.1%}"})
        .set_caption("§4.1  operating-mode split (estimate A, w=3)"))

t17 = cfg.factors["auxiliary_boiler_power"]
band = next(b for b in t17["ship_types"]["container"]["bands"] if b["min"] == 12000)
display(pd.DataFrame({"mode": t17["modes"], "boiler kW": band["boiler"],
                      "auxiliary kW": band["auxiliary"]})
        .set_index("mode").T.style.set_caption(
            "§4.3  IMO Table 17 — container 12,000–14,499 TEU"))
""")

md(r"""
### Chart 3 — the track, coloured by operating mode

Each point is one vessel-hour at its 0.01° cell centroid. Berth and anchorage hours cluster
tightly at ports; transit hours trace the trade lanes.
""")

code("""
track = ref.merge(spine[["imo", "ts", "lat", "lon"]], on=["imo", "ts"], how="left").dropna(
    subset=["lat", "lon"])
colours = {"at_berth": "tab:red", "anchored": "tab:orange", "manoeuvring": "tab:purple",
           "slow_transit": "tab:blue", "normal_cruising": "tab:green"}

fig, ax = plt.subplots(figsize=(13, 6.5))
for mode, colour in colours.items():
    sub = track[track.operating_mode == mode]
    if not sub.empty:
        ax.scatter(sub.lon, sub.lat, s=1.4, alpha=0.45, c=colour,
                   label=f"{mode} ({len(sub):,} h)")
ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
ax.set_xlim(-180, 180); ax.set_ylim(-60, 75)
ax.set_title(f"§4.1  IMO {vessel.imo} track by operating mode, "
             f"{cfg.start_date}–{cfg.end_date}")
ax.legend(markerscale=8, fontsize=9, loc="lower left", ncol=2)
plt.show()
""")

md("""
### Chart 4 — annual CO₂ under each power estimate

The gap between the two series is the §2.2 spread propagating into the result. It is an
uncertainty band, not an error: no free source supplies installed power, so there is no basis
for preferring either.

Years flagged low-confidence had thin AIS reception, and 2018 and 2019 also contain the
out-of-service windows found in §1.7.
""")

code("""
w = 3
sub = yearly[yearly.smoothing_window == w]
pivot = sub.pivot_table(index="year", columns="power_estimate", values="co2_tonnes")
low_years = sorted(sub[sub.is_low_confidence].year.unique())

fig, ax = plt.subplots()
pivot.plot(kind="bar", ax=ax, width=0.8)
for i, yr in enumerate(pivot.index):
    if yr in low_years:
        ax.get_xticklabels()[i].set_color("tab:red")
ax.set_ylabel("CO$_2$ (tonnes)")
ax.set_title(f"§4.5  annual CO$_2$, w={w}, coverage-corrected on in-service hours"
             "  (red year = low confidence)")
ax.legend(title="power estimate")
plt.show()

display(pivot.style.format("{:,.0f}").set_caption("annual CO2 (t)"))
totals = sub.groupby("power_estimate").co2_tonnes.sum()
print(f"8-year total, w={w}:")
for k, v in totals.items():
    print(f"  estimate {k}: {v:>12,.0f} t")
print(f"  spread: {totals.max() / totals.min():.2f}x")
""")

# ---------------------------------------------------------------- §5–7
md(r"""
---
# §5 — Allocate to countries

**What this step does.** Attributes annual CO₂ to countries under each allocation rule.

$$E_{c,\text{option}} = \sum_{\text{ships}} E_{\text{ship}}\cdot
  \mathbb{1}\!\left[\text{key}_{\text{option}}(\text{ship}) = c\right]$$

*Source: Selin et al. (2021) §2.*

**Four of the paper's five options are reproduced.** The fifth — bunker fuel — rests on
national marine-bunker *sales* statistics, and allocating one ship's emissions to a bunkering
country would require knowing where it took fuel, which no public dataset records. It is out
of scope **by construction, not by omission**.

Two caveats on the keys, both from §5.2:

* **Operator is a proxy.** Equasis has no operator field; commercial manager stands in. This
  partially collapses the operator and manager options.
* **Equasis gives an address, not a country of incorporation.** GFW's `registryOwners.flag`
  returns HKG for this Shanghai-registered owner — it appears to echo the ship's flag rather
  than owner domicile, and is **not used**.

---
# §6 — Compare with carbon budgets

$$B_c\,[\text{Mt CO}_2] = B_c\,[\text{Mt C}]\times 3.664$$

*Source: Global Carbon Budget 2025, National Fossil Carbon Emissions v2025, sheet
"Territorial Emissions" (header at row index 11). **The GCB reports million tonnes of
carbon, not CO₂.***

National columns already exclude bunker fuels — only the World total includes them — so the
denominator is clean and adding shipping emissions does not double-count.

### §6.4 The Hong Kong question — resolved against the paper itself

Selin et al.'s supplementary Table 1 carries **199 countries and no Hong Kong row**. No
Taiwan, no Macao either: it is aligned strictly to the UNFCCC party list. **The paper folds
Hong Kong into China**, so `folded_into_china` is the replication-faithful treatment.

Both are still computed, because the two baselines differ by a factor of 369 and that gap is
itself the methodological finding.
""")

code("""
base = baselines.build_baselines(cfg)
b2024 = base[base.year == 2024]
rows = []
for t in cfg.run["hk_treatments"]:
    sub = b2024[b2024.hk_treatment == t]
    hk = sub[sub.country == "Hong Kong"].mtco2
    rows.append({"treatment": t,
                 "Hong Kong (Mt CO2)": hk.iloc[0] if len(hk) else np.nan,
                 "China (Mt CO2)": sub[sub.country == "China"].mtco2.iloc[0]})
display(pd.DataFrame(rows).style.format({"Hong Kong (Mt CO2)": "{:,.1f}",
                                         "China (Mt CO2)": "{:,.1f}"}, na_rep="folded in")
        .set_caption("§6.4  2024 national baselines under each treatment"))

check = baselines.shipping_cross_check(cfg, 2024)
print(f"§6.2 cross-check — GCB 'International Shipping' 2024: "
      f"{check['mtc']:.2f} MtC = {check['mtco2']:.0f} Mt CO2")
print("  (an independent global total to sanity-check any fleet-scale result)")
""")

md(r"""
---
# §7 — Compute allocation impacts

$$\Delta E_c = E_c, \qquad
  \Delta E\%_c = 100\cdot\frac{\Delta E_c}{B_c}, \qquad
  \text{rank}_c = \mathrm{RANK}()\ \text{over}\ (\text{option},\text{scenario})$$

*Source: Selin et al. (2021) §3.*

**Ranking and concentration shares are structurally meaningless at n = 1** — with one country
per option the top-20 share is always 1.0. The code path exists and is exercised because the
fleet-scale version needs it, and it is labelled rather than interpreted.

### Chart 5 — the allocation comparison

This is the headline methodological result. **Identical emissions, two territory
conventions**: the same tonnage is a ~370× larger share of Hong Kong's budget than of
China's. Nothing the ship did changes; only the convention does.

With vessel B absent, the four rules cannot diverge — under the paper's own treatment they
all resolve to China. That degeneracy is the finding at n = 1, and it is exactly why
`docs/METHODOLOGY.md` argues a second, open-registry hull is what makes the comparison
interpretable.
""")

code("""
im = pd.read_parquet(INTERIM / "impacts.parquet")
view = im[(im.smoothing_window == 3) & (im.year == 2024)]

display(view[["option", "country", "gcb_name", "hk_treatment", "power_estimate",
              "delta_e_mt", "baseline_mt", "delta_e_pct"]]
        .sort_values(["option", "hk_treatment", "power_estimate"])
        .style.format({"delta_e_mt": "{:.4f}", "baseline_mt": "{:,.1f}",
                       "delta_e_pct": "{:.6f}"})
        .set_caption("§7  2024 allocation impacts, w=3"))

fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
for ax, est in zip(axes, sorted(view.power_estimate.unique())):
    d = view[view.power_estimate == est]
    p = d.pivot_table(index="option", columns="hk_treatment", values="delta_e_pct")
    p.plot(kind="bar", ax=ax, logy=True, width=0.8)
    ax.set_title(f"power estimate {est}")
    ax.set_ylabel(r"$\\Delta E\\%$ of national budget (log)")
    ax.legend(title="HK treatment", fontsize=8)
fig.suptitle("§7  same emissions, two territory conventions — a ~370x swing", y=1.02)
plt.tight_layout(); plt.show()

spread = impacts.scenario_spread(im)
display(spread[spread.year == 2024][["option", "country", "hk_treatment",
                                     "delta_e_mt_min", "delta_e_mt_max", "spread_ratio"]]
        .style.format({"delta_e_mt_min": "{:.4f}", "delta_e_mt_max": "{:.4f}",
                       "spread_ratio": "{:.2f}x"})
        .set_caption("§8.1  scenario spread — the uncertainty band, not an error"))
""")

# ---------------------------------------------------------------- §8
md(r"""
---
# §8 — Sensitivity and validation

### §8.1 The two dominant drivers

1. **Installed power and design speed** — three estimates, no primary.
2. **Speed-smoothing window** — the $v^3$ bias.

Remaining uncertainties — coverage correction, displacement convention, TEU estimation,
engine-type assignment — are documented qualitatively rather than propagated.

### §8.2 Validation

`THETIS-MRV` is used **only** to validate, never as an input — it is EU-scope and this study
allocates globally. Feeding it back would make the comparison circular and import an EU
boundary into a global allocation.
""")

code("""
checks = validate.run_all(cfg, vessel, spine, port_calls, legs,
                          activity.coverage_by_year(spine), hourly, yearly, estimates)
for c in checks:
    print(c.line())
    if c.basis:
        print(f"            basis: {c.basis}")

display(validate.summarise(checks).style.set_caption("§8.2  validation summary"))
print("\\nThe fleet-envelope FAIL is the EXPECTED result for estimate A (§2.2).")
""")

md("""
---
## What remains open

These are stated rather than worked around. Each is a named error or a PENDING marker in the
code, never a substituted default.

| # | Item | Effect |
|---|---|---|
| 1 | **Vessel B not selected** | Allocation is degenerate at n = 1; §0.2 steps 1–4 are automated, step 5 needs Equasis |
| 2 | ~~Selin supplementary Table 1~~ | **Closed** — 199 countries, no Hong Kong row; the paper folds it into China |
| 3 | ~~Coastline layer~~ | **Closed** — land derived from EEZ_land_union minus EEZ v12 |
| 4 | **Estimate C** | No free source for installed power / design speed; raises on use |
| 5 | Smoothing window | Now measured across the full series, not one day: bias 1.94× → 1.38× at w=3 |
| 6 | Joint-regime EEZs | Default `ISO_SOV1`, affected hours reported separately |
| 7 | THETIS-MRV export | Annual CO₂ remains **UNVERIFIED** against external ground truth |

### Findings that contradict the specification

* **Coverage is not uniform.** §1.7 and §4.5 assume it is negligible from 2024 alone. It runs
  36%–99.98%, and two contiguous lay-ups mean a uniform correction would fabricate voyages.
* **Table 16's Port 1–5 nm column is tanker-only.** §4.1 reproduces it without the footnote.
* **At berth cannot be found from anchorage distance.** Port-visit intervals are used instead.
* **Estimate B's power range is wider than quoted.** §2.2 rounds the Froude speeds to 22–23 kn;
  the full 21.48–23.75 kn range gives 64,788–93,656 kW rather than 69,600–85,100 kW.
""")

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata.kernelspec = {"display_name": "Python 3", "language": "python", "name": "python3"}
OUT.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, OUT)
print(f"wrote {OUT} ({len(cells)} cells)")
