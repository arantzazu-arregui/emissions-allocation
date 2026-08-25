-- §5.2 -- distance from each position to the nearest port, in nautical miles.
--
-- The IMO study represents each port as a single point (its §2.7.1). Here the port
-- point set is the distinct anchorage coordinates from this vessel's own port-visit
-- events, which is what METHODOLOGY §5.2 specifies -- "distance to port comes from
-- the port-call anchorage coordinates".
--
-- That is a deliberate narrowing worth stating: it measures distance to a port the
-- vessel ACTUALLY VISITED, not to the nearest port in the world. A ship passing
-- close by a port it never calls at will read as far from port. For the Table 16
-- matrix this is the right behaviour at the <=1 nm threshold -- a vessel within
-- 1 nm of a berth it is not using is manoeuvring past, not berthed -- but it would
-- need a global anchorage set to generalise.
--
-- Both start and end anchorages are used: a visit can begin at one and end at
-- another, and both are places the vessel was.
--
-- Only positions within `$prefilter_degrees` of a port are computed; beyond 5 nm
-- Table 16 stops distinguishing port distance, so the rest come back NULL and the
-- mode matrix reads NULL as "> 5".

WITH port_point AS (
    SELECT DISTINCT lat, lon FROM (
        SELECT lat, lon FROM port_call WHERE lat IS NOT NULL
        UNION
        SELECT end_lat AS lat, end_lon AS lon FROM port_call WHERE end_lat IS NOT NULL
    )
),
nearest AS (
    SELECT
        p.lat,
        p.lon,
        gc_nm(p.lat, p.lon, q.lat, q.lon) AS nm
    FROM distinct_position AS p
    JOIN port_point AS q
      ON abs(p.lat - q.lat) <= $prefilter_degrees
     AND abs(p.lon - q.lon) <= $prefilter_degrees / greatest(cos(radians(p.lat)), 0.01)
)
SELECT lat, lon, min(nm) AS port_nm
FROM nearest
GROUP BY lat, lon;
