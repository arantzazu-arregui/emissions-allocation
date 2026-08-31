-- §5 -- hourly CO2, scenario-keyed.
--
-- Joins the operating mode to IMO Table 17 (auxiliary and boiler power by ship type
-- x size band x mode), applies the Table 19 base SFCs and the equation (10) load
-- correction, and converts to CO2 with the Table 21 fuel factor.
--
--   FC_i     = [ W_ME,i*SFC_ME,i + W_AE,i*SFC_AE + W_BO,i*SFC_BO ] * dt   [g fuel]
--   E_CO2,i  = FC_i * EFf_i / 1e6                                        [tonnes]
--
-- with dt = 1 h.
--
-- Main-engine power is zero in the At berth and Anchored modes and below the 7%
-- MCR cutoff -- "At engine loads below 7%, fuel consumption and all the emissions
-- derived from the main engine are assumed to be zero" (printed p.70). Auxiliary
-- and boiler demand continues in those modes, which is the point: this hull spends
-- 24.9% of the period in port, so they are a large share of the total rather than
-- a correction term.
--
-- LLF does not appear. CO2's low-load factor is 1.00 at every load (Table 20),
-- because CO2 varies directly with fuel consumption, which is already
-- load-dependent.
--
-- Out-of-service hours carry no emissions at all -- see activity.classify_gaps.
-- They are not zero because the engines idled; they are zero because the hull was
-- not there.

SELECT
    m.imo,
    m.ts,
    m.scenario_id,
    m.power_estimate,
    m.smoothing_window,
    m.gap_treatment,
    m.is_interpolated,
    m.operating_mode,
    m.sog,
    m.me_load,
    f.fuel_type,
    f.in_eca,
    f.is_eu_eu_leg,

    -- Section 5 main engine
    m.w_me_kw,
    -- Section 5 equation (10): SFC_base * (0.455*Load^2 - 0.710*Load + 1.280)
    m.sfc_me_g_kwh,

    -- Section 5 Table 17, by ship type x size band x mode
    CASE o.auxiliary_method
        WHEN 'zero' THEN 0
        WHEN 'mcr_fraction' THEN $mcr_kw * o.auxiliary_mcr_fraction
        ELSE t.auxiliary_kw
    END                                   AS w_ae_kw,
    CASE o.boiler_method
        WHEN 'zero' THEN 0
        ELSE t.boiler_kw
    END                                   AS w_bo_kw,
    m.sfc_ae_g_kwh,
    m.sfc_bo_g_kwh,

    -- Section 5 fuel and CO2 for this hour
    m.w_me_kw * m.sfc_me_g_kwh            AS fc_me_g,
    (CASE o.auxiliary_method WHEN 'zero' THEN 0 WHEN 'mcr_fraction' THEN $mcr_kw * o.auxiliary_mcr_fraction ELSE t.auxiliary_kw END) * m.sfc_ae_g_kwh AS fc_ae_g,
    (CASE o.boiler_method WHEN 'zero' THEN 0 ELSE t.boiler_kw END) * m.sfc_bo_g_kwh AS fc_bo_g,
    (   m.w_me_kw       * m.sfc_me_g_kwh
      + (CASE o.auxiliary_method WHEN 'zero' THEN 0 WHEN 'mcr_fraction' THEN $mcr_kw * o.auxiliary_mcr_fraction ELSE t.auxiliary_kw END) * m.sfc_ae_g_kwh
      + (CASE o.boiler_method WHEN 'zero' THEN 0 ELSE t.boiler_kw END) * m.sfc_bo_g_kwh) AS fc_total_g,
    (   m.w_me_kw       * m.sfc_me_g_kwh
      + (CASE o.auxiliary_method WHEN 'zero' THEN 0 WHEN 'mcr_fraction' THEN $mcr_kw * o.auxiliary_mcr_fraction ELSE t.auxiliary_kw END) * m.sfc_ae_g_kwh
      + (CASE o.boiler_method WHEN 'zero' THEN 0 ELSE t.boiler_kw END) * m.sfc_bo_g_kwh) * m.ef_f / 1e6 AS co2_tonnes

FROM hour_model     AS m
JOIN fuel_assignment AS f ON f.imo = m.imo AND f.ts = m.ts
JOIN imo_table17     AS t
      ON t.ship_type = $ship_type
     AND t.mode      = m.table17_mode
     AND $vessel_size BETWEEN t.size_min AND coalesce(t.size_max, 1e18)
JOIN imo_table17_mcr_override AS o
       ON $mcr_kw >= o.mcr_min
      AND ($mcr_kw < o.mcr_max OR o.mcr_max IS NULL)
WHERE NOT m.is_inactive;
