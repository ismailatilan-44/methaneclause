# Licence of the data in `data/derived/`

The **code** in this repository is MIT. The **data** in this folder is not: it is derived from two third-party
datasets and carries their terms. Raw provider responses are never committed (data/raw/ is gitignored); only
derived fields and the SHA-256 of each raw response (`data/MANIFEST.json`) are kept.

Our own measurements (`pq_bench.json`, `measurements.json`, `testnet_run.json`, `demo_run.json`,
`verification_time.json`, `sepolia_*`) contain no third-party data and are MIT like the code.

## Carbon Mapper
- Files: `cm_sources.csv`, `cm_timelines.json`, `cm_plumes.csv`, the Carbon Mapper parts of `join_v1.json`,
  `cases/*.json` (including `cases/*.clean_passes.json`), `scenes_v1/*.json`, `backtest.json`, `cleanruns.json`,
  `data_quality.json`, `adversarial.json`, `export/*`, `sourceset_v2.json`, `stake_band.json`, `model/*`,
  `cm_timelines_notified.json` (same fields as `cm_timelines.json`, for the notified sources beyond the join
  neighbourhood; `pipeline/fetch_cm_notified.py`), `cm_timelines_not_mars_notified.json` (same fields, for a seeded
  random sample of oil and gas sources farther than 1.5 km from every MARS-notified source, the projection's
  natural-stop baseline; `pipeline/fetch_cm_not_mars_notified.py`), the Carbon Mapper parts of `projection.json`.
- Source: Carbon Mapper public data portal / API v1 (`api.carbonmapper.org`), fetched keyless.
- Terms: Carbon Mapper Terms of Use, effective / last updated 13 January 2026,
  <https://carbonmapper.org/terms>: **non-commercial use only** (§3.2), **attribution required** (§3.3),
  redistributions **under the same non-commercial terms and attribution requirements** (§3.4); the licence can be
  terminated or suspended by Carbon Mapper (§3.5). These are Carbon Mapper's own terms, not a Creative Commons licence.
  Attribution line: **"Data by Carbon Mapper®"**.
- This repository is a non-commercial research prototype: its contracts run on a local chain and on the Ethereum
  Sepolia testnet with test tokens of no monetary value [SIM]; nothing is sold or paid. A production clause that settles
  real payments on Carbon Mapper data would be commercial use and would need a commercial data agreement with Carbon
  Mapper, held by the observers. That agreement, the other providers the design supports (NASA EMIT open data; MARS as
  corroboration) and the path to production are stated in one place:
  [`docs/DATA.md`](../../docs/DATA.md#data-dependency-and-the-path-to-production).
- Carbon Mapper's API v1 returned no `plume_quality` label (empty in all 9,285 rows of `cm_plumes.csv`), so no quality
  filter is applied; `data_quality.json` counts the flags that are present (hidden rate, uncertainty above the rate,
  quantification version).
- What we keep: source names and coordinates, observation/detection dates, and per-plume derived fields
  (`plume_id, scene_timestamp, instrument, platform, emission_auto, emission_uncertainty_auto, plume_quality,
  emission_version, processing_software, published_at, modified, lat, lon`). No imagery, no signed URLs.
- Scene-catalogue derived fields (`cases/*.clean_passes.json`, from `/catalog/scenes/annotated`,
  `/catalog/search/source/{name}` and `/catalog/plumes/annotated?scene_id=`): scene id and name, `timestamp`,
  `published_at`, `instrument`, `platform`, `cloud_cover_pct`, `cloud_cover_pct_assessed`, `not_cloudy`, `bounds`,
  `published_plume_count`, `sensitivity_mode`, `solar_zenith_angle`, `off_nadir`, `gsd`; per scene, the plume ids and
  rates of the linked sources and of plumes within 1.5 km of the facility; the SHA-256 of each scene record and of each
  response. No imagery, no tiles, no signed URLs (the page loads Carbon Mapper's CH₄ tiles from Carbon Mapper at view
  time; nothing is committed).
- `scenes_v1/*.json` (from `/catalog/search/source/{name}`, `/catalog/scenes/annotated?intersects=` and
  `/catalog/plumes/annotated?bbox=`): per scene, id and name, `timestamp`, `published_at`, `instrument`,
  `cloud_cover_pct`, `cloud_cover_pct_assessed`, `not_cloudy`, `published_plume_count`, the indexes of the plume-origin
  points inside the footprint, the plume ids of the linked sources and of plumes within 1.5 km of the facility, and the
  SHA-256 of the scene record; per facility, the origin points with their first publication time and the content
  SHA-256 of every response. No imagery, no signed URLs.
- Older case files (`cases/*.json`) and `data/MANIFEST.json` carry an earlier licence string; the terms above apply.
- Carbon Mapper re-processes plumes and its source names can change; values here are pinned to the response
  hashes in `data/MANIFEST.json` and in each clean-pass file's `generatedFrom`.

## UNEP IMEO Methane Alert and Response System (MARS)
- Files: `mars_sources.csv`, `mars_plumes_og.csv`, `mars_notified.json` (first notified plume date per source, from the
  plume-level `notified` flag; `pipeline/mars_notified.py`), the MARS parts of `join_v1.json`, `cases/*.json` and
  `projection.json`.
- Source: UNEP International Methane Emissions Observatory, public detected plumes/sources GeoJSON
  (`unepazeconomyadlsstorage.blob.core.windows.net/public/unep_methanedata_detected_plumes_geojson.zip`).
- Terms: **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)**.
  Attribution: "UNEP IMEO MARS, CC BY-NC-SA 4.0".
- Changes made: field subset only (see `pipeline/fetch_mars.py`); plumes restricted to the Oil and Gas sector.

## Published figures quoted in `projection_sources.json`
- Short verbatim quotes and a few table counts from published reports and papers (UNEP, UN News, IEA, Ravikumar et
  al. 2020, Mateo-Garcia et al., Cusworth et al., the California Air Resources Board's 2024 airborne summary report and
  its appendix, Carbon Mapper's 2023 final report to CARB),
  each with its title, URL and read date; cited for the projection's parameters, no dataset redistributed.

## California Air Resources Board (CARB), airborne campaign appendix
- File: `carb_summary.json` — aggregates only, no per-incidence row: the number of oil and gas incidences of 2021
  and 2023, counts by campaign year and outcome and by number of plumes and outcome, the number of rows whose (year,
  earliest plume date, number of plumes) key is unique, and for the Type A outcome histograms (days → number of
  incidences) of the day differences notification → mitigation and earliest plume → mitigation; plus the citation (appendix URL, SHA-256, read date),
  the RFC 8785 hash of the rows they come from and a schema id (`methaneclause.carb_summary/1`). Written by
  `pipeline/fetch_published.py --write-summary`; `pipeline/projection.py` reads only this file.
- Source: CARB, Appendix to the Summary Report of the 2020, 2021, and 2023 Airborne Methane Plume Mapping Studies,
  Tables A1 and A2 (URL, SHA-256 and read date in the file and in `projection_sources.json`, `carb-2024-appendix`).
- Not in the repository: the per-incidence rows (incidence ID, campaign year, earliest plume date, number of plumes,
  outcome; for Type A rows also the notification and mitigation dates). `make fetch-published` (network, ask first;
  needs `pdftotext`) downloads the appendix, checks its SHA-256, extracts the rows into `data/derived/
  carb_incidences.json` (git-ignored, local only) and fails unless they reproduce `carb_summary.json` exactly (rows
  hash and every aggregate); tests that need the rows skip with that instruction when the file is absent.
- Terms: these are factual figures (counts and day differences) derived from CARB's published report and appendix,
  cited with URL, SHA-256 and read date (in the file and in `projection_sources.json`). CARB's site-wide "Privacy
  Policy and Conditions of Use" (<https://ww2.arb.ca.gov/privacy-conditions-use>, read 2026-10-21) says, under "Links
  to External Sites": "we ask you not to lift information from this site for placement on another website". We keep
  only the aggregates the projection needs, link the primary documents, and rebuild the rows from CARB's own PDF on
  demand instead of republishing them.

## Story page context (`data/derived/story/`)
The story page (`app/story.html`, data built by `scripts/build_story_data.py`) adds map context for TKM_S_276. Each
file is re-fetched, keyless, by the script of the same name in `scripts/story/`.
- `cm_scene_footprints_tkm.geojson`, `wind_convention.json`: Carbon Mapper scene footprints over the facility and
  per-plume wind fields with the bearing of each plume mask; Carbon Mapper terms above apply (non-commercial,
  "Data by Carbon Mapper®"). The plume rasters used for the bearing check stay out of the repository.
- `osm_tkm.geojson`: OpenStreetMap pipelines, roads, places and industrial areas within about 6 km of the MARS point.
  © OpenStreetMap contributors, Open Database License 1.0 (https://www.openstreetmap.org/copyright). The OSM features inside
  `app/data/story.json` (`extras.osm`) stay under the same ODbL 1.0 terms.
- `weather_tkm.json`: ERA5 reanalysis at the scene hours, via Open-Meteo; CC BY 4.0, "Weather data by Open-Meteo.com"
  (ERA5: Copernicus Climate Change Service; "Contains modified Copernicus Climate Change Service information
  2026"). A grid cell of about 25 km, not a measurement at the facility.
- `orbits_tkm.json`: an estimate of where Tanager-1 was during each pass, from the scene's view angle and current
  orbital elements (CelesTrak GP data, NORAD 60507); the satellite's position on the page is drawn, not measured.

## What this data is not
A join candidate is a spatial-temporal match between two public datasets, not a confirmed attribution and not a
finding that MARS is wrong. Non-detection is never evidence of repair. Downstream use of this folder must keep
each provider's terms above: Carbon Mapper's non-commercial and attribution terms, with redistribution under the
same terms, and MARS's CC BY-NC-SA 4.0.
