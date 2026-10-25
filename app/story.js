/* MethaneClause story page: the 9-chapter story on real data. Data: data/story.json, built by
   scripts/build_story_data.py from committed files. Every number below comes from that file. */
"use strict";
const noSim = (s) => String(s).replace(/\s*\[SIM[^\]]*\]/g, "");   // purple marks what is simulated; no bracket labels on the page
fetch("data/story.json").then((r) => { if (!r.ok) throw new Error("data/story.json " + r.status); return r.json(); })
  .then((d) => { window.SCENE = d.scene; window.EXTRAS = d.extras; storyMain(); })
  .catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="err" style="position:fixed;top:60px;left:12px;z-index:99">The story data could not load: ${String(e.message).replace(/[<>&]/g, "")}</div>`); });
function storyMain() {
const S = window.SCENE;
const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("en-US");
const ETH = (wei) => Number(BigInt(wei)) / 1e18;
const D = (s) => Date.parse(s.length === 10 ? s + "T12:00:00Z" : s);
const dfmt = (t, withTime) => new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) +
  (withTime ? " " + new Date(t).toISOString().slice(11, 16) + " UTC" : "");
const FAC = [S.facility.lon, S.facility.lat];
const RM = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- facts derived from the committed data ---------- */
const plumes = S.cmPlumes.map(p => ({ ...p, t: D(p.date) }));
const lead = plumes.find(p => p.id === S.episode.plumeId);
const firstCM = plumes[0];
const steps = Object.fromEntries(S.sepolia.steps.map(s => [s.step, s]));
const bondEth = ETH(steps.createCovenant.bondWei);
const rebateEth = ETH(steps.proposeEpisode.rebateWei);
const proposerEth = ETH(steps.withdraw_proposer.amountWei);
const rejects = S.sepolia.steps.filter(s => s.status === 0);
const marsPl = S.mars.plumes.map(p => ({ ...p, t: D(p.date) }));
const tx = (s) => s.tx.split("/tx/")[1];
const X_ = window.EXTRAS;
const PASS = X_.passes.map(p => ({ ...p, t: Date.parse(`${p.date}T${p.time}:00Z`) }));     // every scene that looked at the facility
const NS = S.nearSource;                                                                  // 0.19 km from the facility
const OBS = NS.obs.map(([date, inst]) => ({ date, inst, t: Date.parse(`${date}T12:00:00Z`), det: NS.det.includes(date), scene: PASS.find(p => p.date === date) }));
const WX = Object.fromEntries(X_.weather.map(w => [w.t.slice(0, 10), w]));               // ERA5 at the plume scene times
const compass = (deg) => ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][Math.round(deg / 22.5) % 16];
$("live").href = "https://sepolia.etherscan.io/address/" + S.sepolia.covenant;

/* ---------- the story: 9 chapters. Every date and number comes from data/story.json ----------
   On chain: created = covenant creation (testnet_run.json), propose = deadline - challenge window,
   finalize / withdraw = block timestamps (data/derived/sepolia_txs.json, via scripts/build_story_data.py). */
const WIN_H = S.policy.challengeWindow / 3600;                         // the appeal window, from the clause policy
const created = S.sepolia.createdAt * 1000;
const deadline = steps.proposeEpisode.deadline * 1000;
const proposeAt = deadline - S.policy.challengeWindow * 1000;
const finT = steps.finalize.blockTime * 1000, payT = steps.withdraw_buyer.blockTime * 1000;
const pubT = D(S.episode.publishedAt);
const startT = S.policy.start * 1000;
const backdated = created > startT;                                     // the Sepolia covenant was created after its term start
const marsLast = marsPl.at(-1);
const days = (arr) => new Set(arr.map(p => p.date)).size;
const seen2025 = plumes.filter(p => p.t > marsLast.t && p.t < Date.UTC(2026, 0, 1));
const pl = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const W = (t, time) => `<span class="when">${dfmt(t, time)}</span>`;
const sim = (w) => `<span class="simw">${w}</span>`;
const simMark = (s) => String(s).replace(/(^|[,;:(]\s*)([^,;:()[]+?)\s*\[SIM[^\]]*\]/g, (m, pre, w) => pre + sim(w.trim()));   // "EU importer [SIM]" -> purple "EU importer"
const pinned = `<span class="chip simc" title="simulated">pinned clock</span>`;
const ORD = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"];
/* chapter 9: the refund side of the clause, on DZA_S_012 (origin/main ledger + Anvil replay + Sepolia cure arc) */
const Z9 = S.dza, Z9C = Z9.clean;
const Z9_R0 = Z9C.since * 1000, Z9_R1 = Math.max(...Z9C.scenes.map(x => x.t)) * 1000 + 20 * 864e5;
const z9Scenes = Z9C.scenes.map(x => ({ ...x, t: x.t * 1000 }));
const z9Relapse = Date.parse(Z9.relapse.sceneAt), z9Leak = Date.parse(Z9.leak.sceneAt);
const z9Out = Z9.replayA.outcome, z9Sp = Z9.replayA.sponsor;
const z9Pct = (bp) => `${bp / 100} %`;
const CH = [
  { id: "sight", short: "First sighting", tiny: "Sighting", from: Date.UTC(2023, 5, 1), to: Date.UTC(2025, 5, 1), at: firstCM.t, view: "incident", w: 1.25, act: ["fac", "sat", "cm", "mars"],
    head: "A satellite sees methane.",
    body: `${W(firstCM.t)} — EMIT, on the space station, sees a plume at a gas pipeline in Turkmenistan; Carbon Mapper estimates about ${fmt.format(firstCM.rate)} kg/h.` },
  { id: "absent", short: "Marked absent", tiny: "Absent", from: Date.UTC(2025, 5, 1), to: Date.UTC(2025, 6, 1), at: marsLast.t, view: "incident", w: 0.85, act: ["mars"],
    head: "The registry marks it “absent”.",
    body: `${W(marsLast.t)} — UNEP's last plume here, ${fmt.format(marsLast.rate)} kg/h. UNEP notifies the government; the operator does not reply. Its record today labels the source “${S.mars.persistency}”.` },
  { id: "seen", short: "Still seen", tiny: "Seen", from: Date.UTC(2025, 6, 1), to: Date.UTC(2026, 0, 1), at: seen2025.at(-1).t, view: "incident", w: 0.95, act: ["cm", "sat", "fac"],
    head: "Satellites keep seeing it.",
    body: `Carbon Mapper records plumes within ${S.facility.radiusKm} km on ${days(seen2025)} more days in 2025. The registry still says “${S.mars.persistency}”.` },
  { id: "clause", short: "The clause", tiny: "Clause", from: Date.UTC(2026, 0, 1), to: Date.UTC(2026, 5, 1), at: startT, view: "contract", w: 0.9, act: ["seller", "buyer"], sim: true,
    head: "A buyer writes methane into the deal.",
    body: `${W(startT)} — an ${sim("EU importer")} and the ${sim("seller")} sign a methane clause and the seller locks a ${bondEth} ETH bond: if independent witnesses confirm a leak, the bond pays the buyer a rebate.${backdated ? ` On Sepolia the clause was created on ${W(created)} with its ${sim("start backdated")} to this day, so the sightings below fall inside its term.` : ""}` },
  { id: "caught", short: "Caught again", tiny: "Caught", from: Date.UTC(2026, 5, 1), to: created - 60e3, at: lead.t, view: "satellites", w: 1.15, act: ["sat", "cm", "fac"],
    head: `Caught again: ${fmt.format(lead.rate)} kg/h.`,
    body: `${W(lead.t)} — Tanager-1 sees ${fmt.format(lead.rate)} ± ${fmt.format(lead.unc)} kg/h, ${Math.round(lead.distKm * 1000)} m from the MARS source point: its ${ORD[days(plumes.filter(p => p.t >= startT && p.t <= lead.t)) - 1]} plume day since the clause began. Carbon Mapper publishes it on ${dfmt(pubT)}; this run settles that one.` },
  { id: "proof", short: "Proof on chain", tiny: "Proof", from: created - 60e3, to: proposeAt + 60e3, at: proposeAt, view: "contract", w: 0.95, act: ["obs", "contract"],
    head: "Witnesses sign. The clause checks.",
    body: `${W(proposeAt, true)} — ${S.policy.k} of ${S.policy.n} ${sim("witnesses")} sign the record and the clause records a proposed leak. We also sent ${rejects.length} bad proposals: the contract refused each one.`, tamper: true },
  { id: "window", short: `${WIN_H} h to appeal`, tiny: `${WIN_H} h`, from: proposeAt + 60e3, to: deadline + 60e3, at: deadline, view: "contract", w: 0.85, act: ["contract"],
    head: `Anyone can appeal for ${WIN_H} hours.`,
    body: `Until ${W(deadline, true)}, anyone may appeal with a deposit and an objective reason; the ${sim("seller")} may contest, and then the rebate needs ${S.policy.k + 1} of ${S.policy.n} witnesses; if neither resolves in time, the held rebate stays in the bond and no one is slashed. Neither was filed.` },
  { id: "paid", short: "Paid", tiny: "Paid", from: deadline + 60e3, to: payT + 10 * 60e3, at: payT, view: "contract", w: 0.8, act: ["buyer", "seller", "contract"],
    head: "The contract pays the buyer.",
    body: `${W(finT, true)} — the confirmed leak is final and the ${sim("buyer")} withdraws ${rebateEth} test ETH from the ${sim("seller")}'s bond. No bank, registry or court had to approve it.` },
  { id: "refund", short: "Earn it back · DZA", tiny: "Refund", from: payT + 10 * 60e3, to: payT + 10 * 60e3 + 864e5, at: payT + 10 * 60e3 + 864e5 - 60e3,
    view: "incident", w: 1.15, act: ["seller", "obs", "sat", "contract"], dza: true,
    head: "Earn it back: clean passes pay the seller back.",
    body: `Same clause, another site: ${Z9.source}, ${Z9.country}. Passes with no plume and no cloud flag count. After ${z9Out.counted}, the ${sim("seller")} had earned ${z9Pct(z9Out.paidBp)} of the rebate back; a ${fmt.format(Z9.relapse.rateKgH)} kg/h plume on ${dfmt(z9Relapse)} forfeited the rest.` },
];
/* timeline geometry: one band per chapter, widths by weight, a small gap between bands */
const GAP = 0.006, SUMW = CH.reduce((a, c) => a + c.w, 0), USABLE = 1 - GAP * (CH.length - 1);
let acc = 0;
for (const c of CH) { c.x0 = acc; c.x1 = acc + USABLE * c.w / SUMW; acc = c.x1 + GAP; }
const T0 = CH[0].from, T2 = CH.at(-1).to;
const inZ9 = (t) => t >= CH.at(-1).from;
const z9On = () => inZ9(T) && view !== "verify";   // the DZA replay in the timeline label, charts and money; Verify (the end card) is the TKM case
const z9Real = (t) => Z9_R0 + Math.max(0, Math.min(1, (t - CH.at(-1).from) / (CH.at(-1).at - CH.at(-1).from))) * (Z9_R1 - Z9_R0);
const chOf = (t) => Math.max(0, CH.findIndex(c => t < c.to) === -1 ? CH.length - 1 : CH.findIndex(c => t < c.to));
const xOf = (t) => { const c = CH[chOf(t)]; const f = Math.max(0, Math.min(1, (t - c.from) / (c.to - c.from))); return c.x0 + f * (c.x1 - c.x0); };
const tOf = (x) => { let c = CH.find(c => x <= c.x1) || CH.at(-1); if (x < c.x0) x = c.x0; return c.from + ((x - c.x0) / (c.x1 - c.x0)) * (c.to - c.from); };
// moments used by the players and the contract view
const EVT = { first: firstCM.t, marslast: marsLast.t, start: startT, lead: lead.t, pub: pubT, deploy: created, rejects: created, propose: proposeAt, final: finT, paid: payT };
const KCOL = { methane: "var(--methane)", registry: "var(--registry)", sim: "var(--sim)", data: "var(--ink)", chain: "var(--chain)", bad: "var(--bad)", money: "var(--money)" };
let T = CH[0].at;
let view = "incident";
/* prologue: the problem in one screen, before chapter 1 (failure first). T sits at the end of "Still seen". */
const PRO = { T: CH[2].to - 864e5, view: "incident",        // the last day of "Still seen"
  head: "Registry: “absent”. Satellites: still seeing it.",
  body: `UNEP's record labels this pipeline's methane source “${S.mars.persistency}”. Every time Carbon Mapper looked again, it saw a plume. Nothing in the gas contract responds to this.` };
let prologue = false;

/* ---------- players: who they are, what they do now ---------- */
const ICON = {
  pipe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9h7v6H3zM14 9h7v6h-7zM10 12h4"/></svg>',
  registry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20V8l8-4 8 4v12M8 20v-7M12 20v-7M16 20v-7M3 20h18"/></svg>',
  data: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/></svg>',
  sat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M3 4l5 5M16 15l5 5M2 9l4-4 4 4-4 4zM14 19l4-4 4 4-4 4z"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  seller: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 21V10l6-4v4l6-4v4l6-4v15zM7 17h2M12 17h2M17 17h1"/></svg>',
  buyer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 10l9-6 9 6M5 10v9h14v-9M10 19v-5h4v5"/></svg>',
  contract: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5"/></svg>',
};
const after = (id) => T >= EVT[id];
const seenBy = (inst) => plumes.filter(p => p.inst === inst && p.t <= T);
const PLAYERS = () => [
  { id: "fac", icon: "pipe", name: "Facility · TKM_S_276", role: "transmission pipeline",
    st: after("lead") ? `Leaking ${fmt.format(lead.rate)} kg/h on ${dfmt(lead.t)}` : plumes.some(p => p.t <= T) ? `Methane seen on ${pl(days(plumes.filter(p => p.t <= T)), "day")}` : "No public plume yet",
    light: plumes.some(p => p.t <= T) ? "methane" : "" },
  { id: "mars", icon: "registry", name: "UNEP IMEO MARS", role: "registry · notifies governments",
    st: after("marslast") ? `Notified the government · labelled “${S.mars.persistency}”` : after("first") ? "Plume detected" : "Watching",
    light: after("first") ? "registry" : "" },
  { id: "cm", icon: "data", name: "Carbon Mapper", role: "publishes plume records",
    st: `${pl(plumes.filter(p => D(p.date) <= T).length, "plume")} at this facility`, light: plumes.some(p => p.t <= T) ? "methane" : "" },
  { id: "sat", icon: "sat", name: "Satellites · EMIT, Tanager-1", role: "see the methane from orbit",
    st: `EMIT ${pl(days(seenBy("emi")), "plume day")} · Tanager-1 ${days(seenBy("tan"))}`, light: plumes.some(p => p.t <= T) ? "methane" : "" },
  { id: "obs", icon: "eye", name: `Witnesses × ${S.policy.n}`, role: "independent organisations; sign the record", sim: true,
    st: after("propose") ? `${S.policy.k} of ${S.policy.n} signed the record` : after("deploy") ? "Checking the public record" : "Not yet engaged", light: after("propose") ? "chain" : "" },
  { id: "seller", icon: "seller", name: "Seller", role: "gas exporter", sim: true,
    st: after("paid") ? `Bond paid ${rebateEth} ETH rebate` : after("start") ? `Bond ${bondEth} ETH locked` : "—", light: after("start") ? "money" : "" },
  { id: "buyer", icon: "buyer", name: "Buyer", role: "EU gas importer", sim: true,
    st: after("paid") ? `Received ${rebateEth} ETH rebate` : after("start") ? "Signed the clause" : "—", light: after("paid") ? "money" : "" },
  { id: "contract", icon: "contract", name: "The clause", role: "MethaneCovenant on Ethereum Sepolia",
    st: after("paid") ? "Paid out" : after("final") ? "Leak confirmed" : after("propose") ? `Leak proposed · ${WIN_H} h appeal window` : after("rejects") ? `Rejected ${rejects.length} bad proposals` : after("deploy") ? "Deployed · holding the bond" : after("start") ? `Holding the bond${backdated ? ` · on chain ${dfmt(created)}, ${sim("start backdated")}` : ""}` : "Not deployed yet",
    light: after("start") ? "chain" : "" },
];
let lastSt = {};

/* ---------- map ---------- */
const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      img: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, maxzoom: 17,
        attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
      dem: { type: "raster-dem", tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"], tileSize: 256, encoding: "terrarium", maxzoom: 13,
        attribution: "Elevation: Mapzen Terrain Tiles (AWS Open Data)" },
      hsdem: { type: "raster-dem", tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"], tileSize: 256, encoding: "terrarium", maxzoom: 13 },
      light: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, maxzoom: 16,
        attribution: "Light gray canvas © Esri, HERE, Garmin, OpenStreetMap contributors" },
    },
    layers: [
      { id: "light", type: "raster", source: "light", layout: { visibility: "none" } },
      { id: "img", type: "raster", source: "img", paint: { "raster-saturation": 0.08, "raster-contrast": 0.14, "raster-brightness-min": 0.03, "raster-brightness-max": 0.98 } },
      // relief shading on top of the photo: the flat desert reads as terrain, like FireTrace's stylised hills
      { id: "hs", type: "hillshade", source: "hsdem", paint: { "hillshade-exaggeration": 0.6, "hillshade-shadow-color": "#2e2216", "hillshade-highlight-color": "#fff8ec", "hillshade-accent-color": "#4a3a28", "hillshade-illumination-direction": 315 } },
    ],
    terrain: { source: "dem", exaggeration: 2.4 },
    sky: { "sky-color": "#a9c4de", "horizon-color": "#efe5d5", "fog-color": "#efe5d5", "sky-horizon-blend": 0.7, "horizon-fog-blend": 0.55, "fog-ground-blend": 0.25, "atmosphere-blend": 0 },
  },
  center: [53.716, 39.4875], zoom: 13.45, pitch: 60, bearing: -28, maxPitch: 82,
  attributionControl: { compact: false, customAttribution: "Data by Carbon Mapper® · UNEP IMEO MARS, CC BY-NC-SA 4.0 · © OpenStreetMap contributors (ODbL) · Weather data by Open-Meteo.com (ERA5) · Contains modified Copernicus Climate Change Service information 2026" },
});

const CAMS = {
  incident: { center: [53.716, 39.4875], zoom: 13.45, pitch: 60, bearing: -28 },
  satellites: { center: [53.718, 39.49], zoom: 13.1, pitch: 72, bearing: -28 },
  contract: { center: [53.72, 39.484], zoom: 11.8, pitch: 40, bearing: 0 },
  verify: { center: [53.72005, 39.48457], zoom: 13.1, pitch: 0, bearing: 0 },
};

function circle(lon, lat, km, n = 72) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI;
    out.push([lon + (km / (111.32 * Math.cos(lat * Math.PI / 180))) * Math.cos(a), lat + (km / 110.57) * Math.sin(a)]);
  }
  return out;
}
const along = (lon, lat, brgDeg, km) => {
  const b = brgDeg * Math.PI / 180;
  return [lon + (km * Math.sin(b)) / (111.32 * Math.cos(lat * Math.PI / 180)), lat + (km * Math.cos(b)) / 110.57];
};

/* CH4 scene tiles from Carbon Mapper, loaded at view time (nothing committed).
   They are plain <img> elements: Carbon Mapper sends CORS headers only to localhost and its own sites, so a hosted
   page cannot read the pixels (WebGL, canvas). Displaying an <img> needs no CORS: colour comes from a CSS filter,
   the dark background drops out with mix-blend-mode: screen, and the 3x3 mosaic is pinned to the map with a
   matrix3d homography through the four projected corners. */
const tile2lon = (x, z) => (x / 2 ** z) * 360 - 180;
const tile2lat = (y, z) => { const n = Math.PI - (2 * Math.PI * y) / 2 ** z; return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))); };
const lon2tile = (lon, z) => Math.floor(((lon + 180) / 360) * 2 ** z);
const lat2tile = (lat, z) => Math.floor(((1 - Math.log(Math.tan(lat * Math.PI / 180) + 1 / Math.cos(lat * Math.PI / 180)) / Math.PI) / 2) * 2 ** z);
const Z = 12, CX = lon2tile(FAC[0], Z), CY = lat2tile(FAC[1], Z);
const MOS = [[tile2lon(CX - 1, Z), tile2lat(CY - 1, Z)], [tile2lon(CX + 2, Z), tile2lat(CY - 1, Z)], [tile2lon(CX + 2, Z), tile2lat(CY + 2, Z)], [tile2lon(CX - 1, Z), tile2lat(CY + 2, Z)]];
const tileUrl = (pid, kind, dx, dy) => `https://api.carbonmapper.org/api/v1/layers/scene/${pid}/${kind}/${Z}/${CX + dx}/${CY + dy}.png`;
function mosaic(pid, kind) {
  let h = "";
  for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++)
    h += `<img src="${tileUrl(pid, kind, dx, dy)}" alt="" style="left:${(dx + 1) * 256}px;top:${(dy + 1) * 256}px">`;
  return h;
}
const yM = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
const mosXY = (lon, lat) => [((lon - MOS[0][0]) / (MOS[1][0] - MOS[0][0])) * 768, ((yM(MOS[0][1]) - yM(lat)) / (yM(MOS[0][1]) - yM(MOS[3][1]))) * 768];

function homography(src, dst) {           // 4 point pairs -> CSS matrix3d
  const A = [], b = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i], [u, v] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  for (let c = 0; c < 8; c++) {           // Gaussian elimination with partial pivoting
    let p = c; for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]]; [b[c], b[p]] = [b[p], b[c]];
    for (let r = 0; r < 8; r++) if (r !== c) { const f = A[r][c] / A[c][c]; for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k]; b[r] -= f * b[c]; }
  }
  const h = b.map((v, i) => v / A[i][i]);
  return `matrix3d(${h[0]},${h[3]},0,${h[6]},${h[1]},${h[4]},0,${h[7]},0,0,1,0,${h[2]},${h[5]},0,1)`;
}
let ch4El = null;
function placeCH4() {
  if (!ch4El) return;
  const on = T >= lead.t && (view === "incident" || view === "satellites");
  ch4El.style.opacity = on ? 1 : 0;
  if (!on) return;
  const dst = MOS.map(c => { const p = map.project(c); return [p.x, p.y]; });
  ch4El.style.transform = homography([[0, 0], [768, 0], [768, 768], [0, 768]], dst);
}

const leadScene = S.scenes.find(s => s.date === lead.date);
let markers = [];
map.on("load", () => {
  map.addSource("fac", { type: "geojson", data: { type: "Feature", geometry: { type: "Polygon", coordinates: [circle(...FAC, S.facility.radiusKm)] } } });
  map.addLayer({ id: "fac-fill", type: "fill", source: "fac", paint: { "fill-color": "#ffffff", "fill-opacity": 0.06 } });
  map.addLayer({ id: "fac-line", type: "line", source: "fac", paint: { "line-color": "#ffffff", "line-width": 1.6, "line-dasharray": [3, 2] } });
  map.addSource("pl", { type: "geojson", data: { type: "FeatureCollection", features: plumes.map(p => ({ type: "Feature", properties: { t: p.t, rate: p.rate || 300, lead: p.id === lead.id ? 1 : 0 }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } })) } });
  map.addLayer({ id: "pl-halo", type: "circle", source: "pl", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "rate"], 100, 9, 3500, 22], "circle-color": "#e2590f", "circle-opacity": 0.18, "circle-blur": 0.6 } });
  map.addLayer({ id: "pl", type: "circle", source: "pl", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "rate"], 100, 3.5, 3500, 8], "circle-color": ["case", ["==", ["get", "lead"], 1], "#ffd27a", "#e2590f"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.4 } });

  map.addSource("osm", { type: "geojson", data: X_.osm });
  map.addLayer({ id: "osm-road", type: "line", source: "osm", filter: ["==", ["get", "@class"], "road"], paint: { "line-color": "#f6efe2", "line-opacity": 0.75, "line-width": ["case", ["==", ["get", "highway"], "primary"], 2.4, 1.2] } }, "fac-fill");
  map.addLayer({ id: "osm-pipe", type: "line", source: "osm", filter: ["==", ["get", "@class"], "pipeline"], paint: { "line-color": "#ffd76a", "line-width": 2.2, "line-dasharray": [2, 1.6] } }, "fac-fill");
  map.addSource("fp", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "fp-line", type: "line", source: "fp", paint: { "line-color": "#ffffff", "line-width": 1.6, "line-opacity": 0.9 } });
  for (const f of X_.osm.features) {        // a few real names for orientation
    const pr = f.properties, isGas = pr["@class"] === "pipeline" && pr.substance === "gas" && pr.network === "transmission";
    const inRing = pr["@class"] === "pipeline" && pr["@dist_km"] < S.facility.radiusKm;
    const lab = isGas ? "gas transmission pipeline" : inRing ? "underground pipeline" : pr.ref === "P-17" ? "P-17 road" : pr.place ? pr.name : (pr["@class"] === "industrial" && pr.name) ? pr.name : null;
    if (!lab) continue;
    const g = f.geometry, c = g.type === "Point" ? g.coordinates : g.type === "Polygon" ? g.coordinates[0][0] : g.coordinates[Math.floor(g.coordinates.length / 2)];
    const el = document.createElement("div"); el.className = "osmlbl"; el.textContent = lab;
    markers.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [6, 0] }).setLngLat(c).addTo(map));
  }
  map.addSource("z9", { type: "geojson", data: { type: "FeatureCollection", features: [
    { type: "Feature", properties: { k: "ring" }, geometry: { type: "Polygon", coordinates: [circle(Z9.mars.facility[1], Z9.mars.facility[0], S.facility.radiusKm)] } },
    ...Z9.plumes.map(q => ({ type: "Feature", properties: { k: "plume", rate: q.rate || 300 }, geometry: { type: "Point", coordinates: [q.lon, q.lat] } })),
    { type: "Feature", properties: { k: "relapse", rate: Z9.relapse.rateKgH }, geometry: { type: "Point", coordinates: [Z9.relapse.lon, Z9.relapse.lat] } } ] } });
  map.addLayer({ id: "z9-ring", type: "line", source: "z9", filter: ["==", ["get", "k"], "ring"], layout: { visibility: "none" }, paint: { "line-color": "#ffffff", "line-width": 1.6, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "z9-pl", type: "circle", source: "z9", filter: ["!=", ["get", "k"], "ring"], layout: { visibility: "none" },
    paint: { "circle-radius": ["case", ["==", ["get", "k"], "relapse"], 8, 4.5], "circle-color": ["case", ["==", ["get", "k"], "relapse"], "#ffd27a", "#e2590f"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.4 } });
  const z9El = document.createElement("div"); z9El.className = "z9lbl";
  z9El.innerHTML = `<div class="lbl" style="transform:none"><b>${Z9.source}</b> · ${Z9.sourceType.toLowerCase()}<br><span class="s">${Z9.country} · MARS “${Z9.mars.category}” · ring ${S.facility.radiusKm} km</span></div>`;
  markers.push(new maplibregl.Marker({ element: z9El, anchor: "right", offset: [-14, 0] }).setLngLat([Z9.mars.facility[1], Z9.mars.facility[0]]).addTo(map));
  ch4El = document.createElement("div"); ch4El.className = "ch4ov"; ch4El.innerHTML = mosaic(leadScene.providerId, "ch4");
  map.getCanvasContainer().appendChild(ch4El);

  const pinEl = document.createElement("div"); pinEl.className = "pin";
  markers.push(new maplibregl.Marker({ element: pinEl, anchor: "center" }).setLngLat(FAC).addTo(map));
  const facEl = document.createElement("div");
  facEl.className = "faclbl"; facEl.innerHTML = `<div class="lbl" style="transform:none"><b>TKM_S_276</b> · gas pipeline<br><span class="s">MARS source · ${S.facility.radiusKm} km ring</span></div>`;
  markers.push(new maplibregl.Marker({ element: facEl, anchor: "right", offset: [-14, 0] }).setLngLat(FAC).addTo(map));
  const leadEl = document.createElement("div"); leadEl.id = "leadlbl";
  leadEl.innerHTML = `<div class="lbl methane"><b>${fmt.format(lead.rate)} kg/h</b><br><span class="s" style="color:#ffe6d2">${dfmt(lead.t)} · Tanager-1</span></div>`;
  markers.push(new maplibregl.Marker({ element: leadEl, anchor: "bottom-right", offset: [-8, -8] }).setLngLat([lead.lon, lead.lat]).addTo(map));
  update();
});
let mapReady = false;
let mapLoading = document.createElement("div");
mapLoading.className = "maploading"; mapLoading.setAttribute("role", "status"); mapLoading.textContent = "Loading satellite map…";
$("mapwrap").appendChild(mapLoading);
const loaded = () => { mapLoading?.remove(); mapLoading = null; };
map.once("load", loaded);
map.once("idle", () => { mapReady = true; loaded(); drawSky(); });
let skyBoxes = [];
function fitMapLabels() {
  const c = document.querySelector(".maplibregl-ctrl-bottom-right")?.getBoundingClientRect(), m = map.getContainer().getBoundingClientRect();
  if (!c) return;
  const hit = (r, b) => r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top;
  // an orientation label under the credit line or under a sky label hides instead of mixing with it (a class: the map library owns the marker's inline opacity)
  for (const el of document.querySelectorAll(".osmlbl")) { const r = el.getBoundingClientRect(); el.classList.toggle("undercredit", (r.bottom > c.top - 2 && r.right > c.left) || skyBoxes.some(b => hit(r, b))); }
  // a site label near the map's edge slides back inside it
  for (const el of document.querySelectorAll(".faclbl, .z9lbl")) {
    const lbl = el.firstElementChild; if (!lbl || !el.offsetWidth) continue;
    lbl.style.transform = "none";
    const r = lbl.getBoundingClientRect(), dx = Math.max(0, m.left + 6 - r.left) - Math.max(0, r.right - (m.right - 6));
    if (dx) lbl.style.transform = `translateX(${Math.round(dx)}px)`;
  }
}
map.on("move", fitMapLabels); map.on("idle", fitMapLabels);
map.on("render", () => { try { placeCH4(); drawSky(); } catch (e) { /* terrain tiles not ready yet */ } });

/* ---------- satellites in the sky ----------
   Pass dates, scenes and images are real. Where a satellite is drawn is not: it is placed just above the scene so
   you can see who is looking, so its path is purple (simulated). */
const SATS = [
  { id: "tan", name: "Tanager-1", org: "Planet · Carbon Mapper coalition", inst: "tan", lane: 0.24, dir: 1 },
  { id: "emi", name: "EMIT on the ISS", org: "NASA / JPL imaging spectrometer", inst: "emi", lane: 0.17, dir: -1 },
];
let selSat = "tan";
const satGlyph = (x, y, s, active) => `
  <g transform="translate(${x},${y}) scale(${s})">
    <rect x="-27" y="-6" width="18" height="12" rx="1.5" fill="#2b4b8a" stroke="#fff" stroke-width="1.2"/>
    <rect x="9" y="-6" width="18" height="12" rx="1.5" fill="#2b4b8a" stroke="#fff" stroke-width="1.2"/>
    <path d="M-21 -6v12M-15 -6v12M15 -6v12M21 -6v12" stroke="#8fb0ea" stroke-width=".8"/>
    <rect x="-8" y="-8" width="16" height="16" rx="3" fill="#f4efe6" stroke="#1d1b18" stroke-width="1.4"/>
    <circle cx="0" cy="8" r="3.2" fill="${active ? "#f59e0b" : "#1d1b18"}"/>
  </g>`;
/* each pass is aimed at a real point: the strongest plume origin of that date (Carbon Mapper), or the MARS source
   point for a MARS-only detection (MARS plumes come without coordinates in our extract) */
const passTargets = (inst) => {
  const byDate = {};
  for (const p of plumes.filter(p => p.inst === inst)) if (!byDate[p.date] || (p.rate || 0) > (byDate[p.date].rate || 0)) byDate[p.date] = p;
  const out = Object.values(byDate).map(p => ({ t: p.t, date: p.date, target: [p.lon, p.lat], rate: p.rate }));
  if (inst === "emi") for (const m of marsPl) if (!out.some(o => o.t === m.t)) out.push({ t: m.t, date: m.date, target: FAC, rate: m.rate, mars: true });
  return out.sort((a, b) => a.t - b.t);
};
const PASSES = { tan: passTargets("tan"), emi: passTargets("emi") };
function satState(sat, now) {
  if (view === "satellites") {             // continuous fly-over loop, aimed at the scene's lead plume
    const tgt = [lead.lon, lead.lat];
    if (RM) return { s: sat.id === selSat ? 0 : 0.8, show: true, target: tgt, date: lead.date };
    const period = 18000, ph = ((now / period) + (sat.id === "emi" ? 0.45 : 0)) % 1;
    return { s: ph * 2 - 1, show: true, target: tgt, date: lead.date };
  }
  if (view !== "incident") return { show: false };
  const win = 14 * 864e5;
  const ps = PASSES[sat.inst].filter(p => Math.abs(T - p.t) <= win).sort((a, b) => Math.abs(T - a.t) - Math.abs(T - b.t));
  if (!ps.length) return { show: false };
  return { s: (T - ps[0].t) / win, show: true, target: ps[0].target, rate: ps[0].rate, date: ps[0].date };
}
function drawSky() {
  if (!mapReady) return;
  const svg = $("sky");
  const now = performance.now();
  const h = svg.clientHeight, w = svg.clientWidth;
  const credH = document.querySelector(".maplibregl-ctrl-bottom-right")?.offsetHeight || 0;   // labels stay clear of the credit line
  const so = svg.getBoundingClientRect(), camEl = $("cam");
  const camR = view === "satellites" && camEl.offsetWidth ? (() => { const a = camEl.getBoundingClientRect(); return { x: a.left - so.left - 8, y: a.top - so.top - 8, r: a.right - so.left + 8, b: a.bottom - so.top + 8 }; })() : null;
  const underCam = (x, y, bw, bh) => !!camR && x < camR.r && x + bw > camR.x && y < camR.b && y + bh > camR.y;
  const boxes = [], keep = (x, y, bw, bh) => boxes.push({ left: so.left + x, top: so.top + y, right: so.left + x + bw, bottom: so.top + y + bh });
  let out = "", windDrawn = false, wbx = 0, wby = 0;
  const glyphs = [], pending = [];   // satellites drawn this frame, and their name labels still to place
  for (const sat of SATS) {
    const st = satState(sat, now);
    if (!st.show) continue;
    const G = map.project(st.target);                 // the exact ground point this pass looks at
    // a screen-space arc across the sky that passes straight above G at s = 0
    const yTop = h * sat.lane;
    const P = (s) => [G.x + sat.dir * s * w * 0.55, yTop + h * 0.07 * s * s];
    let d = "";
    for (let i = 0; i <= 48; i++) { const [x, y] = P(-1.2 + 2.4 * i / 48); d += (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1); }
    out += `<path d="${d}" fill="none" stroke="#7b3fe4" stroke-width="1.7" stroke-dasharray="7 6" opacity=".85"/>`;
    const s = Math.max(-1.2, Math.min(1.2, st.s));
    const [px, py] = P(s);
    const over = Math.abs(s) < 0.18;
    if (over) {
      // locked on: a narrow beam ends in a reticle on the plume origin
      out += `<path d="M${px - 3},${py + 10} L${G.x - 9},${G.y} L${G.x + 9},${G.y} L${px + 3},${py + 10} Z" fill="rgba(245,158,11,.30)"/>`;
      out += `<line x1="${px}" y1="${py + 10}" x2="${G.x}" y2="${G.y}" stroke="#f59e0b" stroke-width="1.6"/>`;
      out += `<g transform="translate(${G.x},${G.y})" fill="none"><g stroke="rgba(0,0,0,.45)" stroke-width="4"><circle r="10"/><path d="M-27,0h9M18,0h9M0,-27v9M0,18v9"/></g>
        <g stroke="#ffb52e" stroke-width="2.2"><circle r="10"/><circle r="19" stroke-dasharray="4 4"/><path d="M-27,0h9M18,0h9M0,-27v9M0,18v9"/></g></g>`;
      // wind: Carbon Mapper's own (used for the rate; FROM convention, data/derived/story/wind_convention.json), else the ERA5 cell
      const cw = X_.cmWindByDate[st.date], ew = WX[st.date];
      const wx = cw ? { wind_direction_10m: cw.dir, wind_speed_10m: cw.speed, src: "Carbon Mapper · used for the rate" } : ew ? { ...ew, src: "ERA5 reanalysis grid cell" } : null;
      if (wx && !windDrawn) {
        windDrawn = true;
        const D2 = map.project(along(...st.target, (wx.wind_direction_10m + 180) % 360, 0.8));
        const vx = D2.x - G.x, vy = D2.y - G.y, L = Math.hypot(vx, vy) || 1, ux = vx / L, uy = vy / L;
        const x0 = G.x + ux * 26, y0 = G.y + uy * 26, x1 = G.x + ux * 92, y1 = G.y + uy * 92;
        wbx = Math.max(4, Math.min(w - 180, x1 + ux * 10 + (ux < 0 ? -176 : 0))); wby = Math.min(h - credH - 26, y1 + uy * 10 + 4);   // the wind box stays inside the map
        const head = `M${x1},${y1} L${x1 - ux * 11 - uy * 6},${y1 - uy * 11 + ux * 6} L${x1 - ux * 11 + uy * 6},${y1 - uy * 11 - ux * 6} Z`;
        out += `<g><line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="rgba(0,0,0,.5)" stroke-width="5"/><line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="#fff" stroke-width="2.4"/><path d="${head}" fill="#fff" stroke="rgba(0,0,0,.5)"/>
          <g transform="translate(${wbx},${wby})" style="${underCam(wbx, wby - 12, 176, 34) ? "display:none" : ""}"><rect x="0" y="-12" width="176" height="34" rx="6" fill="rgba(29,27,24,.86)"/><text x="8" y="2" fill="#fff" font-family="Inter" font-size="11" font-weight="600">wind ${wx.wind_speed_10m.toFixed(1)} m/s from ${compass(wx.wind_direction_10m)}</text><text x="8" y="16" fill="#d8d1c6" font-family="Inter" font-size="10">${wx.src}</text></g></g>`;
      }
    } else {
      // passing: the swath it sweeps, under its own nadir
      const half = w * 0.05;
      out += `<path d="M${px},${py + 10} L${px - half},${G.y} L${px + half},${G.y} Z" fill="rgba(255,255,255,.10)" stroke="rgba(255,255,255,.4)" stroke-width="1"/>`;
    }
    const big = view === "satellites" && sat.id === selSat;
    const gs = big ? 1.6 : 1.3;
    out += satGlyph(px, py, gs, over);
    glyphs.push({ id: sat.id, x: px - 27 * gs, y: py - 8 * gs, r: px + 27 * gs, b: py + 11 * gs });
    const tag = over && (view !== "satellites" || sat.inst === lead.inst);   // the camera loop aims both at the lead plume; only its own satellite saw it
    pending.push({ sat, px, py, gs, over: tag, sub: tag ? (st.rate ? `● ${fmt.format(st.rate)} kg/h in view` : "● methane in view") : "" });
  }
  const placed = [], clash = (x, y, bw, bh) => underCam(x, y, bw, bh)
    || glyphs.some(g => x < g.r && x + bw > g.x && y < g.b && y + bh > g.y)   // its own satellite too: a label pushed off the edge must not land on it
    || placed.some(q => x < q.r + 6 && x + bw > q.x - 6 && y < q.b + 4 && y + bh > q.y - 4);
  if (windDrawn && !underCam(wbx, wby - 12, 176, 34)) placed.push({ x: wbx, y: wby - 12, r: wbx + 176, b: wby + 22 });   // the wind box is already drawn
  for (const { sat, px, py, gs, over, sub } of pending) {
    const lw = over ? 176 : 132, lh = over ? 38 : 24, fit = (x) => Math.max(4, Math.min(w - lw - 4, x));
    const side = 27 * gs + 5, top = (y) => Math.max(16, y);   // the box (12 px above its baseline) stays below the map's top edge
    const right = [fit(px + side), top(py - 14)], left = [fit(px - side - lw), top(py - 14)], below = [fit(px - lw / 2), py + 44], above = [fit(px - lw / 2), top(py - 62)];
    const tries = view === "satellites" && px > w * 0.45 ? [left, right, below, above] : [right, left, below, above];
    const at = tries.find(([x, y]) => !clash(x, y - 12, lw, lh));   // nothing fits: the name is left out rather than drawn over another
    if (!at) continue;
    const [lx, ly] = at;
    placed.push({ x: lx, y: ly - 12, r: lx + lw, b: ly - 12 + lh }); keep(lx, ly - 12, lw, lh);
    out += `<g transform="translate(${lx},${ly})"><rect x="0" y="-12" rx="6" width="${lw}" height="${lh}" fill="rgba(29,27,24,.86)"/>
      <text x="9" y="4" fill="#fff" font-family="Inter" font-size="11.5" font-weight="600">${sat.name}</text>
      ${over ? `<text x="9" y="20" fill="#ffd27a" font-family="Inter" font-size="10.5" font-weight="600">${sub}</text>` : ""}</g>`;
  }
  if (windDrawn && !underCam(wbx, wby - 12, 176, 34)) keep(wbx, wby - 12, 176, 34);
  svg.innerHTML = out;
  skyBoxes = boxes; fitMapLabels();
  if (view === "satellites" && !RM) requestAnimationFrame(() => map.triggerRepaint());
}

/* ---------- satellite camera inset (same <img> tiles, cropped around the facility) ---------- */
let camKind = "ch4", camScene = leadScene;
function setFootprint() {
  const src = map.getSource && map.getSource("fp"); if (!src) return;
  const p = view === "satellites" ? PASS.find(p => p.date === camScene.date && p.inst === "tan") : null;
  src.setData({ type: "FeatureCollection", features: p ? [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [p.footprint] } }] : [] });
}
function drawCam() {
  setFootprint();
  const box = $("camimg"), bw = box.clientWidth || 430, bh = box.clientHeight || 250;
  const satName = SATS.find(x => x.id === selSat).name;
  $("hud-sat").textContent = satName.toUpperCase();
  $("sceneseg").style.display = selSat === "tan" ? "" : "none";
  if (selSat !== "tan") {        // the camera only has Carbon Mapper scene tiles for the Tanager-1 passes in this case
    const em = plumes.filter(p => p.inst === "emi");
    box.innerHTML = `<div class="nocam">No ${satName} scene in this viewer.<br>EMIT saw this facility on ${em.map(p => dfmt(p.t)).join(" and ")}; open the record from "How we know" in the Incident view.</div>`;
    $("hud-tag").style.display = "none"; $("hud-time").innerHTML = ""; $("hud-meta").innerHTML = ""; $("hud-mode").textContent = ""; $("hud-ll").innerHTML = "";
    document.querySelector(".hud .cross").style.display = "none"; return;
  }
  const k = 1.9;                                    // zoom: 1 mosaic px -> k screen px
  const [fx, fy] = mosXY(...FAC);
  box.innerHTML = `<div class="mos ${camKind}" style="transform:translate(${bw / 2 - fx * k}px,${bh / 2 - fy * k}px) scale(${k})">${mosaic(camScene.providerId, camKind)}</div>`;
  document.querySelector(".hud .cross").style.display = "";
  const sceneLead = camScene.date === lead.date ? lead : plumes.filter(p => p.date === camScene.date && p.rate).sort((a, b) => b.rate - a.rate)[0];   // the story's scene tags the plume the story uses
  const tag = $("hud-tag");
  if (sceneLead) {
    const [x, y] = mosXY(sceneLead.lon, sceneLead.lat);
    tag.style.display = "block"; tag.style.left = (bw / 2 + (x - fx) * k + 14) + "px"; tag.style.top = (bh / 2 + (y - fy) * k - 10) + "px";
    tag.textContent = `PLUME ${fmt.format(sceneLead.rate)} kg/h · ${Math.round(sceneLead.distKm * 1000)} m from source`;
    tag.style.left = Math.max(6, Math.min(parseFloat(tag.style.left), bw - tag.offsetWidth - 6)) + "px";   // a long tag stays inside a narrow camera card
    const ring = 34, tl = parseFloat(tag.style.left), tt = parseFloat(tag.style.top), tw = tag.offsetWidth, th = tag.offsetHeight;   // the reticle (46 px ring + ticks) at the centre stays clear
    if (tl < bw / 2 + ring && tl + tw > bw / 2 - ring && tt < bh / 2 + ring && tt + th > bh / 2 - ring) tag.style.top = (tt + th / 2 < bh / 2 ? bh / 2 - ring - th - 2 : bh / 2 + ring + 2) + "px";
  } else tag.style.display = "none";
  $("hud-time").innerHTML = `${dfmt(D(camScene.date)).toUpperCase()}<br>${camScene.time} UTC`;
  $("hud-mode").textContent = camKind === "ch4" ? "CH₄ ENHANCEMENT" : "TRUE COLOUR";
  $("hud-meta").innerHTML = `GSD ${camScene.gsd} m · OFF-NADIR ${camScene.offNadir}°<br>CLOUD ${camScene.cloudPct} % AUTO · ${camScene.cloudAssessed ?? "—"} % ASSESSED`;
  $("hud-ll").innerHTML = `${S.facility.lat.toFixed(4)}° N<br>${S.facility.lon.toFixed(4)}° E`;
}
$("camfullb").onclick = () => { document.documentElement.classList.toggle("camfull"); $("camfullb").textContent = document.documentElement.classList.contains("camfull") ? "⤡ Exit full view" : "⤢ Full view"; setTimeout(drawCam, 30); };
$("camseg").onclick = (e) => { const k = e.target.dataset.k; if (!k) return; camKind = k; [...$("camseg").children].forEach(b => b.classList.toggle("on", b.dataset.k === k)); drawCam(); };
$("sceneseg").innerHTML = S.scenes.map(s => `<button data-d="${s.date}" class="${s === camScene ? "on" : ""}">${dfmt(D(s.date)).replace(/ 20\d\d/, "")}</button>`).join("");
$("sceneseg").onclick = (e) => { const d = e.target.dataset.d; if (!d) return; camScene = S.scenes.find(s => s.date === d); [...$("sceneseg").children].forEach(b => b.classList.toggle("on", b.dataset.d === d)); drawCam(); };

/* ---------- contract view: the players as a graph; what lights up follows the chapter ---------- */
function drawFlow() {
  const ci = chOf(T), c = CH[ci];
  if (c.dza) {
    const N = (x, y, w, t, sub, cls = "") => `<g class="node ${cls} on"><rect x="${x}" y="${y}" width="${w}" height="52" rx="10"/><text x="${x + 14}" y="${y + 22}">${t}</text><text class="sub" x="${x + 14}" y="${y + 39}">${sub}</text></g>`;
    $("flow").innerHTML = `<div class="flowcap">Chapter ${ci + 1} · ${c.short} — who acts</div><svg viewBox="0 0 880 300">
      <defs><marker id="ahon" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="#1d1b18"/></marker></defs>
      <path class="edge on" d="M150,76 L218,76" marker-end="url(#ahon)"/><text class="elab" x="184" y="66" text-anchor="middle">no plume</text>
      <path class="edge sig on" d="M370,76 L440,76 L440,150" marker-end="url(#ahon)"/><text class="elab" x="470" y="118">k of n witnesses sign each clean pass</text>
      <path class="edge money on" d="M520,206 L720,206" marker-end="url(#ahon)"/><text class="elab" x="620" y="196" text-anchor="middle">earn-back steps ${Z9C.installments.map(([, bp]) => bp / 100).join(" / ")} %</text>
      <path class="edge on" d="M150,206 L360,206" marker-end="url(#ahon)"/><text class="elab" x="255" y="196" text-anchor="middle">buyer funds, sponsor tops up</text>
      <path class="edge" d="M90,104 C90,250 300,270 420,236" stroke="#c33a2c" stroke-dasharray="5 4" marker-end="url(#ahon)"/><text class="elab" x="120" y="262" fill="#c33a2c">a new detection cancels what is unpaid</text>
      ${N(10, 50, 140, "Satellites", "Tanager-1 · EMIT")}${N(220, 50, 150, `Witnesses × ${S.policy.n}`, "independent organisations", "simn")}
      ${N(10, 180, 140, "Buyer + sponsor", "fund the pool", "simn")}${N(360, 180, 160, "IncentivePool", "earn-back claim", "chainn")}${N(722, 180, 140, "Seller", "earns it back", "simn")}
    </svg>`;
    return;
  }
  const live = (id) => c.act.includes(id) || (id === "k" && c.act.includes("contract"));
  const N = (id, x, y, w, title, sub, cls = "") => `<g class="node ${cls} ${live(id) ? "on" : ""}" opacity="${live(id) || ci < 3 ? 1 : .55}"><rect x="${x}" y="${y}" width="${w}" height="52" rx="10"/><text x="${x + 14}" y="${y + 22}">${title}</text><text class="sub" x="${x + 14}" y="${y + 39}">${sub}</text></g>`;
  const E = (d, on, cls = "", lab = "", lx = 0, ly = 0) => `<path class="edge ${cls} ${on ? "on" : ""}" d="${d}" marker-end="url(#ah${on ? "on" : ""})"/>${lab ? `<text class="elab" x="${lx}" y="${ly}" text-anchor="middle">${lab}</text>` : ""}`;
  const tok = (d, col) => RM ? "" : `<circle r="5" fill="${col}"><animateMotion dur="1.8s" repeatCount="indefinite" path="${d}"/></circle>`;
  const P = { s2c: "M150,76 L218,76", c2o: "M370,76 L438,76", o2k: "M590,76 L640,76 L640,150", s2k: "M150,206 L560,206", k2b: "M720,206 L760,206" };
  const id = c.id;
  const sk = ci >= 3;                                    // bond locked from the clause chapter on (the main page's "Bond posted")
  const extra = id === "proof" ? `<g transform="translate(560,118)"><rect width="160" height="24" rx="12" fill="#fbe4e1"/><text x="80" y="16" text-anchor="middle" font-size="11.5" font-weight="600" fill="#c33a2c">✕ ${rejects.length} bad attempts rejected</text></g>`
    : id === "window" ? `<g transform="translate(560,118)"><rect width="160" height="24" rx="12" fill="#e4ecfd"/><text x="80" y="16" text-anchor="middle" font-size="11.5" font-weight="600" fill="#2457d6">⏱ ${S.policy.challengeWindow / 3600} h · no appeal</text></g>`
    : id === "paid" ? `<g transform="translate(560,118)"><rect width="160" height="24" rx="12" fill="#e0f3e7"/><text x="80" y="16" text-anchor="middle" font-size="11.5" font-weight="600" fill="#157a42">✓ CONFIRMED · paid</text></g>` : "";
  if (id === "proof" && tamperOn) {
    const t = steps.reject_tamper;
    $("flow").innerHTML = `<div class="flowcap">Chapter ${ci + 1} · try to cheat</div>
    <div class="tamper"><div class="ev ok"><small>The record as witnesses A and B signed it</small><b>${fmt.format(lead.rate)} kg/h</b><span>tier ${tierOf(lead.rate) + 1} → rebate ${ETH(S.policy.tierAmountsWei[tierOf(lead.rate)])} ETH</span><span class="sig">✓ both signatures match</span></div>
      <div class="arrow">→</div>
      <div class="ev bad"><small>The ${sim("seller")} edits one number, keeps the signatures</small><b>${fmt.format(TAMPER_RATE)} kg/h</b><span>tier ${tierOf(TAMPER_RATE) + 1} → rebate ${ETH(S.policy.tierAmountsWei[tierOf(TAMPER_RATE)])} ETH</span><span class="sig">✕ signatures no longer match the evidence</span></div></div>
    <div class="verdict">The contract refused it: <b>${t.reason}</b>, status 0 · <a href="${t.tx}" target="_blank" rel="noopener">${tx(t).slice(0, 10)}…${tx(t).slice(-6)} on Sepolia ↗</a> · <a href="index.html?tamper=A">recompute it in your browser →</a></div>`;
    return;
  }
  $("flow").innerHTML = `<div class="flowcap">Chapter ${ci + 1} · ${c.short} — who acts</div><svg viewBox="0 0 880 300">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="#d6cec1"/></marker>
    <marker id="ahon" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="#1d1b18"/></marker></defs>
    ${E(P.s2c, id === "caught", "", "image", 184, 68)}
    ${E(P.c2o, id === "caught" || id === "proof", "", "record", 404, 68)}
    ${E(P.o2k, id === "proof", "sig", `${S.policy.k}-of-${S.policy.n} signatures`, 712, 112)}
    ${E(P.s2k, sk, "money", id === "clause" ? `clause signed · bond ${bondEth} ETH` : `bond ${bondEth} ETH`, 300, 198)}
    ${E(P.k2b, id === "paid", "money", "", 0, 0)}
    <path class="edge" d="M514,250 L514,104" stroke-dasharray="4 4"/>
    <text class="elab" x="526" y="232" text-anchor="start">an input, not a judge</text>
    ${N("sat", 10, 50, 140, "Satellites", "EMIT · Tanager-1")}
    ${N("cm", 220, 50, 150, "Carbon Mapper", `${plumes.length} plume records`)}
    ${N("obs", 440, 50, 150, `Witnesses × ${S.policy.n}`, "independent organisations", "simn")}
    ${N("seller", 10, 180, 140, "Seller", sk ? `bond ${bondEth} ETH` : "gas exporter", "simn")}
    ${N("k", 560, 150, 160, "The clause", "MethaneCovenant · Sepolia", "chainn")}
    ${N("buyer", 762, 180, 108, "Buyer", id === "paid" ? `+${rebateEth} ETH` : "EU importer", "simn")}
    ${N("mars", 440, 250, 150, "UNEP IMEO MARS", `says “${S.mars.persistency}”`)}
    ${extra}
    ${id === "window" ? `<g class="alt">
      <path d="M720,160 C780,150 800,120 812,96" fill="none" stroke="#7b3fe4" stroke-width="1.8" stroke-dasharray="6 5"/>
      <g transform="translate(736,52)"><rect width="142" height="44" rx="9" fill="#f1e9fe" stroke="#7b3fe4" stroke-dasharray="5 4"/><text x="10" y="18" font-size="11.5" font-weight="600" fill="#7b3fe4">Appeal</text><text x="10" y="34" font-size="10.5" fill="#7b3fe4">anyone · bond + reason</text></g>
      <path d="M640,202 C640,232 660,252 690,262" fill="none" stroke="#7b3fe4" stroke-width="1.8" stroke-dasharray="6 5"/>
      <g transform="translate(672,244)"><rect width="206" height="44" rx="9" fill="#f1e9fe" stroke="#7b3fe4" stroke-dasharray="5 4"/><text x="10" y="18" font-size="11.5" font-weight="600" fill="#7b3fe4">Contest by the seller</text><text x="10" y="34" font-size="10.5" fill="#7b3fe4">needs k+1 = ${S.policy.k + 1} of ${S.policy.n} witnesses</text></g>
      <text x="806" y="112" font-size="10.5" font-weight="700" fill="#7b3fe4">not filed</text><text x="600" y="300" font-size="10.5" font-weight="700" fill="#7b3fe4">not filed</text></g>` : ""}
    ${id === "caught" ? tok(P.s2c, "#e2590f") + tok(P.c2o, "#e2590f") : ""}${id === "proof" ? tok(P.o2k, "#2457d6") : ""}${id === "paid" ? tok(P.k2b, "#157a42") : ""}
  </svg>`;
}

/* ---------- right panel ---------- */
const REFUSAL = { CLOUD_FLAG: "too cloudy (cloud flag)", NO_ORIGIN_INTERSECT: "footprint missed the plume origins", SCENE_BEFORE_SINCE: "before the claim opened" };
function sideRefund() {
  const now = z9Real(T), seen = z9Scenes.filter(z => z.t <= now);
  const counted = seen.filter(z => z.qualified && !z.afterRelapse).length;
  const inst = Z9C.installments;    // [passes, basis points, needs two instruments, needs MARS corroboration]
  const reached = inst.filter(([n]) => counted >= n);
  const relapsed = now >= z9Relapse;
  const bar = inst.map(([n, bp, two, corr], i) => {
    const got = counted >= n, lost = relapsed && !got;
    return `<div class="seg ${got ? "got" : lost ? "lost" : ""}" style="flex:${bp}"><b>${bp / 100} %</b><small>at ${n} passes${two ? " · 2 instruments" : ""}${corr ? " · MARS corroboration" : ""}</small></div>`;
  }).join("");
  const rows = seen.slice().reverse().map(z => `<li><span class="mk ${z.qualified ? "ok" : ""}"></span><span><b>${dfmt(z.t)}</b> · ${z.inst === "tan" ? "Tanager-1" : "EMIT"} · cloud ${z.cloud} %<br>
      <span class="sub">${z.afterRelapse ? (z.qualified ? "counted in the new claim after the relapse" : "after the relapse · " + (REFUSAL[z.refusal] || z.refusal)) : z.qualified ? "clean pass counted" : "refused · " + (REFUSAL[z.refusal] || z.refusal)}</span></span></li>`).join("");
  const eth = (w) => Number(BigInt(w)) / 1e18;
  return `
  <div class="card"><h3>Earn-back ledger <span class="r">${Z9.source} · ${dfmt(now)}</span></h3>
    <div class="kpis"><div><b>${counted}</b><small>passes counted</small></div><div><b style="color:var(--money)">${z9Pct(reached.reduce((a, [, bp]) => a + bp, 0))}</b><small>earned back</small></div>
      <div><b style="color:${relapsed ? "var(--bad)" : "inherit"}">${relapsed ? z9Pct(z9Out.forfeitedBp) : "—"}</b><small>forfeited</small></div><div><b>${seen.filter(z => !z.qualified).length}</b><small>refused</small></div></div>
    <div class="inst">${bar}</div>
    <p class="hint">${relapsed ? `${dfmt(z9Relapse)}: ${(SATS.find(x => x.inst === Z9.relapse.instrument) || { name: Z9.relapse.instrument }).name} saw ${fmt.format(Z9.relapse.rateKgH)} ± ${fmt.format(Z9.relapse.uncKgH)} kg/h. A new attested detection cancels what is still unpaid.` : `Earn-back steps are paid at ${inst.map(([n]) => n).join(", ")} counted clean passes${(() => { const [, , two, corr] = inst.at(-1); const need = [two && "two instruments", corr && "a MARS corroboration"].filter(Boolean); return need.length ? `; the last needs ${need.join(" and ")}` : ""; })()}.`}</p></div>
  <div class="card"><h3>Replay <span class="r">${Z9.replayA.chain} ${pinned}</span></h3><dl class="kv">
    <dt>Witness fees (${sim("sponsor")})</dt><dd>${eth(z9Sp.observerFeesWei)} ETH</dd></dl>
    <p class="foot-note">${simMark(Z9C.covenantLabel)}; ${simMark(Z9.replayA.label)}; ${Z9.replayA.checks} checks. ${Z9C.statusLabel}.</p></div>
  <div class="card"><h3>Passes <span class="r">${seen.length} of ${z9Scenes.length}</span></h3><ul class="hwk">${rows}</ul></div>
  <div class="card"><h3>On Sepolia</h3><ul class="txs">${Z9.sandbox.map(st => `<li><span class="v">✓</span><div><span style="color:var(--sim)">${noSim(st.step.replace("judge sandbox [SIM]: ", "")).replace(/observer/g, "witness")}</span><br><a class="h" href="${st.tx}" target="_blank" rel="noopener">${st.tx.split("/tx/")[1].slice(0, 10)}…</a></div><span class="h">${fmt.format(st.gasUsed)} gas</span></li>`).join("")}</ul>
    <p class="foot-note" style="color:var(--sim)">Run with a judge test wallet operated by the author, in the sandbox clause.</p></div>
  <div class="card"><h3>Why the refund is capped</h3><p class="notprove" style="padding-top:2px">${Z9.capSentence}</p></div>
  ${NOTPROVE()}`;
}
function sideIncident() {
  if (inZ9(T) && !prologue) return sideRefund();
  const ps = PLAYERS();
  const cast = ps.map(p => {
    const changed = lastSt[p.id] !== undefined && lastSt[p.id] !== p.st; lastSt[p.id] = p.st;
    const act = CH[chOf(T)].act.includes(p.id);
    return `<li class="${p.sim ? "simr" : ""} ${changed ? "flash" : ""} ${act ? "act" : "rest"}"><div class="ico">${ICON[p.icon]}</div>
      <div><div class="nm">${p.name}</div><div class="st">${p.st}</div></div><span class="light ${p.light}"></span></li>`;
  }).join("");
  const cmSince = plumes.filter(p => p.t > marsPl.at(-1).t && p.t <= T);
  return `
  <div class="card"><h3>Players <span class="r">${prologue ? "the problem" : `chapter ${chOf(T) + 1}`} · ${dfmt(T)}</span></h3><ul class="cast">${cast}</ul></div>
  <div class="card"><h3>Two records</h3>
    ${after("marslast") ? `<div class="vs"><div class="a"><small>UNEP IMEO MARS</small><div class="big">“${S.mars.persistency}”</div><small>last plume ${dfmt(marsLast.t)}</small></div>
    <div class="b"><small>Carbon Mapper since then${prologue ? "" : " · so far"}</small><div class="big">${(() => { const H = S.headline; if (prologue) return `${H.n_cm_detected} of ${H.n_cm_passes}`; const dt = H.detections.map(D).filter(t => t <= T).length; return dt ? `${dt} of ${dt}` : "no pass yet"; })()}</div><small>passes over Carbon Mapper sources within ${S.headline.radius_km} km saw a plume</small></div></div>`
    : `<div class="vs"><div class="a"><small>UNEP IMEO MARS</small><div class="big">${pl(marsPl.filter(p => p.t <= T).length, "plume")}</div><small>government notified</small></div>
    <div class="b"><small>Carbon Mapper so far</small><div class="big">${pl(days(plumes.filter(p => p.t <= T)), "day")}</div><small>with a plume within ${S.facility.radiusKm} km</small></div></div>`}
    <dl class="kv"><dt>Government notified</dt><dd>${S.mars.notified}</dd><dt>Operator reply to UNEP</dt><dd class="bad">${S.mars.operatorFeedback === "No" ? "None" : S.mars.operatorFeedback}</dd>
    <dt>Lead plume</dt><dd class="good">${fmt.format(lead.rate)} kg/h · ${dfmt(lead.t)}${lead.t > T ? " (later)" : ""}</dd></dl></div>
  ${prologue || chOf(T) >= 2 ? `<div class="card"><h3>At stake <span class="r">${S.stake.label}</span></h3>
    <div class="vs"><div class="b" style="grid-column:1/3"><small>Methane at stake if the duty cycle seen on passes holds</small><div class="big">${S.stake.kt_ch4_per_yr.low}–${S.stake.kt_ch4_per_yr.high} kt CH₄ / yr</div>
    <small>${fmt.format(Math.round(S.stake.kt_co2e_per_yr_gwp100.low))}–${fmt.format(Math.round(S.stake.kt_co2e_per_yr_gwp100.high))} kt CO₂e (GWP100 ${S.stake.gwp100}) · basis: ${S.stake.detected} of ${S.stake.passes} passes over Carbon Mapper sources within ${S.facility.radiusKm} km since ${dfmt(D(S.stake.since))}</small></div></div></div>` : ""}
  <div class="card"><h3>How we know <span class="r">${OBS.filter(o => o.t <= T).length} observations so far</span></h3><ul class="hwk">${OBS.filter(o => o.t <= T).slice(-6).reverse().map(o => {
      const best = plumes.filter(q => q.date === o.date && q.rate).sort((a, b) => b.rate - a.rate)[0];
      return `<li><span class="mk ${o.det ? "on" : ""}"></span><span><b>${dfmt(o.t)}</b> · ${o.inst === "tan" ? "Tanager-1" : "EMIT"}${o.scene && o.scene.cloudPct != null ? ` · cloud ${o.scene.cloudPct} % auto` : ""}<br>
        <span class="sub">${o.det ? `plume seen${best ? ` · ${fmt.format(best.rate)} kg/h` : ""}` : "observed · no plume"}</span></span>
        ${o.scene && o.scene.uuid ? `<a href="https://api.carbonmapper.org/api/v1/catalog/plumes/annotated?scene_id=${o.scene.uuid}" target="_blank" rel="noopener" title="Raw Carbon Mapper record">raw ↗</a>` : ""}</li>`; }).join("")}</ul>
    <p class="foot-note">Carbon Mapper's own observation list for source ${NS.name} (${Math.round(NS.distKm * 1000)} m away). Since MARS's last plume: ${NS.postMars.n_detected} of ${NS.postMars.n_passes} (data/derived/cases/TKM_S_276.json, sha256 ${NS.contentSha256.slice(0, 8)}…).</p></div>
  <div class="card"><h3>Layers</h3><ul class="layers" id="layers">
    ${LAYERS.map(l => `<li><label><input type="checkbox" data-l="${l.id}" ${LAYER_ON[l.id] ? "checked" : ""}> <span class="sw" style="${l.sw}"></span>${l.sim ? `<span style="color:var(--sim)">${l.name}</span>` : l.name}</label></li>`).join("")}
  </ul></div>
  ${NOTPROVE()}`;
}
function sideSatellites() {
  const sats = SATS.map(s => {
    const n = days(plumes.filter(p => p.inst === s.inst));
    return `<li data-s="${s.id}" class="${s.id === selSat ? "on" : ""}"><div class="ico">${ICON.sat}</div><div><div class="nm">${s.name}</div><div class="st">${s.org}</div></div><span class="badge ok">${n} plume days</span></li>`;
  }).join("");
  const why = { SCENE_BEFORE_SINCE: "Plume seen: counts toward the confirmed leak, not as a clean pass", CLOUD_FLAG: "Too cloudy to count as a clean pass" };
  const passes = S.scenes.map(s => {
    const n = plumes.filter(p => p.date === s.date).length;
    return `<li><b>${dfmt(D(s.date))} · ${s.time} UTC</b><span class="badge ${n ? "ok" : "chain"}">${n ? n + " plumes" : "no plume"}</span><span class="why">cloud ${s.cloudPct} % · GSD ${s.gsd} m · ${why[s.refusal] || s.refusal}</span></li>`;
  }).join("");
  const sat = SATS.find(s => s.id === selSat), mine = plumes.filter(p => p.inst === sat.inst);
  return `
  <div class="card"><h3>Satellite fleet <span class="r">${SATS.length} of ${SATS.length} real</span></h3>
    <div class="kpis"><div><b>${SATS.length}</b><small>satellites</small></div><div><b>${plumes.length}</b><small>plumes</small></div><div><b>${days(plumes)}</b><small>plume days</small></div><div><b>${S.scenes.length}</b><small>passes since ep.</small></div></div>
    <p class="hint">Pick a satellite to follow its pass. The camera shows the real CH₄ scene it recorded.</p>
    <ul class="fleet" id="fleet">${sats}</ul></div>
  <div class="card"><h3>${sat.name}</h3><dl class="kv">
    <dt>Operator</dt><dd>${sat.org}</dd>
    <dt>Plumes at this facility</dt><dd class="good">${mine.length}</dd>
    <dt>First / last</dt><dd>${dfmt(mine[0].t)} → ${dfmt(mine.at(-1).t)}</dd>
    <dt>Largest</dt><dd>${fmt.format(Math.max(...mine.map(p => p.rate || 0)))} kg/h</dd>
    <dt>Sky position</dt><dd style="color:var(--sim)">drawn near · not to scale</dd></dl></div>
  ${(() => { const w = WX[camScene.date], ps = PASS.find(p => p.date === camScene.date && p.inst === "tan"); return `<div class="card"><h3>Conditions at the pass <span class="r">${dfmt(D(camScene.date))} ${camScene.time} UTC</span></h3><dl class="kv">
    ${w ? `<dt>Wind (ERA5 grid cell)</dt><dd>${w.wind_speed_10m.toFixed(1)} m/s from ${compass(w.wind_direction_10m)} (${w.wind_direction_10m}°)</dd><dt>Air</dt><dd>${w.temperature_2m} °C · RH ${w.relative_humidity_2m} %</dd>` : ""}
    ${camScene.date === lead.date ? `<dt>Wind used for the rate</dt><dd>${X_.cmWindByDate[lead.date].speed.toFixed(1)} m/s from ${Math.round(X_.cmWindByDate[lead.date].dir)}° (Carbon Mapper)</dd>` : ""}
    <dt>Cloud (auto · assessed)</dt><dd>${camScene.cloudPct} % · ${camScene.cloudAssessed ?? "—"} %</dd><dt>Off-nadir · GSD</dt><dd>${camScene.offNadir}° · ${camScene.gsd} m</dd>
    ${X_.satgeo[camScene.date] ? `<dt>Where the satellite was</dt><dd>≈ ${Math.round(X_.satgeo[camScene.date].distKm)} km away (${camScene.offNadir}° view angle, ~${Math.round(X_.satgeo[camScene.date].altKm)} km orbit today)</dd>` : ""}
    ${ps && ps.sunElev != null ? `<dt>Sun elevation</dt><dd>${ps.sunElev}°</dd>` : ""}</dl>
    <p class="foot-note">The white outline on the map is this scene's real footprint (Carbon Mapper). The arrow and the rate use Carbon Mapper's wind (blowing from that direction; the plume masks point downwind of it). ERA5 is a reanalysis grid cell, shown for context. A dispute over wind is the seller's contest path. The satellite's position is estimated from the scene's view angle and a ${X_.satgeo[camScene.date] ? Math.round(X_.satgeo[camScene.date].altKm) : "current"} km orbit.</p></div>`; })()}
  <div class="card"><h3>Tanager-1 passes since the confirmed leak <span class="r">from ${dfmt(lead.t)}</span></h3><ul class="passes">${passes}</ul></div>`;
}
function sideContract() {
  if (inZ9(T) && !prologue) {
    const eth = (w) => Number(BigInt(w)) / 1e18;
    return `<div class="card"><h3>Earn-back clause <span class="r">${Z9.source}</span></h3>
      <div class="kpis"><div><b>${z9Out.counted}</b><small>passes counted</small></div><div><b style="color:var(--money)">${eth(z9Sp.releasedWei)}</b><small>ETH back</small></div><div><b style="color:var(--bad)">${eth(z9Sp.forfeitedWei)}</b><small>ETH forfeited</small></div><div><b>${Z9.replayA.checks}</b><small>checks</small></div></div>
      <p class="hint"><span>Replayed on ${Z9.replayA.chain} with a ${sim("pinned clock")} (${simMark(Z9C.covenantLabel)}).</span></p></div>
      <div class="card"><h3>Earn-back steps</h3><dl class="kv">${Z9C.installments.map(([n, bp, two, corr]) => `<dt>at ${n} counted passes</dt><dd>${bp / 100} %${two ? " · 2 instruments" : ""}${corr ? " · MARS corroboration" : ""}</dd>`).join("")}</dl></div>
      <div class="card"><h3>On Sepolia <span class="r">judge sandbox</span></h3><ul class="txs">${Z9.sandbox.map(st => `<li><span class="v">✓</span><div><span style="color:var(--sim)">${noSim(st.step.replace("judge sandbox [SIM]: ", "")).replace(/observer/g, "witness")}</span><br><a class="h" href="${st.tx}" target="_blank" rel="noopener">${st.tx.split("/tx/")[1].slice(0, 10)}…</a></div><span class="h">${fmt.format(st.gasUsed)} gas</span></li>`).join("")}</ul></div>`;
  }
  const order = ["createCovenant", "reject_tamper", "reject_self_attestation", "reject_not_covered", "reject_replay", "proposeEpisode", "finalize", "withdraw_buyer", "withdraw_proposer"];
  const at = { createCovenant: "deploy", reject_tamper: "rejects", reject_self_attestation: "rejects", reject_not_covered: "rejects", reject_replay: "rejects", proposeEpisode: "propose", finalize: "final", withdraw_buyer: "paid", withdraw_proposer: "paid" };
  const label = { createCovenant: "Create the clause · lock bond", reject_tamper: "Tampered evidence", reject_self_attestation: "Seller signs own evidence", reject_not_covered: "Facility not covered", reject_replay: "One signature counted twice", proposeEpisode: "Propose the leak", finalize: "Finalize (no challenge)", withdraw_buyer: "Buyer withdraws rebate", withdraw_proposer: "Proposer withdraws its bond" };
  const rows = order.map(k => steps[k]).map(s => `<li class="${after(at[s.step]) ? "" : "later"}"><span class="${s.status ? "v" : "x"}">${s.status ? "✓" : "✕"}</span>
    <div>${label[s.step]}${s.reason ? ` <span class="badge bad">${s.reason}</span>` : ""}<br><a class="h" href="${s.tx}" target="_blank" rel="noopener">${tx(s).slice(0, 10)}…${tx(s).slice(-6)}</a></div>
    <span class="h">${fmt.format(s.gasUsed)} gas</span></li>`).join("");
  const tiers = S.policy.tierMinKgH.map((m, i) => `<dt>≥ ${fmt.format(m)} kg/h</dt><dd>${ETH(S.policy.tierAmountsWei[i])} ETH</dd>`).join("");
  const kp = `<div class="kpis"><div><b>${after("start") ? bondEth : "—"}</b><small>bond ETH</small></div><div><b>${after("paid") ? rebateEth : after("propose") ? WIN_H + " h" : "—"}</b><small>${!after("paid") && after("propose") ? "until rebate" : "rebate ETH"}</small></div><div><b>${S.sepolia.steps.length}</b><small>transactions</small></div><div><b style="color:var(--bad)">${rejects.length}</b><small>rejected</small></div></div>
    <p class="hint">Every row below is a real Sepolia transaction. Red rows are attacks we sent; the contract refused each.</p>`;
  return `
  <div class="card"><h3>On chain <span class="r">Ethereum Sepolia</span></h3>${kp}</div>
  <div class="card"><h3>No trusted operator?</h3><p class="notprove" style="padding-top:2px">None exists for this function. Registries (MiQ, JOGMEC CLEAN, UNEP MARS) certify and notify but cannot move a seller's money; a bank can move money but cannot adjudicate satellite evidence; a court can do both and takes years. Seller and buyer are counterparties: neither accepts an operator chosen by the other, so the decision has to come from k independent observers plus an open challenge window — which is what the contract executes.</p>
    <p class="notprove"><b>Fits the ledger?</b> Only hashes, IDs and money: <code>evidenceHash</code>, <code>sceneHash</code>, <code>policyHash</code>, a Merkle root of covered facilities, the bond, the pool. Raw data stays with the providers and the observers.</p>
    <p class="notprove"><b>Why a rule, not a model?</b> Our ML forecast did not beat the persistence rule on both held-out sets, so the rule sizes the bond.</p></div>
  <div class="card"><h3>The clause <span class="r">policy on chain</span></h3><dl class="kv">
    <dt>Witnesses needed</dt><dd>${S.policy.k} of ${S.policy.n}</dd><dt>Appeal window</dt><dd>${S.policy.challengeWindow / 3600} h</dd>${tiers}
    <dt>Contract</dt><dd><a href="https://sepolia.etherscan.io/address/${S.sepolia.covenant}" target="_blank" rel="noopener" style="font:11px var(--mono)">${S.sepolia.covenant.slice(0, 8)}…${S.sepolia.covenant.slice(-4)}</a></dd></dl></div>
  <div class="card"><h3>Transactions <span class="r">${S.sepolia.steps.length} on Sepolia</span></h3><ul class="txs">${rows}</ul></div>`;
}
/* ---------- Verify: a working stopwatch over the committed manual protocol (data/derived/verification_time.json) ---------- */
const V = S.verify;
const proposeTx = steps.proposeEpisode.tx;
const VLINKS = {      // public sources a judge opens for each step (none is needed for "compare")
  mars: [["UNEP IMEO MARS public dataset (zip)", "https://unepazeconomyadlsstorage.blob.core.windows.net/public/unep_methanedata_detected_plumes_geojson.zip"]],
  catalogue: [["Carbon Mapper data portal", "https://data.carbonmapper.org/"], [`source ${NS.name}`, `https://api.carbonmapper.org/api/v1/catalog/search/source/${NS.name}`]],
  passes: [["observation list of that source", `https://api.carbonmapper.org/api/v1/catalog/search/source/${NS.name}`]],
  compare: [],
  hash: [["proposeEpisode on Sepolia (evidenceHash)", proposeTx]],
};
const VFACTS = {      // what the judge should find, from the same committed data
  mars: `persistency “${S.mars.persistency}”, last plume ${dfmt(marsLast.t)}`,
  catalogue: `nearest source ${Math.round(NS.distKm * 1000)} m away`,
  passes: `since ${dfmt(marsLast.t)}: ${NS.postMars.n_detected} of ${NS.postMars.n_passes} passes with a plume`,
  compare: `${dfmt(lead.t)}: ${fmt.format(lead.rate)} ± ${fmt.format(lead.unc)} kg/h, after the label`,
  hash: `evidenceHash ${S.sepolia.evidenceHash.slice(0, 10)}…${S.sepolia.evidenceHash.slice(-6)}`,
};
let vRun = { start: 0, stepStart: 0, i: 0, secs: {}, done: false };
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function vNow() { return vRun.start ? (vRun.done ? Object.values(vRun.secs).reduce((a, b) => a + b, 0) : (performance.now() - vRun.start) / 1000) : 0; }
setInterval(() => { const c = $("vclock"); if (c && vRun.start && !vRun.done) c.textContent = mmss(vNow()); }, 250);
function vNext() {
  const t = performance.now();
  if (!vRun.start) { vRun.start = vRun.stepStart = t; renderSide(); return; }
  if (vRun.done) return;
  vRun.secs[V.steps[vRun.i].id] = Math.round((t - vRun.stepStart) / 100) / 10;
  vRun.stepStart = t; vRun.i++;
  if (vRun.i >= V.steps.length) vRun.done = true;
  renderSide();
}
function vSave() {   // same shape as one manual run in verification_time.json; append with scripts/measure_verification.py
  const run = { at: new Date().toISOString().slice(0, 19) + "Z", stepSeconds: vRun.secs, totalSeconds: Math.round(vNow() * 10) / 10 };
  const blob = new Blob([JSON.stringify({ schema: V.schema, label: V.label, case: S.case, run }, null, 1)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "manual_run.json"; a.click();
}
function sideVerify() {
  const rows = V.steps.map((s, i) => {
    const st = vRun.done || i < vRun.i ? "done" : vRun.start && i === vRun.i ? "now" : "";
    const links = VLINKS[s.id].map(([l, u]) => `<a href="${u}" target="_blank" rel="noopener">${l} ↗</a>`).join(" · ");
    return `<li class="${st}"><span class="num">${st === "done" ? "✓" : i + 1}</span><div><b>${s.text}</b>${links ? `<br><span class="vl">${links}</span>` : ""}
      ${st === "done" ? `<br><span class="vf">expected: ${VFACTS[s.id]}</span>` : ""}</div><span class="t">${vRun.secs[s.id] != null ? mmss(vRun.secs[s.id]) : st === "now" ? "…" : ""}</span></li>`;
  }).join("");
  const btn = !vRun.start ? "Start the clock" : vRun.done ? "Done" : vRun.i === V.steps.length - 1 ? "Finish" : "Step done";
  return `
  <div class="card"><h3>Your turn: check it yourself <span class="r">${V.steps.length} steps</span></h3>
    <div class="clock"><span class="big" id="vclock">${mmss(vNow())}</span><span><button class="btn ghost" id="vreset">Reset</button> <button class="btn" id="vnext" ${vRun.done ? "disabled" : ""}>${btn}</button></span></div>
    <ul class="verify">${rows}</ul>
    ${vRun.done ? `<p class="foot-note"><button class="btn ghost" id="vsave">Save my run (manual_run.json)</button></p>` : ""}
    <p class="foot-note">The question: ${V.question.replace(/observers/g, "witnesses")} Your times stay in this browser until you save them; they are ${V.label}. The script does the same check in ${V.toolMedianSeconds} s (median, ${V.toolMode}); ${V.manualRuns} manual runs are recorded so far.</p></div>`;
}

const LAYERS = [
  { id: "img", name: "Satellite imagery · terrain", sw: "background:linear-gradient(90deg,#c8b48f,#9d8a6a)" },
  { id: "ch4", name: "CH₄ enhancement (Carbon Mapper scene)", sw: "background:linear-gradient(90deg,rgba(232,92,14,.2),#f2a35a,#fff3dc)" },
  { id: "pl", name: "Plume origins", sw: "background:#e2590f;border-radius:50%;width:14px" },
  { id: "fac", name: "Facility ring", sw: "border:1.6px dashed #8f887d;border-radius:50%;width:14px" },
  { id: "osm", name: "Pipelines and roads (OSM)", sw: "background:#ffd76a" },
  { id: "sky", name: "Satellite path, drawn near (not to scale)", sw: "border:1.6px dashed var(--sim);background:var(--sim-soft)", sim: true },
];
const LAYER_ON = Object.fromEntries(LAYERS.map(l => [l.id, true]));
function applyLayers() {
  const vis = (ids, on) => ids.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none"); });
  if (view !== "verify") vis(["img"], LAYER_ON.img);
  vis(["pl", "pl-halo"], LAYER_ON.pl); vis(["fac-fill", "fac-line"], LAYER_ON.fac); vis(["osm-road", "osm-pipe"], LAYER_ON.osm);
  if (ch4El) ch4El.style.visibility = LAYER_ON.ch4 ? "visible" : "hidden";
  document.querySelectorAll(".osmlbl").forEach(e => (e.style.visibility = LAYER_ON.osm ? "visible" : "hidden"));
  $("sky").style.visibility = LAYER_ON.sky ? "visible" : "hidden";
}
function NOTPROVE() { return `<div class="card"><h3>What this does not prove</h3><ul class="notprove">
    <li><b>Not abatement.</b> A rebate follows an attested plume. Observed, not abated.</li>
    <li><b>Not legal compliance.</b> Evidence for a regulator's form, never a finding.</li>
    <li class="simt"><b>Prototype parties.</b> Seller, buyer and the witness organisations are played by one person.</li>
    <li class="simt"><b>Back-dated term.</b> The clause was deployed on ${dfmt(created)} with its term back-dated to ${dfmt(startT)}, after this plume was public: the run proves the mechanism, not the absence of foreknowledge.</li>
    <li class="simt"><b>Sky positions.</b> Satellites are drawn close to the scene; pass dates and images are real.</li>
  </ul></div>`; }
const castMini = () => `<div class="card"><h3>Players <span class="r">chapter ${chOf(T) + 1}</span></h3><div class="mini">${PLAYERS().map(p => `<span class="${p.sim ? "simr" : ""} ${CH[chOf(T)].act.includes(p.id) ? "act" : ""}" title="${p.st.replace(/<[^>]*>/g, "")}">${ICON[p.icon]}${p.name.split(" ·")[0]}</span>`).join("")}</div></div>`;
function renderSide() {
  $("side").innerHTML = view === "incident" ? sideIncident() : view === "satellites" ? castMini() + sideSatellites() + NOTPROVE() : view === "contract" ? sideContract() + NOTPROVE() : castMini() + sideVerify() + NOTPROVE();
  const fl = $("fleet");
  if (fl) fl.onclick = (e) => { const li = e.target.closest("li"); if (li) { selSat = li.dataset.s; renderSide(); drawCam(); } };
  if ($("vnext")) { $("vnext").onclick = vNext; $("vreset").onclick = () => { vRun = { start: 0, stepStart: 0, i: 0, secs: {}, done: false }; renderSide(); }; }
  if ($("vsave")) $("vsave").onclick = vSave;
  const ly = $("layers"); if (ly) ly.onchange = (e) => { const id = e.target.dataset.l; if (id) { LAYER_ON[id] = e.target.checked; applyLayers(); } };
}

/* ---------- timeline: one band per chapter ---------- */
const segLabel = (c, i) => c.dza ? `${new Date(Z9_R0).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" })} → ${new Date(Z9_R1).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" })}` : i < 5 ? new Date(c.from).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" })
  : dfmt(c.at, true).replace(" 2026", "").replace(" UTC", "");      // on-chain bands: the key moment, to the minute
function trackSvg() {
  const svg = $("tracksvg"), Wd = svg.clientWidth || 800;
  const X = (t) => xOf(t) * Wd, ci = prologue ? -1 : chOf(T);   // the opening screen highlights no chapter
  let s = "", labEnd = -Infinity;
  CH.forEach((c, i) => {
    const x0 = c.x0 * Wd, w = (c.x1 - c.x0) * Wd, st = i < ci ? "done" : i === ci ? "now" : "next";
    const fill = c.sim ? (st === "now" ? "var(--sim)" : "var(--sim-soft)") : st === "now" ? "#1d1b18" : st === "done" ? "#cfc6b8" : "#efe9df";
    const ink = st === "now" ? "#fff" : c.sim ? "var(--sim)" : st === "done" ? "#1d1b18" : "#5b554d";
    s += `<g class="band" data-i="${i}" style="cursor:pointer"><rect x="${x0}" y="14" width="${w}" height="24" rx="6" fill="${fill}" ${c.sim ? 'stroke="var(--sim)" stroke-dasharray="4 3" stroke-width="1.5"' : ""}/>`;
    const label = w > 8 * (c.short.length + 4) ? `${i + 1} · ${c.short}` : w > 8 * (c.tiny.length + 4) ? `${i + 1} · ${c.tiny}` : `${i + 1}`;
    s += `<text x="${x0 + 8}" y="30" font-size="11.5" font-weight="600" fill="${ink}">${label}</text></g>`;
    const full = segLabel(c, i), seg = x0 + 1 >= labEnd + 6 &&       // narrow screens: chapter 9 keeps the years, on-chain bands drop the minute
      [full, full.replace(/[A-Z][a-z]{2} (?=\d{4})/g, ""), full.replace(/ \d\d:\d\d$/, "")].find(t => t.length * 5.9 <= Math.min(w - 3, Wd - x0 - 1));
    if (seg) { s += `<text x="${x0 + 1}" y="60" font-size="10.5" fill="#736c62">${seg}</text>`; labEnd = x0 + 1 + seg.length * 5.9; }
    if (i === ci) s += `<rect x="${x0}" y="36" width="${Math.max(0, X(T) - x0)}" height="2" fill="${c.sim ? "var(--sim)" : "#e2590f"}"/>`;
  });
  for (const o of OBS) s += o.det ? `<circle cx="${X(o.t)}" cy="44.5" r="3" fill="#e2590f" opacity="${o.t <= T ? 1 : .35}"/>`
    : `<circle cx="${X(o.t)}" cy="44.5" r="2.6" fill="#fff" stroke="#736c62" stroke-width="1.2" opacity="${o.t <= T ? 1 : .45}"/>`;
  for (const p of marsPl) s += `<rect x="${X(p.t) - 1.5}" y="41" width="3" height="6" rx="1" fill="var(--registry)"/>`;
  const c9 = CH.at(-1), X9 = (r) => (c9.x0 + (r - Z9_R0) / (Z9_R1 - Z9_R0) * (c9.x1 - c9.x0)) * Wd, now9 = z9Real(T);
  for (const z of z9Scenes) if (z.t >= Z9_R0) s += z.qualified ? `<circle cx="${X9(z.t)}" cy="44.5" r="3" fill="var(--money)" opacity="${inZ9(T) && z.t <= now9 ? 1 : .35}"/>`
    : `<circle cx="${X9(z.t)}" cy="44.5" r="2.6" fill="#fff" stroke="#736c62" stroke-width="1.2" opacity="${inZ9(T) && z.t <= now9 ? 1 : .45}"/>`;
  s += `<path d="M${X9(z9Relapse) - 4},48 L${X9(z9Relapse) + 4},48 L${X9(z9Relapse)},40 Z" fill="#e2590f"/>`;
  const xh = X(T), lab = z9On() ? `${dfmt(z9Real(T))} · ${Z9.source}` : dfmt(T, T > CH[5].from);
  const bw = lab.length * 6.2 + 14, bx = Math.max(bw / 2, Math.min(Wd - bw / 2, xh));
  s += `<line x1="${xh}" x2="${xh}" y1="11" y2="41" stroke="#e2590f" stroke-width="2.5"/><path d="M${xh - 5},10 L${xh + 5},10 L${xh},15 Z" fill="#e2590f"/>`;
  s += `<rect x="${bx - bw / 2}" y="-4" width="${bw}" height="16" rx="5" fill="#1d1b18"/><text x="${bx}" y="8" text-anchor="middle" font-size="10.5" fill="#fff" font-weight="600">${lab}</text>`;
  svg.innerHTML = s;
  const tr = $("track"); tr.setAttribute("aria-valuenow", Math.max(0, ci + 1)); tr.setAttribute("aria-valuemax", CH.length); tr.setAttribute("aria-valuetext", ci < 0 ? `Before chapter 1: the problem, ${dfmt(T)}` : `Chapter ${ci + 1} of ${CH.length}: ${CH[ci].short}, ${dfmt(T)}`);
}
/* charts share the timeline's chapter geometry, so a point sits under its chapter */
const bandLines = (Xs, top, bottom) => CH.slice(1).map(c => `<line x1="${Xs(c.from)}" x2="${Xs(c.from)}" y1="${top}" y2="${bottom}" stroke="#efe9df"/>`).join("");
function chartRate() {
  if (z9On()) return chartZ9Rate();
  const svg = $("c1"), Wd = svg.clientWidth || 300, H = svg.clientHeight || 110, pad = { l: 34, r: 6, t: 16, b: 14 }, inset = 7;
  const Xs = (t) => pad.l + inset + xOf(t) * (Wd - pad.l - inset - pad.r);   // the first marker sits clear of the axis labels
  const ymax = 10500, Y = (v) => pad.t + (1 - Math.sqrt(v / ymax)) * (H - pad.t - pad.b);
  let s = bandLines(Xs, pad.t, H - pad.b);
  for (const v of [1000, 2500, 5000, 10000]) s += `<line x1="${pad.l}" x2="${Wd - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eee8de"/><text x="${pad.l - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="10.5" fill="#736c62">${v / 1000}k</text>`;
  for (const [v, lab] of [[S.policy.tierMinKgH[1], "tier 2"], [S.policy.tierMinKgH[0], "tier 1"]]) s += `<line x1="${pad.l}" x2="${Wd - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#157a42" stroke-dasharray="3 3" opacity=".7"/><text x="${Wd - pad.r}" y="${Y(v) - 2}" text-anchor="end" font-size="10.5" fill="#157a42">${lab}</text>`;
  for (const p of plumes.filter(p => p.rate && p.unc)) s += `<line x1="${Xs(p.t)}" x2="${Xs(p.t)}" y1="${Y(p.rate + p.unc)}" y2="${Y(Math.max(1, p.rate - p.unc))}" stroke="#e2590f" stroke-width="1.2" opacity="${p.t <= T ? .45 : .12}"/>`;
  for (const p of plumes.filter(p => p.rate)) s += `<circle cx="${Xs(p.t)}" cy="${Y(p.rate)}" r="${p.id === lead.id ? 5 : 3.2}" fill="${p.id === lead.id ? "#ffd27a" : "#e2590f"}" stroke="${p.id === lead.id ? "#c2410c" : "#fff"}" stroke-width="1.2" opacity="${p.t <= T ? 1 : .2}"/>`;
  for (const p of marsPl) s += `<circle cx="${Xs(p.t)}" cy="${Y(p.rate)}" r="4.2" fill="none" stroke="var(--registry)" stroke-width="2" opacity="${p.t <= T ? 1 : .25}"/>`;
  s += `<line x1="${Xs(T)}" x2="${Xs(T)}" y1="${pad.t}" y2="${H - pad.b}" stroke="#1d1b18" stroke-width="1.2"/>`;
  s += `<g font-size="10.5"><circle cx="${pad.l + 4}" cy="6" r="3.2" fill="#e2590f"/><text x="${pad.l + 11}" y="9.5" fill="#5b554d">Carbon Mapper</text><circle cx="${pad.l + 104}" cy="6" r="3.6" fill="none" stroke="var(--registry)" stroke-width="2"/><text x="${pad.l + 111}" y="9.5" fill="#5b554d">MARS</text><text x="${Wd - pad.r}" y="9.5" text-anchor="end" fill="#736c62" data-yield="1">±1σ</text></g>`;
  svg.innerHTML = s;
}
function chartZ9Rate() {
  const svg = $("c1"), Wd = svg.clientWidth || 300, H = svg.clientHeight || 110, pad = { l: 34, r: 6, t: 16, b: 14 };
  const pts = Z9.plumes.filter(q => q.rate).map(q => ({ t: Date.parse(q.t + "T12:00:00Z"), rate: q.rate }));
  const t0 = Math.min(...pts.map(q => q.t)), t1 = Z9_R1, Xs = (t) => pad.l + (t - t0) / (t1 - t0) * (Wd - pad.l - pad.r);
  const ymax = 4000, Y = (v) => pad.t + (1 - Math.sqrt(Math.min(v, ymax) / ymax)) * (H - pad.t - pad.b);
  let s = `<rect x="${Xs(Z9_R0)}" y="${pad.t}" width="${Math.max(0, Xs(Math.min(z9Real(T), Z9_R1)) - Xs(Z9_R0))}" height="${H - pad.t - pad.b}" fill="var(--money-soft)"/>`;
  for (const v of [500, 1000, 2500]) s += `<line x1="${pad.l}" x2="${Wd - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eee8de"/><text x="${pad.l - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="10.5" fill="#736c62">${v >= 1000 ? v / 1000 + "k" : v}</text>`;
  for (const q of pts) s += `<circle cx="${Xs(q.t)}" cy="${Y(q.rate)}" r="3.2" fill="#e2590f" stroke="#fff"/>`;
  s += `<circle cx="${Xs(z9Relapse)}" cy="${Y(Z9.relapse.rateKgH)}" r="5" fill="#ffd27a" stroke="#c2410c" stroke-width="1.4"/>`;
  s += `<g font-size="10.5"><text x="${pad.l}" y="10" fill="#5b554d" data-short="green: claim open">${Z9.source} plumes · green: claim open</text></g>`;
  svg.innerHTML = s;
}
function chartZ9Passes() {
  $("c2t").textContent = "Clean passes · " + Z9.source; $("c2u").textContent = "earn-back steps";
  const svg = $("c2"), Wd = svg.clientWidth || 300, H = svg.clientHeight || 110, pad = { l: 22, r: 8, t: 10, b: 12 };
  const Xs = (t) => pad.l + (t - Z9_R0) / (Z9_R1 - Z9_R0) * (Wd - pad.l - pad.r), now = z9Real(T);
  const ymax = Z9C.installments.at(-1)[0] + 1, Y = (v) => pad.t + (1 - v / ymax) * (H - pad.t - pad.b);
  let d = `M${pad.l},${Y(0)}`, n = 0;
  for (const z of z9Scenes.filter(z => z.qualified && !z.afterRelapse && z.t <= now)) d += `H${Xs(z.t)}V${Y(++n)}`;
  d += `H${Xs(Math.min(now, z9Relapse))}`;
  let s = Z9C.installments.map(([k, bp]) => `<line x1="${pad.l}" x2="${Wd - pad.r}" y1="${Y(k)}" y2="${Y(k)}" stroke="#157a42" stroke-dasharray="3 3" opacity=".6"/><text x="${pad.l - 4}" y="${Y(k) + 3}" text-anchor="end" font-size="10" fill="#157a42">${k}</text><text x="${Wd - pad.r}" y="${Y(k) - 2}" text-anchor="end" font-size="10" fill="#157a42">+${bp / 100} %</text>`).join("");
  s += `<path d="${d}" fill="none" stroke="var(--money)" stroke-width="2.2"/>`;
  if (now >= z9Relapse) s += `<line x1="${Xs(z9Relapse)}" x2="${Xs(z9Relapse)}" y1="${pad.t}" y2="${Y(0)}" stroke="#c33a2c" stroke-dasharray="3 3"/>${(() => {   // the label stays inside the chart: left of the line, else right of it, longest wording that fits
    const x = Xs(z9Relapse), w = (t) => t.length * 5.7;
    for (const lab of ["relapse: unpaid part forfeited", "relapse: forfeited", "relapse"]) {
      if (x - 4 - w(lab) >= 0) return `<text x="${x - 4}" y="${pad.t + 8}" text-anchor="end" font-size="10.5" fill="#c33a2c">${lab}</text>`;
      if (x + 4 + w(lab) <= Wd) return `<text x="${x + 4}" y="${pad.t + 8}" font-size="10.5" fill="#c33a2c">${lab}</text>`;
    }
    return ""; })()}`;
  svg.innerHTML = s;
}
function chartRecords() {
  if (z9On()) return chartZ9Passes();
  $("c2t").textContent = "Two records, one facility"; $("c2u").textContent = "detections";
  const svg = $("c2"), Wd = svg.clientWidth || 300, H = svg.clientHeight || 110, pad = { l: 8, r: 8, t: 10, b: 12 };
  const Xs = (t) => pad.l + xOf(t) * (Wd - pad.l - pad.r);
  const cmDays = OBS.filter(o => o.det).map(o => o.t);     // detections of the source 0.2 km away (same basis as the card)
  const ymax = cmDays.length + 1, Y = (v) => pad.t + (1 - v / ymax) * (H - pad.t - pad.b);
  const step = (ts, upto) => { let d = `M${pad.l},${Y(0)}`, n = 0; for (const t of ts) { if (t > upto) break; d += `H${Xs(t)}V${Y(++n)}`; } return d + `H${Xs(Math.min(upto, T2))}`; };
  let s = bandLines(Xs, pad.t, Y(0)) + `<line x1="${pad.l}" x2="${Wd - pad.r}" y1="${Y(0)}" y2="${Y(0)}" stroke="#e6dfd3"/>`;
  s += `<path d="${step(cmDays, T)}" fill="none" stroke="#e2590f" stroke-width="2.2"/><path d="${step(marsPl.map(p => p.t), T)}" fill="none" stroke="var(--registry)" stroke-width="2.2"/>`;
  const xm = Xs(marsLast.t);
  s += `<line x1="${xm}" x2="${xm}" y1="${pad.t}" y2="${Y(0)}" stroke="var(--registry)" stroke-dasharray="2 3"/><text x="${xm + 4}" y="${Y(0) - 4}" font-size="10.5" fill="var(--registry)" data-yield="1" data-alt-y="${pad.t + 8}" data-short="MARS: “${S.mars.persistency}” →">MARS: “${S.mars.persistency}” from here →</text>`;
  const nC = cmDays.filter(t => t <= T).length, nM = marsPl.filter(p => p.t <= T).length;
  s += `<text x="${Wd - pad.r}" y="${Y(nC) - 4}" text-anchor="end" font-size="11" font-weight="600" fill="#c2410c">${nC} Carbon Mapper</text>`;
  const yM2 = Math.abs(Y(nC) - Y(nM)) < 14 ? Y(nM) + 13 : Y(nM) - 4;
  s += `<text x="${Wd - pad.r}" y="${yM2}" text-anchor="end" font-size="11" font-weight="600" fill="var(--registry)">${nM} MARS</text>`;
  svg.innerHTML = s;
}
function chartMoney() {
  const svg = $("c3"), Wd = svg.clientWidth || 300, H = svg.clientHeight || 110;
  $("moneysrc").textContent = z9On() ? `${Z9.replayA.chain.replace(/ \(.*\)$/, "")} replay · test ETH` : "Sepolia test ETH";
  const now9 = z9Real(T), eth9 = (w) => Number(BigInt(w)) / 1e18, cnt9 = z9Scenes.filter(z => z.qualified && !z.afterRelapse && z.t <= now9).length;
  const rows = z9On() ? [["Rebate (DZA leak)", eth9(z9Sp.openedByBuyerWei), true, "#1d1b18"], ["Back to the seller", eth9(z9Sp.releasedWei) * Math.min(1, Z9C.installments.filter(([k]) => cnt9 >= k).reduce((a, [, bp]) => a + bp, 0) / z9Out.paidBp), cnt9 >= Z9C.installments[0][0], "#157a42"], ["Forfeited", eth9(z9Sp.forfeitedWei), now9 >= z9Relapse, "#c33a2c"]] : [["Seller bond locked", bondEth, after("start"), "#1d1b18"], ["Rebate to buyer", rebateEth, after("paid"), "#157a42"], ["Proposer bond back", proposerEth, after("paid"), "#2457d6"]];
  const max = z9On() ? eth9(z9Sp.openedByBuyerWei) : bondEth, rh = (H - 8) / 3;
  const wide = Wd - 186 >= 48, bx = wide ? 118 : 0, bw = wide ? Wd - 186 : Wd;   // narrow chart: each bar runs full width under its label
  svg.innerHTML = rows.map(([l, v, on, c], i) => {
    const ty = wide ? 8 + i * rh + rh / 2 : 4 + i * rh + rh / 2 - 3, by = wide ? 4 + i * rh + rh / 2 - 6 : ty + 4;
    return `<text x="0" y="${ty}" font-size="11" fill="#5b554d">${l}</text>
    <rect x="${bx}" y="${by}" width="${bw}" height="${wide ? 10 : 8}" rx="3" fill="#f2eee7"/>
    <rect x="${bx}" y="${by}" width="${on ? Math.max(3, bw * v / max) : 0}" height="${wide ? 10 : 8}" rx="3" fill="${c}"/>
    <text x="${Wd}" y="${ty}" text-anchor="end" font-size="11" font-weight="600" fill="${on ? c : "#9b9285"}">${on ? +v.toFixed(6) : "—"}</text>`;
  }).join("");
}

/* ---------- "where on Earth" locator for the opening chapter (CARTO light tiles, z3) ---------- */
(function buildWhere() {
  const z = 5, n = 2 ** z, X = ((FAC[0] + 180) / 360) * n * 256;
  const la = FAC[1] * Math.PI / 180, Y = (1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2 * n * 256;
  const tx0 = Math.floor(X / 256) - 1, ty0 = Math.floor(Y / 256) - 1, bw = 220, bh = 118;
  const ox = bw / 2 - (X - tx0 * 256), oy = bh / 2 - (Y - ty0 * 256);
  let h = "";
  for (let dx = 0; dx < 3; dx++) for (let dy = 0; dy < 3; dy++) h += `<img alt="" src="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${ty0 + dy}/${tx0 + dx}" style="left:${ox + dx * 256}px;top:${oy + dy * 256}px">`;
  $("wm").innerHTML = h + `<span class="wd" style="left:${bw / 2}px;top:${bh / 2}px"></span><span style="position:absolute;left:8px;top:8px;font:600 10.5px Inter;color:#e8f1f7;text-shadow:0 1px 2px #000">Caspian Sea</span><span style="position:absolute;left:${bw / 2 + 12}px;top:${bh / 2 - 7}px;font:600 10.5px Inter;color:#fff;text-shadow:0 1px 2px #000">TKM_S_276</span>`;
  $("wll").textContent = `${FAC[1].toFixed(4)}° N, ${FAC[0].toFixed(4)}° E`;
})();

const TAMPER_RATE = S.tamper.to;   // the Sepolia run's tamper case (the label pipeline/readme_testnet.py writes for reject_tamper)
const tierOf = (r) => S.policy.tierMinKgH.findIndex(m => r >= m);
const PREMISE = "Two-sided: a confirmed leak pays the buyer; clean passes earn the seller money back.";
let tamperOn = false;
$("nsat").textContent = SATS.length; $("ntx").textContent = S.sepolia.steps.length;
// map tiles come from Esri, AWS and Carbon Mapper: if none has arrived after 15 s, say so instead of showing an empty
// map; the note goes away as soon as an imagery tile arrives (a slow network must not leave it over a working map)
let imgTiles = false, tileNote = null;
function tileCheck() {
  if (imgTiles || tileNote) return;
  mapLoading?.remove(); mapLoading = null;
  tileNote = document.createElement("div"); tileNote.className = "offline";
  tileNote.textContent = "Map tiles could not load (they need internet). The story, numbers and contract view still work.";
  $("mapwrap").appendChild(tileNote);
}
map.on("sourcedata", (e) => { if (e.sourceId === "img" && e.tile) { imgTiles = true; tileNote?.remove(); tileNote = null; loaded(); } });   // the first imagery tile also ends "Loading satellite map…"
setTimeout(tileCheck, 15000);
if (q_debug()) {
  const d = document.createElement("div"); d.className = "debug";
  const rows = [
    ["data/story.json", "scripts/build_story_data.py ← app/data/app.json, data/derived/cases/TKM_S_276*.json, cm_timelines.json, stake_band.json, testnet_run.json, sepolia_txs.json, verification_time.json, pipeline/readme_testnet.py"],
    ["context", "data/derived/story/: cm_scene_footprints_tkm.geojson, weather_tkm.json, osm_tkm.geojson, wind_convention.json, orbits_tkm.json (re-fetch: scripts/story/fetch_*.py)"],
    ["count basis", `cases/TKM_S_276.json post_mars_last_plume: ${S.headline.n_cm_detected}/${S.headline.n_cm_passes} over ${S.headline.cm_sources.length} sources within ${S.headline.radius_km} km`],
    ["stake", `stake_band.json: ${S.stake.detected}/${S.stake.passes}, low ${S.stake.kt_ch4_per_yr.low} kt CH₄/yr`],
    ["Sepolia", `${S.sepolia.steps.length} steps · covenant ${S.sepolia.covenant.slice(0, 10)}… · evidenceHash ${S.sepolia.evidenceHash.slice(0, 10)}…`],
    ["chapter 9", `${Z9.source}: ${Z9.replayA.label}; ${z9Out.counted} counted, paid ${z9Out.paidBp} bp, forfeited ${z9Out.forfeitedBp} bp`],
  ];
  d.innerHTML = `<b>Data provenance</b>` + rows.map(([k, v]) => `<div><span>${k}</span>${v}</div>`).join("");
  document.body.appendChild(d);
}
function q_debug() { return new URLSearchParams(location.search).has("debug"); }
/* ---------- Demo story panel ---------- */
function fitStory() {      // a long chapter steps its type down so the button and the dots stay in view on a laptop screen
  const el = $("story");
  el.classList.remove("fit1", "fit2");
  for (const k of ["fit1", "fit2"]) { if (el.scrollHeight <= el.clientHeight + 1) break; el.classList.add(k); }
}
const endCard = () => view === "verify" && !prologue && chOf(T) === CH.length - 1;   // Verify after the last chapter: the story's end card
function storyPanel() {
  const ci = chOf(T), c = CH[ci];
  const el = $("story");
  el.classList.toggle("simch", !!c.sim);
  if (endCard()) {
    el.innerHTML = `<div class="kick"><span>Demo story · chapter ${ci + 1} of ${CH.length} · your turn</span>
        <span class="nav"><button id="sprev" aria-label="Back to chapter ${ci + 1}">←</button></span></div>
      <div class="premise">${PREMISE}</div><h2>Your turn: check the ${S.facility.country} case yourself.</h2>
      <p>${S.case}, the ${S.facility.sourceType.toLowerCase().replace(/s$/, "")} from chapters 1–${ci}: ${V.steps.length} steps on public sources, with a stopwatch on the right. The script does the same check in ${V.toolMedianSeconds} s.</p>
      <div class="dots">${CH.map((x, i) => `<button class="${i < ci ? "done" : i === ci ? "now" : ""} ${x.sim ? "sim" : ""}" data-i="${i}" aria-label="Chapter ${i + 1}: ${x.short}"></button>`).join("")}</div>`;
    $("sprev").onclick = () => goChapter(ci);
    el.querySelector(".dots").onclick = (e) => { const i = e.target.dataset.i; if (i !== undefined) goChapter(+i); };
    return;
  }
  if (prologue) {
    el.classList.remove("simch");
    el.innerHTML = `<div class="kick"><span>Demo story · the problem</span><span class="nav"><button id="snext" aria-label="Start chapter 1">→</button></span></div>
      <div class="premise">${PREMISE}</div><h2>${PRO.head}</h2><p>${PRO.body}</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn yourturn" id="startstory">▶ Watch the story</button><button class="btn ghost" id="pverify">Check it yourself</button><button class="btn ghost" id="pcontract">Open the contract</button></div>`;
    $("pverify").onclick = () => { prologue = false; T = CH.at(-1).at; setView("verify"); };
    $("pcontract").onclick = () => { prologue = false; T = CH.at(-1).at; setView("contract"); };
    $("snext").onclick = () => goChapter(0);
    $("startstory").onclick = () => { goChapter(0); $("play").click(); };
    return;
  }
  el.innerHTML = `<div class="kick"><span>Demo story · chapter ${ci + 1} of ${CH.length}</span>
      <span class="nav"><button id="sprev" aria-label="Previous chapter">←</button><button id="snext" aria-label="Next chapter">→</button></span></div>
    <div class="premise">${PREMISE}</div><h2>${c.head}</h2><p>${c.body}</p>
    ${c.tamper && view === "contract" ? `<button class="btn ghost" id="tamperb" style="align-self:flex-start">${tamperOn ? "← Back to the honest proposal" : `Try to cheat: edit ${fmt.format(S.tamper.from)} to ${fmt.format(S.tamper.to)} kg/h →`}</button>` : ""}
    ${ci === CH.length - 1 ? `<button class="btn yourturn" id="yourturn">Your turn: check it yourself →</button>` : ""}
    <div class="dots">${CH.map((x, i) => `<button class="${i < ci ? "done" : i === ci ? "now" : ""} ${x.sim ? "sim" : ""}" data-i="${i}" aria-label="Chapter ${i + 1}: ${x.short}"></button>`).join("")}</div>`;
  $("sprev").onclick = () => goChapter(ci - 1);
  $("snext").onclick = () => goChapter(ci + 1);
  el.querySelector(".dots").onclick = (e) => { const i = e.target.dataset.i; if (i !== undefined) goChapter(+i); };
  const yt = $("yourturn"); if (yt) yt.onclick = () => { stop(); setView("verify"); };
  const tb = $("tamperb"); if (tb) tb.onclick = () => { tamperOn = !tamperOn; stop(); update(); };
}

let lastCase = "";
function applyCase() {      // chapter 9 moves the map to the second site
  const dz = inZ9(T) && !prologue && view !== "verify" && view !== "satellites", tag = dz ? "dza" : "tkm";   // Verify and the camera are the TKM case
  document.documentElement.classList.toggle("case-dza", dz);
  $("casepill").innerHTML = dz ? `${Z9.source} <span class="muted">· ${Z9.country} · second site</span>` : `TKM_S_276 <span class="muted">· gas pipeline · Turkmenistan</span>`;
  for (const id of ["z9-ring", "z9-pl"]) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", dz ? "visible" : "none");
  for (const id of ["pl", "pl-halo", "fac-fill", "fac-line", "osm-road", "osm-pipe"]) if (dz && map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
  if (ch4El && dz) ch4El.style.visibility = "hidden";
  if (dz) $("sky").style.visibility = "hidden";
  if (tag !== lastCase) {
    const first = !lastCase;
    lastCase = tag;
    const go = (cam, ms) => mapReady ? map.easeTo({ ...cam, duration: RM ? 0 : ms }) : map.jumpTo(cam);   // easing before the terrain loads throws inside the map library
    if (dz) { const k = Math.min(1, map.getContainer().clientWidth / 900);   // a narrow map sits closer to the site and pulls back a little
      go({ center: [Z9.mars.facility[1] + 0.004 * k, Z9.mars.facility[0] + 0.012 * k], zoom: 13.2 - (1 - k) * 0.8, pitch: 58, bearing: -20 }, 1600); }
    else if (!first && view !== "verify") go(CAMS[view], 1400);
  }
}
/* one failing card must not blank the page: it shows a short notice and logs the error */
function safe(name, fn, el) {
  try { return fn(); } catch (e) {
    console.error(`[${name}]`, e);
    if (el) el.innerHTML = `<div class="err">${name} could not render: ${String(e.message).replace(/[<>&]/g, "")}</div>`;
  }
}
function update() {
  if (map.getLayer("pl")) { map.setFilter("pl", ["<=", ["get", "t"], T]); map.setFilter("pl-halo", ["<=", ["get", "t"], T]); }
  try { placeCH4(); } catch (e) {}
  const ll = $("leadlbl"); if (ll) ll.style.display = T >= lead.t && view === "incident" ? "block" : "none";
  $("where").classList.toggle("on", view === "incident" && (prologue || chOf(T) === 0));
  safe("timeline", trackSvg, $("tracksvg")); safe("rate chart", chartRate, $("c1")); safe("records chart", chartRecords, $("c2"));
  safe("money chart", chartMoney, $("c3"));
  safe("story", () => { storyPanel(); fitStory(); }, $("story")); safe("side panel", renderSide, $("side"));
  document.querySelectorAll(".chart h5").forEach(t => (t.title = [...t.children].map(c => c.textContent).join(" · ")));   // a cut title stays readable on hover
  for (const u of document.querySelectorAll(".chart h5 span:not(.t)")) { u.style.visibility = ""; if (u.scrollWidth > u.clientWidth + 1) u.style.visibility = "hidden"; }
  for (const id of ["c1", "c2", "c3"]) try { fitSvgText($(id)); } catch (e) {}
  safe("layers", applyLayers); safe("case", applyCase); syncUrl();
  if (view === "contract") safe("contract graph", drawFlow, $("flow"));
  try { drawSky(); } catch (e) {}
}

/* a chart caption that would run past its chart takes its short wording, then an ellipsis; the full text stays in a tooltip */
function fitSvgText(svg) {
  const Wd = svg.clientWidth;
  if (!Wd) return;
  const texts = [...svg.querySelectorAll("text")], tip = (t, full) => t.insertAdjacentHTML("afterbegin", `<title>${full.replace(/[<>&]/g, "")}</title>`);
  for (const t of texts.filter(t => !t.dataset.yield)) {
    const x = +t.getAttribute("x") || 0, end = t.getAttribute("text-anchor") === "end", room = (end ? x : Wd - x) - 2;
    if (t.getComputedTextLength() <= room) continue;
    const full = t.textContent;
    if (t.dataset.short) t.textContent = t.dataset.short;
    while (t.getComputedTextLength() > room && t.textContent.length > 4) t.textContent = t.textContent.slice(0, -2).trimEnd() + "…";
    tip(t, full);
  }
  const placed = texts.filter(t => !t.dataset.yield).map(t => t.getBBox());
  const gap = 24, hits = (a, b) => a.x < b.x + b.width + gap && b.x < a.x + a.width + gap && a.y < b.y + b.height - .5 && b.y < a.y + a.height - .5;   // a yielding label keeps a clear gap, so two labels never read as one line
  for (const t of texts.filter(t => t.dataset.yield)) {
    const full = t.textContent, y0 = t.getAttribute("y");
    const tries = [y0, t.dataset.altY].filter(Boolean).flatMap(y => [full, t.dataset.short].filter(Boolean).map(w => [y, w]));
    const fit = tries.find(([y, w]) => {
      t.setAttribute("y", y); t.textContent = w;
      const b = t.getBBox();
      return b.x >= -.5 && b.x + b.width <= Wd + .5 && !placed.some(q => hits(b, q));
    });
    if (!fit) { t.style.display = "none"; continue; }   // no wording fits beside its neighbours: left out rather than drawn over them
    placed.push(t.getBBox());
    if (fit[1] !== full) tip(t, full);
  }
}
/* ---------- shareable state: the hash always names the chapter and view on screen ---------- */
function syncUrl() {
  const h = prologue ? "#prologue" : `#ch=${chOf(T) + 1}&view=${view}`;
  if (location.hash !== h) history.replaceState(null, "", location.pathname + location.search + h);
}
window.addEventListener("hashchange", () => {
  const hq = new URLSearchParams(location.hash.slice(1)), i = +hq.get("ch") - 1, v = hq.get("view");
  if (location.hash === "#prologue") { stop(); prologue = true; T = PRO.T; return setView(PRO.view); }
  if (!(i >= 0 && i < CH.length)) return syncUrl();       // unknown hash: put the true one back
  stop(); prologue = false; tamperOn = false; T = CH[i].at; lastCh = i;
  setView(["incident", "satellites", "contract", "verify"].includes(v) ? v : CH[i].view);
});
/* ---------- chapters drive the view; the tabs still let you explore ---------- */
let lastCh = -1;
function goChapter(i, keepPlaying) {
  if (i < 0 || i >= CH.length) return;
  if (!keepPlaying) stop();
  prologue = false; tamperOn = false;
  T = CH[i].at; lastCh = i;
  if (CH[i].view !== view) setView(CH[i].view); else update();
}

/* track: click a band, drag to scrub, keys move by chapter */
const track = $("track");
let drag = false;
const seek = (ev) => {
  const r = track.getBoundingClientRect(); T = tOf(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)));
  const ci = chOf(T);
  if (ci !== lastCh) { lastCh = ci; if (CH[ci].view !== view) return setView(CH[ci].view); }   // setView redraws
  update();
};
let downX = 0, moved = false;
track.addEventListener("pointerdown", (e) => { drag = true; moved = false; downX = e.clientX; stop(); prologue = false; seek(e); track.setPointerCapture(e.pointerId); });
track.addEventListener("pointermove", (e) => { if (!drag) return; if (Math.abs(e.clientX - downX) > 4) moved = true; seek(e); });
track.addEventListener("pointerup", () => { drag = false; if (!moved) goChapter(chOf(T)); });   // a click lands on the chapter's key moment, a drag scrubs
track.addEventListener("keydown", (e) => {
  const ci = chOf(T);
  if (prologue && e.key === "ArrowRight") { goChapter(0); e.preventDefault(); return; }
  if (e.key === "ArrowRight") goChapter(ci + 1); else if (e.key === "ArrowLeft") goChapter(endCard() ? ci : ci - 1);
  else if (e.key === "Home") goChapter(0); else if (e.key === "End") goChapter(CH.length - 1); else return;
  e.preventDefault();
});

/* play: inside each chapter move to its key moment, hold so the text can be read, then go on */
let raf = null, phase = "move", phaseStart = 0, fromT = 0;
const MOVE_MS = 2600, HOLD_MS = 4200;
function tick(ts) {
  const ci = chOf(T), c = CH[ci];
  if (!phaseStart) { phaseStart = ts; fromT = T; }
  const k = (ts - phaseStart) / (phase === "move" ? MOVE_MS : HOLD_MS);
  if (phase === "move") {
    T = fromT + (c.at - fromT) * Math.min(1, k); update();
    if (k >= 1) { phase = "hold"; phaseStart = ts; }
  } else if (k >= 1) {
    if (ci === CH.length - 1) return stop();
    phase = "move"; phaseStart = 0; goChapter(ci + 1, true); T = CH[ci + 1].from; update();
  }
  raf = requestAnimationFrame(tick);
}
function stop() { cancelAnimationFrame(raf); raf = null; phase = "move"; phaseStart = 0; $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play the story"); }
$("play").onclick = () => {
  if (raf) return stop();
  if (prologue) { prologue = false; T = CH[0].from; lastCh = 0; }
  if (chOf(T) === CH.length - 1 && T >= CH.at(-1).at) { T = CH[0].from; setView(CH[0].view); }
  $("play").textContent = "❚❚"; $("play").setAttribute("aria-label", "Pause");
  phase = T >= CH[chOf(T)].at ? "hold" : "move"; phaseStart = 0; raf = requestAnimationFrame(tick);
};
$("back").onclick = () => goChapter(0);
$("fwd").onclick = () => goChapter(prologue ? 0 : Math.min(CH.length - 1, chOf(T) + 1));   // the opening screen sits inside chapter 3's time span

/* views */
function setView(v) {
  if (v === "satellites" && inZ9(T) && !prologue) { T = CH[CH.length - 2].at; lastCh = CH.length - 2; }   // the camera is the TKM case (no scene tiles for the second site); Verify stays on the last chapter as its end card
  view = v; document.body.className = "view-" + v;
  document.querySelectorAll(".tab").forEach(b => { b.classList.toggle("on", b.dataset.view === v); b.setAttribute("aria-selected", b.dataset.view === v); });
  try { map.setTerrain(v === "verify" ? null : { source: "dem", exaggeration: 2.4 }); } catch (e) {}   // the verify view is a flat analytic map
  try {                                                                                                    // ... on a light basemap
    map.setLayoutProperty("light", "visibility", v === "verify" ? "visible" : "none");
    map.setLayoutProperty("img", "visibility", v === "verify" ? "none" : "visible");
    map.setPaintProperty("hs", "hillshade-exaggeration", v === "verify" ? 0.35 : 0.6);
  } catch (e) {}
  if (mapReady) map.easeTo({ ...CAMS[v], duration: RM ? 0 : 1400 }); else map.jumpTo(CAMS[v]);   // easing before the terrain loads throws inside the map library
  $("b3d").classList.toggle("on", CAMS[v].pitch > 0); $("b2d").classList.toggle("on", CAMS[v].pitch === 0);
  if (v === "satellites") drawCam(); else setFootprint();
  try { update(); } catch (e) {}
}
document.querySelectorAll(".tab").forEach(b => (b.onclick = () => setView(b.dataset.view)));
$("b2d").onclick = () => { map.easeTo({ pitch: 0, bearing: 0, duration: 900 }); $("b2d").classList.add("on"); $("b3d").classList.remove("on"); };
$("b3d").onclick = () => { map.easeTo({ pitch: CAMS[view].pitch || 60, bearing: -28, duration: 900 }); $("b3d").classList.add("on"); $("b2d").classList.remove("on"); };
const redrawCam = () => { if (view === "satellites") drawCam(); };   // the camera image and tag are laid out in pixels
window.addEventListener("resize", () => { update(); redrawCam(); });
new ResizeObserver(redrawCam).observe($("camimg"));   // ... and the card also changes size without a window resize (full view)
const VIEWS = ["incident", "satellites", "contract", "verify"];
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target === track) return;                                   // the timeline slider has its own keys
  const ci = chOf(T);
  if (e.key === " " && !e.target.closest("button, a")) { $("play").click(); e.preventDefault(); }
  else if (e.key === "ArrowRight") { prologue ? goChapter(0) : goChapter(ci + 1); e.preventDefault(); }
  else if (e.key === "ArrowLeft") { goChapter(endCard() ? ci : ci - 1); e.preventDefault(); }
  else if (/^[1-4]$/.test(e.key)) setView(VIEWS[+e.key - 1]);
  else if (e.key === "?") $("help").classList.toggle("on");
  else if (e.key === "Escape") $("help").classList.remove("on");
});
const q = new URLSearchParams(location.search);
const hq = new URLSearchParams(location.hash.slice(1));
for (const k of ["ch", "view"]) if (hq.get(k) && !q.get(k)) q.set(k, hq.get(k));
if (q.has("record")) {          // video capture: no cursor, the story plays itself from the prologue
  document.documentElement.classList.add("record");
  setTimeout(() => { if (!raf) $("play").click(); }, +(q.get("record") || 4000));
}
setTimeout(() => {
  if (q.get("ch")) { const i = +q.get("ch") - 1; T = CH[i].at; lastCh = i; setView(q.get("view") || CH[i].view); }
  else if (q.get("t") || q.get("view")) { if (q.get("t")) T = D(q.get("t")); setView(q.get("view") || CH[chOf(T)].view); }
  else { prologue = true; T = PRO.T; setView(PRO.view); }
}, 50);


/* a small handle for tests (scripts/tests) and for anyone checking the page from the console */
window.story = { CH, goChapter, setView, chOf, tileCheck,get T() { return T; }, get view() { return view; }, get prologue() { return prologue; } };
document.documentElement.dataset.storyReady = "1";
}
