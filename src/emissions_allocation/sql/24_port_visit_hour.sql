-- §4 -- mark each vessel-hour that falls inside a GFW port-visit interval.
--
-- Table 16 separates "at berth" from "anchored" by DISTANCE TO PORT alone, because
-- the IMO study had no better signal. METHODOLOGY Section 4 records the departure directly:
-- "GFW does not expose AIS navigational status, so 'at berth' and 'anchored' are
-- separated by distance alone."
--
-- We have a better signal, and it comes from a different endpoint. A GFW port-visit
-- event states, with a confidence score, that the vessel WAS IN PORT between two
-- timestamps. Using it is analogous to Section 5's use of EU-to-EU legs from
-- the actual port sequence rather than from an EEZ proxy.
--
-- Why the distance test was failing: the port point set is built from anchorage
-- coordinates, and a GFW anchorage sits some way off the berth. Vessel A spends
-- 17,427 h inside port visits but only 1,143 h read as within 1 nm of an anchorage
-- point, so ~16,000 h were classified `anchored` (1,800 kW auxiliary) rather than
-- `at_berth` (1,300 kW) -- inflating the total on a hull that spends a quarter of
-- the study period in port.
--
-- `at_dock` is carried separately and NOT used to override the classification. A
-- GFW port visit spans the whole call, including time waiting at anchorage before
-- a berth frees up, and `atDock` is recorded per anchorage rather than per hour --
-- so it cannot cleanly split the interval. It is surfaced so the ambiguity is
-- visible and can be quantified rather than assumed away.

SELECT
    h.imo,
    h.ts,
    count(p.event_id) > 0                      AS in_port_visit,
    -- True when ANY anchorage of the covering visit was at a dock. Informational.
    coalesce(bool_or(p.at_dock), FALSE)        AS visit_at_dock,
    min_by(p.port_id,   p.start_ts)            AS visit_port_id,
    min_by(p.port_iso3, p.start_ts)            AS visit_port_iso3
FROM vessel_hour AS h
LEFT JOIN port_call AS p
       ON p.imo = h.imo
      AND h.ts >= p.start_ts
      AND h.ts <  p.end_ts
GROUP BY h.imo, h.ts;
