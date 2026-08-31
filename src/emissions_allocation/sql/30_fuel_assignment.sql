-- §5 -- fuel assignment at vessel-hour grain.
--
-- Following Selin et al., a vessel-hour burns DISTILLATE (MDO/MGO) when ANY of
-- three conditions holds, and RESIDUAL (HFO) otherwise:
--
--   1. the main engine is high-speed          -- not applicable to either pilot hull
--   2. the position falls inside an ECA        -- point-in-polygon, 21_eca_join.sql
--   3. the hour belongs to a voyage leg between two EU ports
--
-- Condition 3 is read from an actual port-call sequence rather than inferred from
-- EEZ transits, which is a genuine improvement on what gridded-only data would have
-- forced. Vessel A has 27 consecutive EU->EU legs across the period, so the rule
-- genuinely fires rather than being carried for completeness.
--
-- Note on the 2020 sulphur cap: it moved most of the fleet from HFO to VLSFO
-- on 1 January 2020, but that is IMMATERIAL here. The Fourth GHG Study assigns
-- low-sulphur HFO the same carbon content and emission factor as HFO (Table 21,
-- LSHFO 1.0% -> 3.114), so the switch changes SOx, not CO2. No date branch appears
-- below, and that absence is deliberate.
--
-- An hour is matched to a leg when it falls between the previous port call's
-- departure and the next call's arrival. Hours INSIDE a port visit belong to no leg
-- and so fall through to condition 2 alone.

SELECT
    h.imo,
    h.ts,
    e.in_eca,
    e.eca_area,
    coalesce(l.is_eu_eu, FALSE)                    AS is_eu_eu_leg,
    $main_engine_is_high_speed                     AS main_engine_high_speed,
    CASE
        WHEN $main_engine_is_high_speed            THEN $distillate_fuel
        WHEN e.in_eca                              THEN $distillate_fuel
        WHEN coalesce(l.is_eu_eu, FALSE)           THEN $distillate_fuel
        ELSE $residual_fuel
    END                                            AS fuel_type
FROM vessel_hour AS h
LEFT JOIN eca_hour AS e
       ON e.imo = h.imo AND e.ts = h.ts
LEFT JOIN voyage_leg AS l
       ON l.imo = h.imo
      AND h.ts >= l.depart_ts
      AND h.ts <  l.arrive_ts
ORDER BY h.imo, h.ts;
