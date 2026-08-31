-- §1.3 -- observed coverage, per vessel per year.
--
-- Two coverage figures, and they mean different things:
--
--   coverage_raw     observed / elapsed.  Transparency only.
--   coverage_active  observed / (elapsed - inactive).  This is the Section 5 divisor.
--
-- Hours where the hull was out of service are removed from the denominator rather
-- than scaled up, so a lay-up cannot fabricate voyages. For vessel A's 2019 the two
-- differ sharply -- 36.1% raw against 82.0% active -- the latter matching 2017's
-- reception quality once the 282-day absence is set aside. Dividing emissions by the
-- raw figure would multiply that year by 2.77x for a ship that was not sailing.
--
-- `is_inactive` is set by activity.classify_gaps, which distinguishes a contiguous
-- absence (no presence AND no port calls, from two independent endpoints) from
-- scattered reception gaps.
--
-- Computed here rather than taken from the GFW Insights endpoint, which cannot reach
-- before 2020-01-01 and counts "blocks" rather than hours. Where the two overlap they
-- agreed to within 0.08%.

SELECT
    any_value(imo)                                          AS imo,
    year(ts)                                                AS year,
    count(*)                                                AS elapsed_hours,
    count(*) FILTER (WHERE is_inactive)                     AS inactive_hours,
    count(*) - count(*) FILTER (WHERE is_inactive)          AS active_hours,
    count(*) FILTER (WHERE NOT is_interpolated)             AS observed_hours,
    (count(*) - count(*) FILTER (WHERE is_inactive))
        - count(*) FILTER (WHERE NOT is_interpolated)       AS interpolated_hours,

    count(*) FILTER (WHERE NOT is_interpolated)
        / CAST(count(*) AS DOUBLE)                          AS coverage_raw,

    count(*) FILTER (WHERE NOT is_interpolated)
        / nullif(
            CAST(count(*) - count(*) FILTER (WHERE is_inactive) AS DOUBLE), 0
          )                                                 AS coverage_active

FROM vessel_hour
GROUP BY year(ts)
ORDER BY year;
