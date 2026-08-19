-- §4.5 -- ship-year CO2, with and without the coverage correction.
--
--   E_ship,y = SUM_i E_CO2,i          [t / year]
--
-- The coverage correction divides by `coverage_active`, NOT by raw coverage.
-- Raw coverage counts out-of-service hours as missing observations; dividing by it
-- would multiply vessel A's 2019 by 2.77x for a hull that was laid up for 282 days,
-- fabricating roughly nine months of voyages. `coverage_active` excludes those
-- hours from the denominator, so the correction recovers only genuinely missed
-- reception. See activity.classify_gaps and the note in config/pilot.yaml.
--
-- Both figures are reported either way, as METHODOLOGY §4.5 requires.

SELECT
    e.imo,
    year(e.ts)                                    AS year,
    e.scenario_id,
    e.power_estimate,
    e.smoothing_window,
    e.gap_treatment,

    count(*)                                      AS modelled_hours,
    sum(e.co2_tonnes)                             AS co2_tonnes_observed,
    sum(e.co2_tonnes) / nullif(c.coverage_active, 0)
                                                  AS co2_tonnes_corrected,
    CASE WHEN $apply_coverage_correction
         THEN sum(e.co2_tonnes) / nullif(c.coverage_active, 0)
         ELSE sum(e.co2_tonnes)
    END                                           AS co2_tonnes,

    c.coverage_raw,
    c.coverage_active,
    c.inactive_hours,
    -- Flagged so a low-confidence year is visible wherever the number travels.
    (c.coverage_active < $coverage_warn)          AS is_low_confidence,

    sum(e.co2_tonnes) FILTER (WHERE e.operating_mode = 'at_berth')     AS co2_at_berth,
    sum(e.co2_tonnes) FILTER (WHERE e.operating_mode = 'anchored')     AS co2_anchored,
    sum(e.co2_tonnes) FILTER (WHERE e.operating_mode = 'manoeuvring')  AS co2_manoeuvring,
    sum(e.co2_tonnes) FILTER (WHERE e.operating_mode = 'slow_transit') AS co2_slow_transit,
    sum(e.co2_tonnes) FILTER (WHERE e.operating_mode = 'normal_cruising') AS co2_cruising,
    sum(e.co2_tonnes) FILTER (WHERE e.fuel_type = 'MDO')               AS co2_mdo,
    sum(e.co2_tonnes) FILTER (WHERE e.fuel_type = 'HFO')               AS co2_hfo

FROM emissions_hour AS e
JOIN coverage       AS c ON c.imo = e.imo AND c.year = year(e.ts)
GROUP BY e.imo, year(e.ts), e.scenario_id, e.power_estimate, e.smoothing_window,
         e.gap_treatment,
         c.coverage_raw, c.coverage_active, c.inactive_hours
ORDER BY e.imo, year, e.scenario_id;
