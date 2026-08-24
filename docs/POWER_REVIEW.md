# Review: power estimation in `emissions-allocation`

**Scope.** Verification of the main-engine power calculation in `docs/METHODOLOGY.md` §2 and §4 and its implementation in `src/emissions_allocation/specs.py` and `emissions.py`, against three external references, with an assumption register and a set of proposed changes aimed at making the model work for ship types other than containers.

**References used**

| Ref | Document | Role here |
|---|---|---|
| MERB | *The Maritime Engineering Reference Book*, **Chapter 6, "Marine engines and auxiliary machinery"** (the file supplied) — §6.3.1 rating, §6.3.4 derating, §6.3.6 slip, §6.3.7 propeller law, §6.3.8–6.3.9 fuel and Admiralty coefficients, §6.3.11–6.3.12 propeller performance and power build-up | Physical basis and limits of the cube law |
| MoSES | Schwarzkopf et al. (2021), *A ship emission modeling system with scenario capabilities*, Atmos. Env. X 12, 100132 | Peer model using the same load formulation; attribute-uncertainty budget |
| EPA | USEPA `Marine_Emissions_Tools` (`ShipPowerModel` + `ShipEF` R packages) and its documenting paper, *Power models and average ship parameter effects on marine emissions inventories*, JA&WMA 69(5), plus EPA *Port Emissions Inventory Guidance* (EPA-420-B-22-011) | Reference implementation of four power models and their inter-comparison |

> **Note on the supplied file.** The PDF text supplied is Chapter **6**, not Chapter 5. Chapter 5 of that book ("Powering") is the one that carries hull resistance, the wake/thrust-deduction chain, sea and service margins, and propeller design — Chapter 6 cross-references it repeatedly (§5.6, §5.7, §5.7.6, §5.15). Chapter 6 is still the right chapter for engine rating, the propeller law and the Admiralty coefficient, which is what this review needs; but if the resistance-based route in Part C is taken, Chapter 5 is the text to get.

---

## Verdict

The formula is the right first-order formula and it is implemented cleanly. Two things are wrong with it, one of them large and systematic.

**Round 2 — re-verified against the repo as of 2026-08-24 18:40.** Five findings were closed between the two passes. Status column below; the closed findings are kept in the body as the record of why the change was made.

| # | Finding | Severity | Direction / size | Status |
|---|---|---|---|---|
| F1 | Cube law anchored at `P = MCR` when `V = V_ref`. Every source used here anchors it at 75–83 % of MCR. | **Major** | **+20 % to +33 %** on main-engine power, systematically, across *all* estimates | **Closed** |
| F2 | No displacement or draught term. The model cannot distinguish laden from ballast. | **Major for non-containers** | ±15–25 % for tankers and bulkers; near-zero for containers | **Open — now the top item** |
| F3 | Speed exponent fixed at 3. | Moderate | ±10 % depending on operating speed | Plumbing done, axis not |
| F4 | Sea margin, weather and fouling omitted, and the reference point is not declared as trial or service. | Moderate | −10 % to −25 %, and a possible spurious inter-year trend | Declaration closed; quantification open |
| F5 | SOG used for STW, documented but not quantified or tested. | Moderate | ±16 % at n = 3 | Open |
| F6 | Low-load fuel penalty uses IMO `CF_L` alone; EPA's LLAF is up to 50 % larger below 10 % load. | Moderate | +0 % to +50 % on the low-load hours | Open |
| F7 | Smoothing window crosses port-call boundaries, and the smoothed speed also drives mode assignment. | Moderate | Misclassifies berth/manoeuvring hours → wrong Table 17 auxiliary demand | **Closed** |
| F8 | `size_for_table17` silently passes DWT into a **cbm**-indexed band for gas carriers. | **Bug** | Wrong Table 17 row for every LNG/LPG carrier | **Closed** |
| F9 | Stale, self-contradicting artefacts left in the docs and config. | Hygiene | Contradiction inside `METHODOLOGY.md`; dead config that would `KeyError` | **Closed** |
| F10 | Three free cross-checks available and unused. | Opportunity | Would bound F1 and F2 with no new data | Open |
| **F11** | **New.** The 7 % cutoff interacts with `f_ref`: the speed at which the main engine is declared off has moved up. | Check | See below | **New, open** |
| **F12** | **New.** Estimate B now sits on a different anchor (`f_ref = 1.0`) from A and D, so the §8.1 spread is no longer like-for-like. | Reporting | See below | **New, open** |

F1 mattered more than it looked, because it was **invisible to the sensitivity design**: §8.1 carries two axes — power estimate (A/B/D) and smoothing window — and every one of the three power estimates inherited the same anchoring error, so the reported spread did not bound it.

---

## Round 2 — what changed

### Closed, and correctly

**F1.** `PowerEstimate` now carries `load_at_reference` and `speed_exponent`; `main_engine_load` takes both and validates them (`0 < f_ref ≤ 1`, `n > 0`). Resolution is per estimate × ship type from `defaults.power_reference`, with **no shared default** — a missing row raises `MissingParameter` rather than silently anchoring at 1.0. That is the right failure mode and it is what makes the fix survive contact with a second ship type. Values: A = 0.75 (MEPC.333(76) original-MCR curve), D/container = 0.83 (EPA convention), D/vehicle = 0.75 — correctly inheriting the EEXI `V_ref` it is paired with rather than reusing EPA's 0.83 for a speed EPA did not supply. `METHODOLOGY.md` §4.2 is rewritten to match, and two tests pin the behaviour (`test_reference_load_scales_the_speed_power_curve`, `test_speed_exponent_is_configurable_per_reference_curve`).

**F4, declaration half.** `reference_condition` is now a **required** field — a row without one raises. So every estimate must state whether its reference point is calm-water/summer-load-draught or something else. This is what makes it possible to add a sea margin later without double-counting; that is the remaining half.

**F7.** `add_smoothed_speeds` now builds an `underway` mask from `is_inactive` and the port-visit intervals, forms contiguous segments with `underway.ne(underway.shift()).cumsum()`, and smooths **within** segments. Applied to the `imo2020` branch too, which is easy to forget. Berth hours no longer acquire speed from adjacent cruise hours, and Table 16 no longer sees smeared speeds at arrivals and departures.

**F8.** Replaced the catch-all with an explicit `field_by_unit = {"gt": "gt", "dwt": "dwt", "cbm": "cbm_capacity"}` and a raise for an unmapped unit — and a second raise, with a readable message, when the vessel has no value for the required field. Gas carriers can no longer land in the wrong band silently.

**F9.** Dead `defaults.eexi_curve_fit` block removed; `specs.py` docstring now reads 25.55 kn; `METHODOLOGY.md` §8.3 corrected to *"25.55 kn after the MEPC.333(76) cap, still just above the envelope"*; the unused `engine` binding in `build_hour_model` is gone.

### Two new findings that the F1 fix creates

**F11 — the 7 % cutoff now bites at a higher speed.** `Ẇ_ME = 0` below `Load = 0.07`. With `f_ref` applied, the speed at which that triggers moves:

```
before:  Load = (S/V_ref)³         → 0.07 at S = 0.412 · V_ref = 10.5 kn
after:   Load = 0.75·(S/V_ref)³    → 0.07 at S = 0.454 · V_ref = 11.6 kn
```

So for vessel A every hour between roughly 10.5 and 11.6 kn now has its main engine switched off entirely, where before it did not. 11.6 kn is a plausible slow-steaming speed, not an idle one. The IMO's 7 % threshold was defined against *its* load definition, and it is not obvious it should be applied unchanged to a load that has been rescaled by `f_ref`.

This is a check, not a verdict: **count the hours in that band before deciding.** If it is a handful, ignore it. If it is thousands, the cutoff needs either to be applied to the unscaled `(S/V_ref)³` or replaced by EPA's 2 % floor, which never zeroes the engine at all. Either way, record which.

The Table 16 `0.65` load threshold moves the same way — from `0.866·V_ref` (22.1 kn) to `0.954·V_ref` (24.4 kn) — but for a hull that slow-steams well below both, this changes nothing in practice. Worth one assertion, not worth a redesign.

**F12 — the estimate spread is no longer like-for-like.** B carries `f_ref = 1.0` while A and D carry 0.75/0.83, because Charchalis's Table 1 does not establish the installed-MCR fraction. The config says so plainly and refuses to guess, which is the right call. But the consequence is that the A-vs-B spread reported in §8.1 now mixes a genuine difference in estimated installed power with a difference in anchoring convention, and a reader will not separate them.

Two options: resolve the Charchalis ambiguity from the paper and give B a real `f_ref`; or report B on its own axis, labelled as a reference-power curve rather than an MCR estimate, and let the headline spread be A-vs-D. The second costs nothing and is honest today.

### Still open, in priority order

1. **F2 / C2 / C3 — the draught and displacement term.** Zero occurrences of `ballast`, `loading_state`, `T_ref` or `draught_ratio` anywhere in `src/` or `config/`. This is now the single largest remaining gap and it is *the* generalisation blocker: the model still cannot tell a laden tanker from a ballast one. Everything in C2 and C3 below stands unchanged.
2. **C5 — the type vocabulary.** `fleet_speed_envelope` is still container-only, so `check_fleet_envelope` returns `PENDING`/unassessed for every other ship type — the validation is inert for the fleet. `defaults.admiralty.by_ship_type` is still container-only and `epa_dwt_power.by_ship_type` still has two rows. `test_specs_and_fuel.py` does now assert all twelve EEXI types resolve for speed and power, which is good; the equivalent assertion for envelope, Admiralty and EPA rows does not exist.
3. **F3 — make the exponent an axis.** The plumbing is in (`speed_exponent`, per estimate × type, validated) but every configured row is `3.0` and `run:` has no exponent axis. One config line away from being a real sensitivity.
4. **F5, F6, F10** — unchanged; no `LLAF`, no STW perturbation, none of the three cross-checks.

The revised priority order at the end of this document (Part D) still holds with steps 1, 2, 3 and 4 struck out.

---

# Part A — Verification

## F1. The cube law is anchored at the wrong point on the curve

**What the code does.** `emissions.main_engine_load`:

```
Load_i = min( (SOG_i / V_design)^3 , 1.0 )
W_ME,i = MCR · Load_i
```

so it asserts `W_ME = MCR` when `SOG = V_ref`. `METHODOLOGY.md` §4.2 states this explicitly: *"At the paired reference condition for a scenario, `P = MCR` when `v = V_ref`."*

**Why that is wrong.** A ship's reference or service speed is not the speed at 100 % MCR. All three references say so, independently and with numbers that agree:

* **MERB §6.3.1.** *"Prudent shipowners usually insist that the engines be capable of maintaining the desired service speed fully loaded, when developing not more than 80 % (or some other percentage) of their rated brake power."* And: the in-service upper power level *"could be as much as 20 % less than the engine maker's guaranteed maximum continuous rating."* The distinction between **CSR** (the moderate in-service figure) and **MCR** (the builder's continuous set point) is the whole subject of that section.

* **EPA `ShipPowerModel`.** `P_ref` at service speed is modelled as **83 % of total installed propulsive power**, and service speed is defined as **94 % of maximum speed**. Those two statements are the same statement: 0.94³ = **0.830**. The EPA convention is internally consistent and it is the cube law anchored at 0.83 MCR.

* **MEPC.333(76) — the repo's own primary source for Estimate A.** `P_ME` in the attained-EEXI calculation is *"83 % of the limited installed power (MCR_lim) or 75 % of the original installed power (MCR), whichever is lower."* `V_ref` is the speed **at that power**, at summer load draught in calm water. So `V_ref,avg` and `MCR_avg` from the Appendix are *not* a matched (speed, power-at-that-speed) pair in the sense the code uses them: `V_ref,avg` is the speed at 0.75–0.83 × `MCR_avg`.

  The resolution says so a second time, in its own approximation rule: `V_ref,app = V_ref,avg · (MCR / MCR_avg)^(1/3)`. That cube-root scaling only makes sense if the resolution is itself treating the pair as a point on a cubic speed–power curve with a fixed load fraction. The repo has adopted the endpoints of that curve and dropped the fraction.

**Magnitude.** Correcting the anchor multiplies main-engine power, and therefore main-engine CO₂, by `f_ref`:

| Estimate | What its MCR is | What its speed is | `f_ref` | Effect on `W_ME` |
|---|---|---|---|---|
| A (EEXI) | installed `MCR_avg` | speed at 0.75–0.83 MCR | 0.75–0.83 | **−17 % to −25 %** |
| B (Admiralty) | `Δ^{2/3}V³/C_adm` — see caveat | Froude service speed | ambiguous, see below | unresolved |
| D (EPA) | *rated* main-engine power (EPA Tables 4-3/4-5 regress rated hp on DWT) | Froude / EEXI service speed | ~0.83 | **−17 %** |

For Estimate B the answer depends on what Charchalis's power column is. If his table pairs service speed with *installed* power, then `C_adm = Δ^{2/3}V³/MCR` already absorbs the load fraction and B is self-consistent as written — but then B's output is not comparable to A's and D's on the same axis. If his power column is *delivered* power at service speed, B needs the same correction. **This has to be resolved from the paper; it is currently undocumented and it decides whether B is being used correctly.**

**Knock-on effect that is not a simple rescale.** Table 16 assigns operating mode partly from main-engine load, with a threshold at 0.65. Reducing every load by 17–25 % moves hours across that threshold, from *normal cruising* into *slow transit* and from *slow transit* into *manoeuvring*. Table 17 then charges a different auxiliary and boiler demand for those hours. Since this vessel spends 24.9 % of the study period inside port visits and auxiliary demand is a large share of the total, the correction propagates into the auxiliary term with the opposite sign. It cannot be applied as a post-hoc scalar on the CO₂ total.

**Fix.**

```python
def main_engine_load(sog_kn, reference_speed_kn, load_at_reference, exponent=3.0):
    return np.minimum(load_at_reference * (sog_kn / reference_speed_kn) ** exponent, 1.0)
```

with `load_at_reference` a *property of the estimate*, carried on `PowerEstimate` alongside `design_speed_kn` and `mcr_kw`, sourced per estimator (A: 0.75 or 0.83 from MEPC.333(76); D: 0.83 from EPA; C: whatever the sea-trial report says). Never a shared default — the whole point is that it differs by estimator.

## F2. There is no displacement or draught term — the main obstacle to other ship types

The load equation depends on speed alone. Two identical hulls, one laden and one in ballast, get identical power at identical speed. That is a defensible approximation for a container ship and a poor one for everything that trades in ballast.

* **MERB §6.3.9** gives the Admiralty coefficient as `A_c = Δ^{2/3} V³ / P` — displacement is in the relation at the ⅔ power, and the repo already uses this form in Estimate B to *size* the engine, then discards it when computing hourly load.
* **EPA's Admiralty Law** is exactly the missing term, written as a ratio so no coefficient is needed:

  ```
  LF = ( T_reported^{2/3} · V_reported³ ) / ( T_ref^{2/3} · V_ref³ )
  ```

  where `T_ref` is the summer load line draught.
* **EPA's finding is directly on point.** Across the fleet, Admiralty Law gives 9.5–18.1 % lower power than Propeller Law. The deviation is *largest* for bulk carriers and tankers, *"operating in either fully loaded or ballast conditions"*, and *smallest* for container ships, which *"continue to operate well below their service speed"* and sail effectively fully loaded. The paper explicitly flags Ro-Ros and passenger ships as the untested variable group.

So: the pure cubic law has been validated on the one ship type where it performs best, and the pilot's second vessel is a vehicle carrier — a type EPA names as unvalidated.

**The constraint.** GFW's presence product does not carry AIS draught. The voyage-related AIS message does, but you do not have it. Three options, in order of cost:

1. **Two-state laden/ballast by leg**, inferred from the port-call sequence and ship type (a tanker leaving a loading terminal is laden; the return leg is ballast), with `T_ballast/T_summer` ratios by type from the IMO Fourth GHG Study. Cheap, uses data you already pull.
2. **A configured constant `T/T_ref` per ship type** — a single number per type, reported as an assumption. Costs nothing, bounds the effect.
3. **An explicit sensitivity axis** `draught_ratio ∈ {ballast, laden}` run as a bracket, exactly like the smoothing window. This is the option most consistent with how the rest of the project handles irreducible uncertainty.

Note that even option 2 makes the model *structurally* type-aware rather than container-shaped, which is what the request asks for.

## F3. The exponent is fixed at 3

MERB §6.3.7, verbatim: *"The propeller law index is not always 3, nor is it always constant over the full range of speeds for a ship. It could be as much as 4 for short high speed vessels but 3 is normally satisfactory for all ordinary calculations."*

§6.3.12 puts numbers on it from an actual power build-up: over the whole range the index is ≈3, but *"For the range 95–109 rev/min, the index increases to 3.5 for the power … Between 120 and 109 rev/min a more closely drawn curve shows the index to rise to 3.8."*

So 3 is defensible as a fleet-average, and it is the value MoSES uses too (its Eq. 5). But it is a choice, it is not constant across the speed range, and it is hull-form dependent — which is precisely the axis you need for generalisation. Make `exponent` a per-ship-type config value with a documented default of 3.0 and a sensitivity at 3.5. Its interaction with F1 is worth stating: at slow-steaming speeds (`SOG < V_ref`) a *higher* exponent *lowers* power, so F3 partly offsets F1 rather than compounding it.

## F4. Sea margin, weather and fouling are omitted, and the reference condition is undeclared

`METHODOLOGY.md` §4.2 says weather and fouling corrections *"are not imputed"*. That is the correct decision, but the write-up treats it as neutral and it is not.

The reference point is a **calm-water, clean-hull, summer-load-draught** point (that is what an EEXI `V_ref` is, and what a sea-trial point is). The model then applies that clean-water curve to an *achieved* speed over ground in real weather with a real hull. A ship making 15 kn into a head sea with six months of fouling is burning materially more than the calm-water 15-kn power, and the model charges it the calm-water figure.

MERB §6.3.11 quantifies the gap: shaft power for sea service is *"about 11–12 % more for the South Atlantic and 20–25 % more for the North Atlantic"* than trial conditions, and *"a small ship needs a greater margin"*; *"15 % margin over trial conditions equals 26.5 % over tank tests."*

Two consequences:

1. **Declare the reference condition.** Add a field to `PowerEstimate` recording whether `(V_ref, MCR·f_ref)` is a trial/calm-water point or an in-service point. Add a sea margin only for the former. Right now it is undefined, so it is impossible to tell whether adding a margin would double-count.
2. **Fouling over an eight-year window.** The study runs 2017–2024 with no drydock or hull-condition model. Hull and propeller degradation is commonly 0.5–1 % per year of added power between dockings, resetting at each docking. A constant-efficiency model over eight years cannot produce a trend that isn't in the speed data, which is fine — but it also means any inter-year change the pilot reports is a pure activity/speed effect by construction. Say so, or carry `fouling_pct_per_year` as an axis.

## F5. SOG is used where STW is required — quantify it

§4.2 notes *"Strictly, `v` is speed through water."* MERB §6.3.6 gives the size of the error: *"a following current may be as much as 2.5 % and heavy weather ahead may have an effect of more than twice this amount"*, and *"The effects on ship speed 'over the ground' by ocean currents is sometimes considerable."*

A ±5 % speed error is a **±16 % power error** at n = 3. That is the same order as the entire smoothing-window sensitivity the project already runs, and it costs nothing to add: perturb the smoothed speed series by ±5 % and re-run. It belongs in §8.1 as a third axis.

## F6. Low-load fuel penalty: `CF_L` alone is optimistic

The repo applies IMO equation (10) to the main engine and notes correctly that the CO₂ low-load factor `LLF` is 1.00 at every load, so no further correction is applied. EPA disagrees, and by a wide margin at the bottom of the range. Its CO₂ low-load adjustment (applied below 20 % load) is an emission factor in g/kWh:

```
LLAF_CO2 = 44.1·LF⁻¹ + 648.6          [g CO2 / kWh]
```

whose high-load asymptote is 692.7 g/kWh. Comparing the two as multipliers on high-load fuel:

| Load | IMO `CF_L` | EPA LLAF ratio | Divergence |
|---|---|---|---|
| 0.20 | 1.156 | 1.255 | +9 % |
| 0.10 | 1.214 | 1.573 | +30 % |
| 0.07 | 1.233 | 1.846 | **+50 %** |

Two follow-ons. First, the repo zeroes the main engine below 7 % load; EPA never zeroes it, applying a **2 % floor** and a 100 % ceiling instead. Second, once F1 is fixed all loads drop by ~20 %, so *more* hours land in the divergent region. For a ship that slow-steams and manoeuvres a lot this is not a rounding term. Add EPA's LLAF as an alternative `low_load_treatment` and report both.

## F7. Smoothing crosses operating-mode boundaries, and then decides the mode

`activity.smooth_speed` is a centred rolling mean with `min_periods=1`. Mathematically this is the right family of estimator: a rolling mean of hourly `d/Δt` values *is* total path length over elapsed time, so it is distance-conserving and the v³ Jensen argument in §1.6 is sound.

The problem is where the window is applied. It runs across the whole series, so a 7-hour centred window straddling a departure mixes berth hours (0 kn) with cruise hours (16 kn). Two effects:

* Berth and anchored hours acquire a non-zero smoothed speed.
* The *same* smoothed series is fed to `assign_modes`, which runs Table 16. Table 16's first two rows key off SOG ≤ 1 kn and 1–3 kn. Smearing speed across the boundary moves hours out of *at berth* into *anchored* or *manoeuvring*, and Table 17 charges 1,300 / 1,800 / 3,250 kW auxiliary for those three modes respectively — a 2.5× swing on a term that covers a quarter of this vessel's hours.

The `MODES_WITHOUT_MAIN_ENGINE` guard protects the main-engine term but not the mode assignment that produced it.

**Fix:** smooth *within contiguous underway segments*, delimited by the `port_visit_hour` intervals you already build, so no window spans a port call. Windows shorter than the segment behave as now; segments shorter than the window get the segment mean. This also makes the window sensitivity honest — at present, w=7 and w=1 differ partly because of boundary smearing rather than because of the v³ bias they are meant to probe.

Open item 5 also still stands and is load-bearing: the 1.67× / 1.19× figures come from 11 underway hours of one day of one vessel.

## F8. Bug — gas carriers get DWT passed into a cbm-indexed Table 17 band

`specs.size_for_table17`:

```python
unit = table["size_unit"]
if unit == "TEU":  return ship_type, resolve_teu(vessel, cfg), unit
if unit == "gt":   return ship_type, vessel.require_spec("gt"), unit
return ship_type, vessel.require_spec("dwt"), unit      # <- catches "cbm"
```

`config/emission_factors.yaml` sets `liquefied_gas_tanker: size_unit: cbm`. Any LNG or LPG carrier therefore has its **deadweight** matched against **cubic-metre** bands. A 90,000 m³ LNG carrier is roughly 70,000 DWT, so it lands one or two bands low and gets too little auxiliary and boiler power — silently, with no error. This is precisely the failure mode the docstring warns about for GT ("Passing deadweight to a GT-indexed row lands in the wrong band and returns a plausible wrong number") and the code guards GT but not cbm.

Make the fallback explicit rather than a catch-all:

```python
BASIS = {"TEU": ..., "gt": "gt", "dwt": "dwt", "cbm": "cbm_capacity"}
```

and raise `MissingParameter` for a unit with no configured spec, in the same style as the rest of the module.

## F9. Stale artefacts that now contradict each other

Not physics, but they will mislead a reader or a future run:

* `docs/METHODOLOGY.md` §8.3 validation table still reads *"Estimate A fails this test at 28.92 kn"*, while §2.2 of the same document records the correction and gives 25.55 kn with the caps applied. One document, two answers.
* `src/emissions_allocation/specs.py` module docstring, lines 12–14, repeats the superseded claim: *"Returns 28.92 kn for vessel A, which is outside the observed modern container fleet envelope."*
* `config/vessel_specs.yaml` `defaults.eexi_curve_fit` is dead — `config.py:426` loads `eexi_parameters.yaml` instead — but it still contains a containership power row `{C: 0.504, D: 1.030}` with **no cap**, rounded coefficients, and key names (`C`, `D`) that do not match the reader in `specs.estimate_a_eexi` (`D`, `F`). If anything ever wires it up it raises `KeyError`; if someone reads it, they get the wrong constants. Delete it or replace it with a one-line pointer.
* `emissions.build_hour_model` line 115 binds `engine = vessel.require_spec("engine_type")` and never uses it.

## F10. Three free cross-checks you are not running

1. **Attained EEXI as an internal consistency test.** With `MCR`, `V_ref`, capacity, `SFC` and `C_F` you can compute an attained EEXI and compare it against the required EEXI line for that type and size. It is a free, ship-type-general test of whether a (power, speed) pair is physically plausible — it would have caught the uncapped-coefficient error immediately, and it works for bulkers and tankers where you have no fleet speed envelope.
2. **MEPC.333(76)'s own scaling as a cross-estimate test.** `V_ref,app = V_ref,avg · (MCR / MCR_avg)^(1/3)`. Estimate D supplies an independent MCR; the resolution then tells you what reference speed *should* accompany it. For vessel A: 92,868 / 67,912 = 1.368, cube root 1.110, × 25.55 kn = **28.4 kn** — outside the 6.0–24.5 kn container envelope. That is a clean, sourced signal that the EPA container regression (R² = 0.59, fitted on 20,000–70,000 DWT) is being extrapolated past its range for a 156,610 DWT hull. At present D pairs the EPA power with an *unrelated* Froude speed, which combines two independent errors in an uncontrolled direction instead of testing one against the other.
3. **IMO Fourth GHG Study fleet averages.** The study publishes average installed main-engine power and design speed by ship type × size bin (it needs them for its own bottom-up aggregation). You already have the PDF and already extract Tables 17, 19, 20 and 21 from it. Those averages are a free fifth estimate — call it **E** — that covers every ship type with the same provenance as the auxiliary tables, and it is the estimate most likely to scale to the full fleet.

---

# Part B — Assumption register

Everything the current power number rests on. Ticks mark assumptions already documented in `METHODOLOGY.md`; the rest are implicit.

### Speed–power relationship

| # | Assumption | Documented? | Direction if wrong |
|---|---|---|---|
| 1 | Propulsion power ∝ speed³ | ✓ §4.2 | ±10 % (MERB: index 3–3.8 in service) |
| 2 | The exponent is constant across the whole speed range | ✗ | Understates at high load, overstates at low |
| 3 | `P = MCR` at `V = V_ref` | ✓ stated, ✗ unjustified | **+20–33 % overstatement** (F1) |
| 4 | The reference speed–power pair is internally consistent (same hull, same draught, same condition) | partly, for D | D deliberately mixes provenances |
| 5 | Displacement / draught constant over the study period | ✗ | ±15–25 % for tankers & bulkers (F2) |
| 6 | Load capped at 1.0, no lower bound except the 7 % cutoff | ✓ | EPA uses a 2 % floor instead of zeroing |
| 7 | Calm water, clean hull, no added resistance | ✓ §4.2 | −10 to −25 % (MERB §6.3.11) |
| 8 | No hull/propeller degradation over 2017–2024 | ✗ | Flattens any real inter-year efficiency trend |
| 9 | SOG ≈ STW | ✓ noted, ✗ unquantified | ±16 % at n = 3 (F5) |
| 10 | No shaft generator / PTO drawing off the main engine | ✗ | Understates ME load, double-counts aux |

### Ship specification

| # | Assumption | Documented? | Note |
|---|---|---|---|
| 11 | MEPC.333(76) type-average curves apply to an individual hull | ✓ §2.2 | Regression means, not this ship |
| 12 | DWT is an adequate size proxy for every type | partly | The resolution's own vehicle-carrier `f_cVEHICLE` correction says otherwise; also wrong for gas carriers and cruise |
| 13 | Charchalis `C_adm` (1,200–1,400 TEU feeders) extrapolates to 13,200 TEU | ✓ flagged as weakest joint | Tenfold size jump |
| 14 | Displacement = mean of geometric and DWT/0.80 | ✗ (range reported, point estimate averages) | Averaging two conventions rather than choosing one |
| 15 | Fixed `C_B` per ship type | ✗ | Varies with size within a type |
| 16 | EPA regression valid outside its 20,000–70,000 DWT fit range | ✓ flagged | R² = 0.59 |
| 17 | TEU from beam via Cepowski & Chorab | ✓ §2.1 | Feeds the Table 17 band, not the power estimate |
| 18 | Slow-speed diesel by default | ✓ §2.3 | Fails for ferries, ropax, small cargo — see Part C |
| 19 | One main engine, single screw, fixed pitch | ✗ | Wrong for cruise, ropax, twin-screw, diesel-electric |
| 20 | No power limitation (EPL/ShaPoLi) fitted | ✗ | Post-2023 EEXI compliance makes this increasingly false |

### Activity and mode

| # | Assumption | Documented? | Note |
|---|---|---|---|
| 21 | Hourly centroid speed is an adequate instantaneous speed | ✓ §1.6 | The v³ bias is the project's own finding |
| 22 | A centred moving average is the right smoother | ✓ | Sound, but applied across mode boundaries (F7) |
| 23 | Smoothing window transferable from one day to eight years | ✓ open item 5 | 11 underway hours of evidence |
| 24 | Missing hours share the observed speed distribution | ✓ §4.5 | Coverage correction |
| 25 | Table 16 thresholds (0.65 load, 1/3/5 kn, 1/5 nm) apply to all types | ✗ | Only the "Port 1–5 nm" column is type-aware |
| 26 | Distance alone separates berth from anchored | ✓ §4.1 | No AIS nav status in GFW |
| 27 | Table 17 auxiliary demand is independent of speed and load within a mode | ✗ | Step function by mode |
| 28 | Boiler demand at sea is zero for this type | ✓ via Table 17 | Correct for containers; must *not* be generalised — tankers heat cargo at sea |

---

# Part C — Proposed changes

Ordered by effect on correctness, with the type-generalisation goal as the organising principle.

## C1. Make the reference load fraction an explicit, per-estimate parameter — *do this first*

```yaml
# vessel_specs.yaml, per estimator
power_estimates:
  A:
    load_at_reference:
      value: 0.75
      source: "MEPC.333(76) — P_ME = 75% of MCR (or 83% of MCR_lim), and V_ref is the speed at that power"
      method: "reference load fraction; V_ref and MCR_avg are the endpoints of one cubic curve, not a matched pair"
  D:
    load_at_reference:
      value: 0.83
      source: "EPA ShipPowerModel — P_ref at service speed = 83% of installed; service speed = 94% of max; 0.94^3 = 0.830"
```

Carry it on `PowerEstimate`, thread it through `main_engine_load`, and re-run. Expect main-engine CO₂ to fall 17–25 % and auxiliary CO₂ to *rise* as hours redistribute across Table 16 modes. Resolve the Estimate B ambiguity (installed vs delivered power in Charchalis's table) at the same time and record the answer in the config, not in a comment.

## C2. Replace Estimate B with the Admiralty *law* rather than the Admiralty *coefficient*

This one change fixes F2 and the generalisation problem together.

Estimate B currently uses `C_adm` to size an engine, and correctly refuses to run for any type without a published calibration — good design, but it means B is unavailable for eleven of twelve ship types. EPA's Admiralty **Law** needs no coefficient at all, because it is a ratio against a reference point you already have:

```
Load_i = load_at_reference · (T_i / T_ref)^{2/3} · (SOG_i / V_ref)^n
```

`T_ref` is the summer load line draught (Equasis carries it). `T_i` is the operating draught — which you cannot observe, so it becomes the laden/ballast state from F2. The result:

* works for every ship type on day one, with no per-type calibration;
* introduces the displacement dependence the model is missing;
* keeps the container-ship result almost unchanged (containers trade near-laden), so it does not disturb the existing pilot;
* moves bulkers and tankers by the 9.5–18.1 % EPA measured, in the right direction.

Keep the `C_adm` version as a separate estimate for container ships if you want the continuity, but it should not be the mechanism by which the model becomes type-general.

## C3. Add a laden/ballast state per voyage leg

You already build `voyage_leg` from port-visit events. Add a `loading_state` column, resolved in this order:

1. AIS voyage-message draught, if a source for it ever becomes available (not GFW);
2. a per-type rule keyed on the port pair (a crude oil tanker leaving a loading terminal is laden; the leg back is ballast);
3. a configured constant `T/T_ref` per ship type as a fallback;

and run `{always_laden, inferred, always_ballast}` as a sensitivity axis alongside the smoothing window. For containers all three collapse; for a VLCC they will not, and that difference *is* the finding.

## C4. Make the exponent and the low-load treatment configurable axes

```yaml
run:
  speed_exponent: [3.0, 3.5]              # MERB §6.3.7 and §6.3.12
  low_load_treatment: [imo_cfl, epa_llaf] # F6
  stw_perturbation_pct: [-5, 0, +5]       # MERB §6.3.6
```

Three new axes, all cheap, all sourced. Together with C1 and C3 the scenario space becomes a defensible uncertainty envelope rather than a two-axis slice.

## C5. Close the ship-type vocabulary

There are currently four type namespaces with no test that they agree:

| Table | Types | Coverage |
|---|---|---|
| `eexi_parameters.speed` / `.power` | 12 IMO EEXI categories | complete ✓ |
| `emission_factors.auxiliary_boiler_power.ship_types` | 19 IMO Table 17 categories | complete ✓ |
| `defaults.admiralty.by_ship_type` | container only | 1 of 19 |
| `defaults.epa_dwt_power.by_ship_type` | container, vehicle | 2 of 19 |
| `defaults.fleet_speed_envelope` | container only | 1 of 19 — the validation check is inert for everything else |

Add a test that asserts every canonical ship type resolves in **every** table it needs (EEXI speed, EEXI power, Table 17 with the right size basis, mode matrix, envelope), and fails the config rather than the run. Then:

* **Fleet speed envelopes for all types.** Derive them from MEPC.333(76) itself — evaluate `V_ref,avg` across the DWT range for each type and take a ±15 % band — or from the type-specific literature. Anything is better than `None`, which currently means "silently unchecked".
* **Fix F8** (cbm) while you are in `size_for_table17`.
* **Extend EPA regressions.** EPA (2000) Tables 4-3 and 4-5 publish relations for more types than the two configured; transcribe them all, with their R² and fit ranges, so Estimate D is available fleet-wide.

## C6. Replace the constant engine-type assignment with a rule

`default_engine_type: SSD` is right for a 13,200 TEU container ship and wrong for ferries, ropax, small general cargo and offshore vessels — which are MSD or HSD, which changes both `SFC_base` (Table 19) and the fuel assignment (high-speed engines burn MGO). Two free rules:

* **IMO Fourth GHG Study Table 10** gives engine-type shares by ship type, size and build year — the study's own approach, and already in the PDF you have extracted.
* **MoSES's rule** (Zeretzke 2013), for the fuel side: 95 % of engines at 60–300 rpm and 70 % of engines at 300–1500 rpm burn residual fuel; the rest burn distillate.

## C7. Longer term — a resistance-based Estimate E

EPA's paper is the argument for doing this and the argument that it is affordable. Their finding: using **subtype-averaged** hull parameters instead of ship-specific ones changes fleet emission estimates by **< 2.5 %**, while the choice of power *model* changes them by 9.5–42.4 %. In other words, the model matters and the per-ship parameters mostly do not — which is exactly the situation you are in, with no IHS access.

A Kristensen (SHIP-DESMO) or Holtrop–Mennen implementation needs L, B, T, `C_B` and wetted surface. Cepowski & Chorab supply the first four by regression for containers; equivalent published fits exist for other types, and the IMO study's own size bins give a fallback. If you go this route, Chapter **5** of the Maritime Engineering Reference Book is the text for the resistance decomposition, and Chapter 6 §6.3.11 gives the quasi-propulsive coefficients you would need to close the loop to shaft power:

| Ship type | `η_D` |
|---|---|
| Tanker | 0.67–0.72 |
| Slow cargo vessel | 0.72–0.75 |
| Fast cargo liner | 0.70–0.73 |
| Ferry | 0.58–0.62 |
| Passenger ship | 0.65–0.70 |

plus 5–6 % for sterntube and plummer-block friction between delivered and shaft power. These are useful *today* as a plausibility band on any (power, speed, displacement) triple, even without a resistance model.

## C8. Add the three cross-checks from F10

Attained-EEXI consistency; the `V_ref,app` cross-estimate test; the IMO Fourth GHG Study fleet averages as Estimate E. All three use documents already in `data/external/`.

---

# Part D — Suggested order

| Step | Change | Why first |
|---|---|---|
| 1 | F9 — clear the stale docs and dead config | Ten minutes, and it stops the contradiction propagating into the next decision |
| 2 | F8 — the cbm bug | A one-line correctness bug that only bites once you leave containers |
| 3 | C1 — `load_at_reference` | The largest single error, and it changes every downstream number including mode assignment |
| 4 | F7 — segment-aware smoothing | Cheap, and it makes the existing window sensitivity mean what §8.1 says it means |
| 5 | C2 + C3 — Admiralty law and laden/ballast | The change that makes the model type-general rather than container-shaped |
| 6 | C5 — close the type vocabulary with a config test | Turns "will it work for a bulker?" from a question into an assertion |
| 7 | C4 — the three new sensitivity axes | Now the envelope is honest |
| 8 | C6, C7, C8 | Fleet readiness |

None of steps 1–5 requires new data. Steps 3 and 5 are the two that change the answer.

---

## Sources

* [Power Models and Average Ship Parameter Effects on Marine Emissions Inventories — PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6534469/) (the documenting paper for EPA's `ShipPowerModel`)
* [Marine Emissions Tools — USEPA GitHub](https://github.com/USEPA/Marine_Emissions_Tools)
* [EPA Emissions Models and Other Methods to Produce Emission Inventories](https://www.epa.gov/moves/emissions-models-and-other-methods-produce-emission-inventories)
* [EPA Port Emissions Inventory Guidance (EPA-420-B-22-011)](https://nepis.epa.gov/Exe/ZyPDF.cgi?Dockey=P1014J1S.pdf)
* [IMO Resolution MEPC.333(76)](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.333(76).pdf)
* [IACS Recommendation No. 172 — EEXI Implementation Guidelines](https://www.classnk.or.jp/hp/pdf/activities/statutory/eexi/eexi_rec_172_new_june_2022.pdf)
* Schwarzkopf, D.A. et al. (2021). A ship emission modeling system with scenario capabilities. *Atmospheric Environment: X* 12, 100132 (supplied)
* *The Maritime Engineering Reference Book*, Chapter 6, Marine engines and auxiliary machinery (supplied)
