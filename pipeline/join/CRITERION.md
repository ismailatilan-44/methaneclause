# Join criterion v1 (frozen 2026-10-14)

MARS × Carbon Mapper "contradiction candidate". Implemented by `pipeline/join/join.py`; its SHA-256 is written
into `data/derived/join_v1.json` (`criterion_sha256`). Any change to this file or to the rules below is a new
version (v2), never an edit of v1. Derived from an exploratory join scan written during idea selection, plus the
fixes an internal security review asked for (twin de-duplication, plume-level matching).

## Inputs
- UNEP IMEO MARS public GeoJSON zip (sources + plumes), hash in `data/MANIFEST.json` (`mars_zip`).
- Carbon Mapper CH4 1B2 sources CSV and `/catalog/search/source/{name}` responses, hashes in the manifest.

## Rules
1. **MARS targets.** MARS sources with `sector == "Oil and Gas"`, `notified == "Yes"` and
   `persistency_category == "absent"`. (The `emission_status` column exists in the dictionary but is empty in the
   public file, so `persistency_category` carries the label.)
2. **As-of date and window.** `as_of` = latest `tile_date` over all MARS plumes in the zip. Window =
   `[as_of − 6 calendar months, as_of]`, both ends inclusive, by UTC date. This is MARS's own "absent" window
   (persistency 0 over the last 6 months).
3. **Source neighbourhood.** A Carbon Mapper source is in scope if its point lies within **1.5 km** (haversine,
   R = 6371.0088 km) of a MARS target. Each Carbon Mapper source is owned by its **nearest** MARS target only, so
   one Carbon Mapper source is never counted for two MARS sources.
4. **Twin dedup (250 m / 1000 m).** Carbon Mapper clusters the same plumes at two scales, and the twins list the
   same `plume_id`s. Plumes from all in-scope sources are pooled and de-duplicated by `plume_id`.
5. **Plume level.** Each de-duplicated plume is assigned to the MARS target nearest to its **origin point**. It
   counts only if (a) the origin is within 1.5 km of that MARS source, (b) its scene date is **after** the MARS
   source's `last_plume_date`, and (c) its scene date lies inside the window. Plumes with a hidden emission rate
   still count as detections (flagged `n_hidden_emission`). All instruments count (Tanager, EMIT, AVIRIS-3, …).
6. **Candidate.** A MARS target with at least one counting plume. `n_candidates` is the headline count; plume,
   detection-day and Carbon Mapper source counts are reported next to it.

## What a candidate is not
A spatial-temporal match, not a confirmed attribution or a MARS error: a different facility can sit within
1.5 km, MARS's "absent" uses its own sensor set and qualified passes, and providers re-process plumes. Say
"~N candidates", never present the count as verified leaks. Non-detection is never evidence of repair.
