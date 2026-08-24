-- §5.4 -- voyage-based international-shipping emissions.
--
-- Fourth IMO GHG Study 2020 Option 2: a voyage is international when its
-- departure and arrival ports are in different countries. The destination port
-- call inherits the preceding voyage label, so each interval runs from departure
-- through the end of its destination call.
--
-- Unlabelled boundary hours are apportioned by the labelled international share
-- of modelled hours in the same vessel-year, following the IMO boundary treatment.

WITH labelled AS (
    SELECT e.*, l.is_international
    FROM emissions_hour AS e
    LEFT JOIN voyage_leg AS l
           ON l.imo = e.imo
          AND e.ts >= l.depart_ts
          AND e.ts < l.label_end_ts
),
annual AS (
    SELECT
        e.imo, year(e.ts) AS year, e.scenario_id, e.power_estimate,
        e.smoothing_window, e.gap_treatment,
        count(*) AS modelled_hours,
        count(*) FILTER (WHERE e.is_international IS NOT NULL) AS labelled_hours,
        count(*) FILTER (WHERE e.is_international) AS international_hours_direct,
        count(*) FILTER (WHERE e.is_international IS NULL) AS unallocated_hours,
        sum(e.co2_tonnes) FILTER (WHERE e.is_international) AS co2_tonnes_direct,
        sum(e.co2_tonnes) FILTER (WHERE e.is_international IS NULL)
            AS co2_tonnes_unallocated
    FROM labelled AS e
    GROUP BY e.imo, year(e.ts), e.scenario_id, e.power_estimate,
             e.smoothing_window, e.gap_treatment
),
split AS (
    SELECT
        a.*,
        a.international_hours_direct / nullif(a.labelled_hours, 0)::DOUBLE
            AS international_hour_share,
        coalesce(a.co2_tonnes_direct, 0)
          + coalesce(a.co2_tonnes_unallocated, 0)
            * coalesce(a.international_hours_direct
                / nullif(a.labelled_hours, 0)::DOUBLE, 0) AS co2_tonnes_observed
    FROM annual AS a
)
SELECT
    s.*,
    s.co2_tonnes_observed / nullif(c.coverage_active, 0) AS co2_tonnes_corrected,
    CASE WHEN $apply_coverage_correction
         THEN s.co2_tonnes_observed / nullif(c.coverage_active, 0)
         ELSE s.co2_tonnes_observed
    END AS co2_tonnes,
    c.coverage_raw, c.coverage_active, c.inactive_hours,
    (c.coverage_active < $coverage_warn) AS is_low_confidence
FROM split AS s
JOIN coverage AS c ON c.imo = s.imo AND c.year = s.year
ORDER BY s.imo, s.year, s.scenario_id;
