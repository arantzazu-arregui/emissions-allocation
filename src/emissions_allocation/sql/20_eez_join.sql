-- §5.4 -- assign each vessel-hour to an EEZ.
--
-- Point-in-polygon against World EEZ v12: 285 polygons, EPSG:4326. Hours matching
-- no polygon fall to the high seas, which is most of an international voyage.
--
-- ISO_SOV1 is the sovereign state and is NEVER NULL in v12 -- unlike GFW's own
-- layer, where joint regimes carry a null iso3. That is why sovereignty is read
-- from ISO_SOV1 rather than ISO_TER1: the territory field IS null for some
-- polygons, and a null country would silently drop hours from the domestic test.
--
-- POL_TYPE splits 229 '200NM' / 21 'Joint regime' / 35 'Overlapping claim'.
-- METHODOLOGY §5.4 sets the default: assign to ISO_SOV1 and report the affected
-- hours separately, which is what `is_disputed` below is for. The rule is not
-- finally settled -- Selin et al.'s supplementary Table 1 turns out to carry no
-- territory breakdown that would resolve it, only 199 sovereign parties.
--
-- A vessel-hour can fall inside more than one polygon where claims overlap. The
-- aggregation keeps ONE row per hour so the hour cannot be double-counted, and
-- flags the ambiguity rather than resolving it silently.

SELECT
    h.imo,
    h.ts,
    count(z.MRGID) > 0                        AS in_eez,
    -- Deterministic pick among overlapping claims: lowest MRGID. Arbitrary, and
    -- flagged as such by is_disputed -- do not read it as an adjudication.
    min_by(z.ISO_SOV1, z.MRGID)               AS eez_iso3,
    min_by(z.MRGID,    z.MRGID)               AS eez_mrgid,
    min_by(z.GEONAME,  z.MRGID)               AS eez_name,
    count(DISTINCT z.MRGID) > 1
        OR bool_or(z.POL_TYPE <> '200NM')     AS is_disputed,
    list(DISTINCT z.ISO_SOV1)                 AS claimant_iso3
FROM vessel_hour AS h
LEFT JOIN eez_polygons AS z
       ON ST_Intersects(z.geom, ST_Point(h.lon, h.lat))
GROUP BY h.imo, h.ts
ORDER BY h.imo, h.ts;
