-- §5.3 -- allocate ship-year CO2 to countries under each rule.
--
--   E_c,option = SUM over ships of ( E_ship * 1[ key_option(ship) = c ] )
--
-- At n = 2 this reduces to assigning each vessel's total to one country per option,
-- but it is written as the general fleet aggregation so the fleet case needs no
-- change -- scaling is a longer `vessel_key` table, not an edit to this file.
--
-- `vessel_key` carries one row per (imo, option) with the country the rule selects,
-- built from config/vessel_specs.yaml allocation_keys. Options whose country is
-- NULL are excluded rather than aggregated into a null bucket: the bunker-fuel
-- option is not computable at this scale, and silently summing it to NULL would
-- read as a country with emissions.
--
-- Output is keyed by scenario as well as option, so the power/speed estimate and
-- the smoothing window travel with the number rather than being averaged away.

SELECT
    k.option,
    k.country,
    k.gcb_name,
    e.year,
    e.scenario_id,
    e.power_estimate,
    e.smoothing_window,
    sum(e.co2_tonnes)                    AS co2_tonnes,
    sum(e.co2_tonnes) / 1e6              AS co2_mt,
    count(DISTINCT e.imo)                AS n_vessels,
    -- Kept visible so a reader can see how much of the result rests on one hull.
    list(DISTINCT e.imo)                 AS imos
FROM emissions_year AS e
JOIN vessel_key     AS k ON k.imo = e.imo
WHERE k.country IS NOT NULL
GROUP BY k.option, k.country, k.gcb_name, e.year,
         e.scenario_id, e.power_estimate, e.smoothing_window
ORDER BY k.option, e.year, co2_tonnes DESC;
