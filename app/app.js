// MethaneClause — the page in three layers: the five-second view (who pays whom), the story on real data (TKM_S_276 penalty, DZA_S_012 earn-back), and the "Why you can trust it" drawer (every proof of the former Monday screen). Static: data/app.json (built by scripts/build_app_data.py) + live Sepolia reads.
// Every hash and signature check on this page runs in the browser: RFC 8785 (JCS) + SHA-256, EIP-712 ECDSA recovery
// (viem), ML-DSA-44 / FIPS 204 verification (@noble/post-quantum). Pinned versions, no build step.
import { createPublicClient, hashTypedData, http, recoverTypedDataAddress } from "https://cdn.jsdelivr.net/npm/viem@2.57.1/+esm";
import { ml_dsa44 } from "https://cdn.jsdelivr.net/npm/@noble/post-quantum@0.7.1/ml-dsa.js";  // published file; SRI in index.html

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k === "html") n.innerHTML = v; else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k.nodeType ? k : document.createTextNode(String(k)));
  return n;
};
const fmt = new Intl.NumberFormat("en-US");
const short = (h, a = 10, b = 4) => (h && h.length > a + b + 1 ? `${h.slice(0, a)}…${h.slice(-b)}` : h);
const ethOf = (wei) => {
  const w = BigInt(wei), whole = w / 10n ** 18n, frac = (w % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return `${whole}${frac ? "." + frac : ""} ETH`;
};
const day = (unix) => new Date(Number(unix) * 1000).toISOString().slice(0, 10);
const link = (href, text) => el("a", { href, target: "_blank", rel: "noopener" }, text);
const enc = new TextEncoder();

// ---------------------------------------------------------------- simulated = purple, dashed (one legend, no "[SIM]" text)
// Committed data and builder sentences carry the repository's "[SIM…]" labels; on this page each one becomes the
// legend's purple dashed mark (a plain [SIM]) or a purple dashed chip with its qualifier ([SIM clock], [SIM look-ahead],
// [SIM: chain clock advanced …]). Everything rendered later passes through the same rule (MutationObserver).
const SIM_RE = /\[SIM(?:([: ])\s*([^\]]*))?\]/g;
const SIM_Q = { clock: "pinned clock", signers: "simulated signers", keys: "with simulated keys" };
function simMark(q) {
  if (!q) return el("span", { class: "simk", role: "img", "aria-label": "simulated", title: "simulated (purple, dashed)" });
  return el("span", { class: "simq", title: "simulated" }, SIM_Q[q.trim()] || q.trim());
}
function desimText(node) {
  const t = node.nodeValue;
  SIM_RE.lastIndex = 0;
  if (!t || !SIM_RE.test(t)) return;
  const frag = document.createDocumentFragment();
  let last = 0, m;
  SIM_RE.lastIndex = 0;
  while ((m = SIM_RE.exec(t))) {
    let a = m.index, b = m.index + m[0].length;
    if (!m[2]) {  // a plain mark: drop one space next to it (the mark carries its own margin)
      if (t[a - 1] === " ") a--; else if (t[b] === " ") b++;
    }
    if (a > last) frag.append(t.slice(last, a));
    frag.append(simMark(m[2] || ""));
    last = b;
  }
  if (last < t.length) frag.append(t.slice(last));
  const marks = [...frag.querySelectorAll(".simk")];
  node.replaceWith(frag);
  marks.forEach(tidyMark);
}
// a mark that is the whole text of a <span class="sim"> takes the space before that span (no double spaces)
function tidyMark(mark) {
  let host = mark;
  while (host.parentNode?.classList?.contains("sim") && host.parentNode.childNodes.length === 1) host = host.parentNode;
  if (host === mark) return;
  const prev = host.previousSibling;
  if (prev?.nodeType === 3 && prev.nodeValue.endsWith(" ")) prev.nodeValue = prev.nodeValue.slice(0, -1);
}
function desim(root) {
  if (root.nodeType === 3) { desimText(root); return; }
  if (root.nodeType !== 1 || root.closest?.("script, style")) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), found = [];
  while (w.nextNode()) if (w.currentNode.nodeValue.includes("[SIM")) found.push(w.currentNode);
  found.forEach(desimText);
  for (const n of [root, ...root.querySelectorAll("[title], [aria-label]")]) {
    for (const a of ["title", "aria-label"]) {
      const v = n.getAttribute?.(a);
      if (v && v.includes("[SIM")) n.setAttribute(a, v.replace(SIM_RE, (_, __, q) => (q ? `simulated: ${q.trim()}` : "simulated")));
    }
  }
}
desim(document.body);
new MutationObserver((muts) => {
  for (const m of muts) {
    if (m.type === "characterData") desimText(m.target);
    else m.addedNodes.forEach(desim);
  }
}).observe(document.body, { childList: true, subtree: true, characterData: true });

// ---------------------------------------------------------------- RFC 8785 (JCS) + SHA-256
export function jcs(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(jcs).join(",") + "]";
  return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + jcs(v[k])).join(",") + "}";
}
async function sha256hex(text) {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return "0x" + [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
const hexBytes = (h) => Uint8Array.from(h.slice(2).match(/../g).map((x) => parseInt(x, 16)));
async function sha256bytes(bytes) {
  const d = await crypto.subtle.digest("SHA-256", bytes);
  return "0x" + [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
// The KeyRegistry record the demo read on chain (address from the Anvil deployment record, pqPubKeyHash from the
// registry): every signature check on this page compares with it, never with the signer field of the same entry.
let REG = {};
const regAddr = (org) => REG[org]?.address || "";
// readable witness names in prose ("Witness A"); check lists keep the raw organisation id in monospace beside it
const wname = (org) => (/^OBS_[A-Z]$/.test(org) ? `Witness ${org.slice(4)}` : org);
const wtag = (org) => `${wname(org)} <code>${org}</code>`;
// Pinned in the page's code, not read from data/app.json: the ML-DSA-44 context string every witness signs under
// (pipeline/observer/pq.py CONTEXT) and the KeyRegistry anchor the signature checks compare with (the registry the demo
// read and each witness's SHA-256 of its ML-DSA-44 public key). scripts/tests/test_page_data.py keeps them equal to the
// committed record; a data file that disagrees turns the self-check red, and a key that differs from the pin fails.
const PINNED = Object.freeze({
  mldsaContext: "MethaneClause/v1",
  registry: "0x5FbDB2315678afecb367f032d93F642f64180aa3",
  pqPubKeyHash: Object.freeze({
    OBS_A: "0xa77d19b75a58b39932f09bae95f581460844d99e3ec21a3a165500665ed99a4b",
    OBS_B: "0xb1304231290fe7a99217580669b7f0adfa432062d5fdaef18287cc90fd68d4ca",
  }),
});
const PQ_CTX = enc.encode(PINNED.mldsaContext);
const pqAnchored = async (org, pk) => {
  const h = await sha256bytes(hexBytes(pk));
  return !!REG[org] && h === REG[org].pqPubKeyHash && h === PINNED.pqPubKeyHash[org];
};
// what in data/app.json differs from the pins (empty = all equal)
function pinMismatches(d) {
  const bad = [], reg = d.hybrid.registeredKeys || {};
  const ctx = [...d.hybrid.entries, ...d.cleanPassDemo.entries].map((e) => e.mldsa44?.context);
  if (ctx.some((c) => c !== PINNED.mldsaContext)) bad.push("the ML-DSA-44 context of a signed entry");
  for (const [org, h] of Object.entries(PINNED.pqPubKeyHash)) {
    if (reg[org]?.pqPubKeyHash !== h) bad.push(`${org}'s anchored key hash`);
    if (reg[org]?.registry !== PINNED.registry) bad.push(`${org}'s registry`);
  }
  for (const org of Object.keys(reg)) if (!(org in PINNED.pqPubKeyHash)) bad.push("a witness the page does not pin");
  return bad;
}

// ---------------------------------------------------------------- EIP-712 (byte-compatible with Attestation.sol)
const TYPES = {
  Attestation: [
    { name: "covenantId", type: "bytes32" }, { name: "sourceId", type: "bytes32" },
    { name: "evidenceHash", type: "bytes32" }, { name: "modelHash", type: "bytes32" },
    { name: "emissionKgH", type: "uint32" }, { name: "uncertaintyKgH", type: "uint32" },
    { name: "detectedAt", type: "uint64" }, { name: "instrumentId", type: "uint8" },
    { name: "providerId", type: "bytes32" }, { name: "kind", type: "uint8" },
    { name: "nonce", type: "uint64" }, { name: "expiry", type: "uint64" },
  ],
};
const UINTS = new Set(["emissionKgH", "uncertaintyKgH", "detectedAt", "instrumentId", "kind", "nonce", "expiry"]);
const asMessage = (att) => Object.fromEntries(Object.entries(att).map(([k, v]) => [k, UINTS.has(k) ? BigInt(v) : v]));

// ---------------------------------------------------------------- checks list helper
function check(list, ok, html) {
  const li = el("li", { class: ok === null ? "info" : ok ? "pass" : "fail", html });
  list.append(li);
  return ok;
}

// ---------------------------------------------------------------- 1. sources + map
const ORDER = ["TKM_S_276", "DZA_S_012", "USA_S_077"];  // lead case first, then the linked case, then the control
let map, layer;
function drawMap(c, since) {
  if (!window.L) { $("map").textContent = "Map tiles unavailable offline; the source list on the right has every plume."; return; }
  if (!map) {
    map = L.map("map", { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 17,
      attribution: "© OpenStreetMap contributors · Data by Carbon Mapper® · UNEP IMEO MARS, CC BY-NC-SA 4.0",
    }).addTo(map);
    addLegend();
  }
  const m = c.mars;
  map.setView([m.lat, m.lon], 14);  // a view must exist before vector layers can compute their bounds
  if (layer) layer.remove();
  layer = L.layerGroup().addTo(map);
  const ring = L.circle([m.lat, m.lon], { radius: 1500, color: "#5d6870", weight: 1, dashArray: "4 4", fill: false });
  ring.addTo(layer);
  L.circleMarker([m.lat, m.lon], { radius: 8, color: "#1d2327", weight: 2, fillColor: "#ffffff", fillOpacity: 1 })
    .bindPopup(`<b>${c.source}</b> · UNEP MARS source point<br>${m.source_type}, ${m.country}<br>MARS: <b>${m.persistency_category}</b> (persistency ${m.persistency})<br>last MARS plume ${String(m.last_plume_date).slice(0, 10)}`)
    .addTo(layer);
  for (const p of c.cmPlumes) {
    if (p.lat == null) continue;
    const after = p.scene_timestamp.slice(0, 10) >= since;
    const r = 4 + Math.min(8, Math.sqrt((p.emission_auto || 0) / 100));
    L.circleMarker([p.lat, p.lon], {
      radius: r, weight: 1, color: after ? "#b45309" : "#7b868c", fillColor: after ? "#f0a35b" : "#c3c9cc", fillOpacity: 0.75,
    }).bindPopup(`Carbon Mapper plume<br><code>${p.plume_id}</code><br>${p.scene_timestamp.slice(0, 10)} · ${p.instrument}<br>${p.emission_auto == null ? "not quantified" : fmt.format(Math.round(p.emission_auto)) + " ± " + fmt.format(Math.round(p.emission_uncertainty_auto)) + " kg/h"}`)
      .addTo(layer);
  }
  map.invalidateSize();
  map.fitBounds(ring.getBounds(), { padding: [30, 30], maxZoom: 13 });
}

function addLegend() {
  const Legend = L.Control.extend({
    onAdd() {
      const div = L.DomUtil.create("div", "legend");
      div.innerHTML = '<span class="dot cm"></span>Carbon Mapper plume after the MARS label<br>'
        + '<span class="dot old"></span>earlier plume<br><span class="dot mars"></span>UNEP MARS source point<br>'
        + '<span class="ring"></span>1.5 km join radius';
      return div;
    },
  });
  new Legend({ position: "bottomleft" }).addTo(map);
}

function renderSources(d) {
  const j = d.join;
  $("join-line").textContent = `Frozen join of UNEP MARS × Carbon Mapper (window ${j.window}, ${j.radius_km} km): ${fmt.format(j.n_candidates)} of ${fmt.format(j.n_mars_target_sources)} MARS "absent" oil & gas sources had a Carbon Mapper detection inside MARS's own window. Three are covered by this demo covenant.`;
  const side = $("source-side");
  for (const name of ORDER) {
    const c = d.cases[name], m = c.mars, pm = c.postMars;
    const since = pm.since;
    const dets = c.cmPlumes.filter((p) => p.scene_timestamp.slice(0, 10) >= since && p.emission_auto != null);
    const box = el("div", { class: "src" });
    const btn = el("button", { class: "btn", type: "button" }, "Show on map");
    btn.addEventListener("click", () => drawMap(c, since));
    box.append(el("h3", {}, name,
      el("span", { class: "badge absent", title: "UNEP MARS persistency_category" }, `MARS: ${m.persistency_category}`),
      el("span", { class: pm.n_cm_detected ? "badge detect" : "badge" },
        `Carbon Mapper (sources ≤ ${pm.radius_km} km): ${pm.n_cm_detected}/${pm.n_cm_passes} passes with a plume since ${since}`), btn));
    box.append(el("p", { class: "sub" }, `${m.source_type}, ${m.country}. MARS's last plume: ${String(m.last_plume_date).slice(0, 10)}.`));
    if (dets.length) {
      const t = el("table", { class: "dets" },
        el("tr", {}, el("th", {}, "Date"), el("th", {}, "Instr."), el("th", { class: "num" }, "kg/h ± σ")));
      for (const p of dets.slice(-8).reverse()) {
        t.append(el("tr", {}, el("td", {}, p.scene_timestamp.slice(0, 10)), el("td", {}, p.instrument),
          el("td", { class: "num" }, `${fmt.format(Math.round(p.emission_auto))} ± ${fmt.format(Math.round(p.emission_uncertainty_auto))}`)));
      }
      box.append(t);
    } else {
      box.append(el("p", { class: "sub" }, "No quantified Carbon Mapper plume since the MARS label: this is the control case. Nothing is proposed, nothing moves."));
    }
    side.append(box);
  }
  const lead = d.cases.TKM_S_276;
  drawMap(lead, lead.postMars.since);
}

// ---------------------------------------------------------------- 2. covenant
function renderCovenant(d, live) {
  const s = d.sepolia, run = s.run, ex = s.explorer;
  const prop = run.steps.find((x) => x.step === "proposeEpisode");
  const lead = d.hybrid.entries[0].bundle;
  const cov = live?.covenant;
  const body = $("covenant-body");
  body.innerHTML = "";
  const stat = (lbl, val, cls = "") => el("div", { class: "stat" }, el("span", { class: "lbl" }, lbl),
    el("span", { class: "val " + cls }, val));
  body.append(
    stat("Seller [SIM]", link(`${ex}/address/${s.seller}`, short(s.seller)), "small"),
    stat("Buyer [SIM]", link(`${ex}/address/${s.buyer}`, short(s.buyer)), "small"),
    stat("Bond remaining" + (cov ? " (live)" : " (at creation)"),
      cov ? ethOf(cov.remaining) : ethOf(run.steps[0].bondWei)),
    stat("Rebate held / paid" + (cov ? " (live)" : ""), cov ? `${ethOf(cov.held)} / ${ethOf(cov.paid)}` : "see episodes"),
    stat("Quorum", `${d.policy.k} of ${d.policy.n} witness organisations`, "small"),
    stat("Windows", `appeal ${d.policy.challengeWindow / 3600} h · contest ${d.policy.contestWindow / 3600} h · link ${d.policy.linkWindow / 86400} days`, "small"),
    stat("Start (backdated [SIM]) → on chain since", `${day(run.covenantStart)} → ${day(run.createdAt)}`, "small"),
    stat("policyHash (committed before any evidence)", el("code", {}, short(run.policyHash, 14, 6)), "small"),
  );
  const list = el("div", { class: "status-list" });
  for (const name of ORDER) {
    const hit = name === "TKM_S_276" && prop;
    const st = live?.episode?.stateName || (hit ? "PROPOSED" : null);
    list.append(el("div", { class: "status" + (hit ? " hit" : "") }, el("strong", {}, name),
      hit
        ? el("span", {}, `Positive re-detection attested: ${lead.sceneTimestamp.slice(0, 10)} · ${fmt.format(lead.emissionKgH)} kg/h · episode ${prop.episodeId} `, el("span", { class: `state ${st}` }, st))
        : el("span", {}, `No positive re-detection attested since ${day(run.covenantStart)}`)));
  }
  body.append(list);
  const bss = d.model.bss_test_b;
  const pf = d.portfolio;
  if (pf) {
    const rj = pf.rejected?.[0];
    body.append(el("div", { class: "status", id: "portfolio-line" }, el("strong", {}, "Portfolio covenant (local chain) "),
      el("span", {}, pf.sentence, el("br"),
        `On chain: one covenant, root `, el("code", {}, short(pf.root)), `, bond ${ethOf(BigInt(pf.bondWei))} and pool ${ethOf(BigInt(pf.poolWei))} read back; an episode at TKM_S_276 is proven by its ${pf.proofSteps}-step path and CONFIRMED`,
        rj ? el("span", {}, `; a proof from the 3-facility set is refused: status 0, ${rj.revert}`) : "", ".")));
  }
  body.append(el("div", { class: "chips" },
    el("span", { class: "chip" }, `Bond sizing: the persistence rule is enough — the ML model did not beat it on held-out data (BSS ${bss > 0 ? "+" : ""}${bss})`),
    el("span", { class: "chip" }, `Bond = P90 annual rebate exposure: ${d.bond.recommended_bond_eth} ETH`),
    el("span", { class: "chip" }, `Lead-case stake, not abatement: ${d.stake.kt_ch4_per_yr.low} kt CH₄/yr, the low bound of the band`)));
}

// ---------------------------------------------------------------- 3. episodes
const REJECT_WHY = {
  reject_tamper: "bundle edited 2,548 → 548, signatures kept", reject_self_attestation: "the seller's key signs as a witness",
  reject_not_covered: "keyed by a Carbon Mapper twin, not the facility", reject_replay: "the same attestation twice",
};
// the clause's rebate tier of a rate: 1-based, the first tier whose minimum the rate reaches (policy.tierMinKgH, highest first)
const tierOf = (policy, kgH) => policy.tierMinKgH.findIndex((m) => kgH >= m) + 1;
// make demo's linked detection: the next Carbon Mapper scene at TKM_S_276 after the paid one, inside the link window
function linkedPlume(d) {
  const t0 = Date.parse(d.hybrid.entries[0].bundle.sceneTimestamp);
  const next = d.cases.TKM_S_276.cmPlumes.map((p) => Date.parse(p.scene_timestamp))
    .filter((t) => t > t0 && new Date(t).toISOString().slice(0, 10) !== new Date(t0).toISOString().slice(0, 10)
      && t - t0 <= d.policy.linkWindow * 1000).sort((a, b) => a - b)[0];
  return { day: new Date(next).toISOString().slice(0, 10), days: Math.round((next - t0) / 864e5) };
}
function renderEpisodes(d, live) {
  const s = d.sepolia, run = s.run;
  const prop = run.steps.find((x) => x.step === "proposeEpisode");
  const lead = d.hybrid.entries[0].bundle;
  const tiers = d.policy.tierAmountsWei;
  const t = el("table", { class: "ep" }, el("tr", {}, el("th", {}, "Episode"), el("th", {}, "State"),
    el("th", {}, "Attested rate"), el("th", {}, "Tier · rebate"), el("th", {}, "Evidence")));
  const st = live?.episode?.stateName || "PROPOSED";
  t.append(el("tr", {}, el("td", {}, `Sepolia #${prop.episodeId} · TKM_S_276 · 2 of 3 witnesses`),
    el("td", {}, el("span", { class: `state ${st}` }, st)),
    el("td", {}, `${fmt.format(lead.emissionKgH)} ± ${fmt.format(lead.uncertaintyKgH)} kg/h`),
    el("td", {}, `tier ${prop.tier} · ${ethOf(prop.rebateWei)}`),
    el("td", {}, link(prop.tx, "proposeEpisode tx"))));
  for (const r of run.steps.filter((x) => x.status === 0)) {
    t.append(el("tr", { class: "child" }, el("td", {}, `↳ rejected: ${REJECT_WHY[r.step] || r.step}`),
      el("td", {}, el("span", { class: "state REVERTED" }, "REVERTED")), el("td", {}, "—"),
      el("td", {}, `${r.reason}; bond unchanged`), el("td", {}, link(r.tx, "failed tx (status 0)"))));
  }
  const demo = d.demo, lk = linkedPlume(d);
  t.append(el("tr", {}, el("td", {}, "Local demo #1 · TKM_S_276 (", el("code", {}, "make demo"), ")"),
    el("td", {}, el("span", { class: "state CONFIRMED" }, "CONFIRMED")), el("td", {}, `${fmt.format(lead.emissionKgH)} kg/h`),
    el("td", {}, `tier ${tierOf(d.policy, lead.emissionKgH)} · ${demo.rebate_eth} ETH withdrawn by the buyer`), el("td", {}, el("code", {}, short(demo.evidenceHash)))));
  t.append(el("tr", { class: "child" }, el("td", {}, `↳ #2 · plume ${lk.days} days later (${lk.day})`),
    el("td", {}, el("span", { class: "state LINKED" }, "LINKED")), el("td", {}, "—"),
    el("td", {}, "recorded under #1, not paid twice"), el("td", {}, el("code", {}, short(demo.linkedEvidenceHash)))));
  const sb = s.sandbox;
  $("episode-body").append(t, el("p", { class: "sub" },
    "Judges can run the same loop with a fresh wallet in a separate sandbox deployment whose keys are published [SIM], registering their own wallet as a witness (org OBS_JUDGE) so one signature is theirs: ",
    link(`${s.explorer}/address/${sb.methaneCovenant}#code`, "sandbox MethaneCovenant"), " · ",
    link(`${s.explorer}/address/${s.methaneCovenant}#code`, "main MethaneCovenant (verified source)"), "."));
}

// ---------------------------------------------------------------- 4. tamper
const TAMPER_ACTIONS = {};  // A, B, C: the edit each tamper button makes; ?tamper=A|B|C runs one after the self-checks
let CHEAT_LABEL = "Try to cheat: shrink the signed leak";  // set by setupTamper from the signed record
const TAMPER_RATE = 548;  // the edited rate of tamper A, as sent to Ethereum Sepolia (the reject_tamper transaction)

// the failure moment above the tampers: edited rate → signatures fail → the bond does not move
function updateMoment(edited, allOk, from, to, sepoliaTamper) {
  const set = (i, cls, ...kids) => { const li = $(`moment-${i}`); li.className = cls; li.querySelector(".mt").replaceChildren(...kids); };
  if (!edited && allOk) {
    set(1, "", `The seller edits the signed leak rate (${fmt.format(from)} kg/h)`);
    set(2, "", "Both witnesses' signatures are checked in your browser");
    set(3, "", "The contract accepts only what the witnesses signed");
    return;
  }
  set(1, "hit", `The seller edits the signed leak rate: ${fmt.format(from)} → ${fmt.format(to)} kg/h`);
  set(2, allOk ? "" : "bad", allOk ? "Signatures still verify" : "Both witnesses' signatures fail (ECDSA and ML-DSA-44)");
  set(3, allOk ? "" : "bad", ...(allOk ? ["—"] : ["The contract refuses; the bond does not move",
    ...(sepoliaTamper ? [" — on Ethereum Sepolia: ", link(sepoliaTamper.tx, `reverted, ${sepoliaTamper.reason}`)] : [])]));
}
const BUNDLE_ORDER = ["plumeId", "sourceName", "cmSourceName", "instrument", "platform", "sceneTimestamp", "emissionKgH",
  "uncertaintyKgH", "emissionVersion", "processingSoftware", "publishedAt", "plumeLat", "plumeLon", "providerId",
  "providerContentSha256", "sourceId", "covenantId", "detectedAt", "schemaVersion"];
async function setupTamper(d) {
  const H = d.hybrid, entries = H.entries, original = entries[0].bundle;
  const domain = { name: "MethaneClause", version: "1", chainId: H.chainId, verifyingContract: H.verifyingContract };
  const sepoliaTamper = d.sepolia.run.steps.find((x) => x.step === "reject_tamper");
  const input = $("rate-input");
  input.value = original.emissionKgH;
  $("tamper-btn").textContent = `Tamper: ${fmt.format(original.emissionKgH)} → ${fmt.format(TAMPER_RATE)}`;
  CHEAT_LABEL = `Try to cheat: shrink the signed leak ${fmt.format(original.emissionKgH)} → ${fmt.format(TAMPER_RATE)} kg/h`;
  $("h-committed").textContent = entries[0].evidenceHash;

  async function evaluate() {
    const v = Number.parseInt(input.value, 10);
    const bundle = { ...original, emissionKgH: Number.isFinite(v) ? v : original.emissionKgH };
    const edited = bundle.emissionKgH !== original.emissionKgH;
    const tbl = $("bundle-table");
    tbl.innerHTML = "";
    for (const k of BUNDLE_ORDER.filter((k) => k in bundle)) {
      const cls = [k === "emissionKgH" && edited ? "edited" : "", k === "emissionKgH" ? "" : "extra"].filter(Boolean).join(" ");
      tbl.append(el("tr", { class: cls }, el("td", {}, k),
        el("td", {}, typeof bundle[k] === "number" ? String(bundle[k]) : bundle[k])));
    }
    const canon = jcs(bundle);
    const h = await sha256hex(canon);
    const same = h === entries[0].evidenceHash;
    $("h-now").textContent = h;
    $("h-now").className = same ? "" : "bad";
    const list = $("sig-checks");
    list.innerHTML = "";
    let allOk = check(list, same, same ? "Canonical bundle hashes to the committed evidenceHash"
      : "Hash changed: the edited record is not what the witnesses signed");
    for (const e of entries) {
      // what an attacker would submit: the edited value and the new hash, with the witnesses' signatures kept
      const msg = asMessage({ ...e.attestation, evidenceHash: h, emissionKgH: String(bundle.emissionKgH) });
      let rec = "";
      try { rec = await recoverTypedDataAddress({ domain, types: TYPES, primaryType: "Attestation", message: msg, signature: e.signature }); }
      catch (err) { rec = "unrecoverable"; }
      const want = regAddr(e.org);
      const ok = rec.toLowerCase() === want.toLowerCase();
      allOk = check(list, ok, ok ? `${wtag(e.org)} · ECDSA secp256k1 (EIP-712) recovers to its key in the KeyRegistry record <code>${short(want)}</code>`
        : `${wtag(e.org)} · ECDSA recovers to <code>${short(rec)}</code>, not its registered <code>${short(want)}</code> → <b>InvalidSignature</b>`) && allOk;
      const m = e.mldsa44;
      const pqOk = (await pqAnchored(e.org, m.publicKey)) && ml_dsa44.verify(hexBytes(m.sig), enc.encode(canon), hexBytes(m.publicKey), { context: PQ_CTX });
      allOk = check(list, pqOk, pqOk ? `${wtag(e.org)} · ML-DSA-44 (FIPS 204) verifies over the same bytes with the key whose SHA-256 the registry anchors (checked here with @noble/post-quantum)`
        : `${wtag(e.org)} · ML-DSA-44 signature does not verify over the edited bytes`) && allOk;
    }
    const verdict = $("tamper-verdict");
    if (allOk) {
      verdict.className = "verdict";
      verdict.textContent = "Untouched: both witnesses' signatures verify over this exact record.";
    } else {
      verdict.className = "verdict bad";
      verdict.innerHTML = `Signature invalid — the transaction would revert and the bond would not move. `
        + (sepoliaTamper ? `The same edit, with the Sepolia witnesses' real signatures, was sent to Ethereum Sepolia: <a href="${sepoliaTamper.tx}" target="_blank" rel="noopener">reverted, ${sepoliaTamper.reason}</a>.` : "");
    }
    updateMoment(edited, allOk, original.emissionKgH, bundle.emissionKgH, sepoliaTamper);
    return allOk;
  }
  input.addEventListener("input", evaluate);
  const all = $("bundle-all");
  all.addEventListener("click", () => {
    const open = all.getAttribute("aria-expanded") !== "true";
    all.setAttribute("aria-expanded", String(open));
    $("bundle-table").classList.toggle("show-all", open);
    all.textContent = open ? "Show only the edited field" : "Show all fields";
  });
  TAMPER_ACTIONS.A = () => { input.value = TAMPER_RATE; return evaluate(); };
  $("tamper-btn").addEventListener("click", TAMPER_ACTIONS.A);
  $("reset-btn").addEventListener("click", () => { input.value = original.emissionKgH; evaluate(); });
  return evaluate();
}

// ---------------------------------------------------------------- 5. export
function parseCsv(text) {
  const rows = []; let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (ch === '"') q = false; else cell += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cell); cell = ""; }
    else if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else if (ch !== "\r") cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}
async function renderExport(d) {
  const [doc, csv] = await Promise.all([fetch("data/TKM_S_276.annex_d.json").then((r) => r.json()),
    fetch("data/TKM_S_276.annex_d.csv").then((r) => r.text())]);
  const t = $("annex-table");
  t.classList.add("annex");
  for (const [k, v] of Object.entries(doc.fields)) {
    t.append(el("tr", {}, el("td", {}, k), el("td", {}, v === "" ? "(blank: ongoing)" : v)));
  }
  const list = $("export-checks");
  const rows = Object.fromEntries(parseCsv(csv).slice(1));
  const back = { template: rows.template, fields: Object.fromEntries(Object.keys(doc.fields).map((k) => [k, rows[k]])),
    mars: JSON.parse(rows.mars), "x-methaneclause": JSON.parse(rows["x-methaneclause"]) };
  const [h1, h2] = await Promise.all([sha256hex(jcs(doc)), sha256hex(jcs(back))]);
  check(list, h1 === h2, `Round trip CSV → import → same canonical hash <code>${short(h1)}</code>`);
  const ext = doc["x-methaneclause"];
  const hb = await sha256hex(jcs(ext.bundle));
  check(list, hb === ext.episode.evidenceHash, `Embedded bundle hashes to the attested evidenceHash <code>${short(hb)}</code>`);
  check(list, doc.fields["Emissions ceased?"] === "NOT EVIDENCED" && doc.mars.source.emission_status === null,
    `"Emissions ceased?" = NOT EVIDENCED; MARS emission_status kept null (empty in the public file)`);
  renderEuEvidence(ext);
  await renderAnnexC(d);
  await renderCreditNote(d);
}

function renderEuEvidence(ext) {
  const box = $("annex-eu"), eu = ext.euEvidence, mv = ext.marsVocabulary;
  if (!eu) { $("annex-eu-box").hidden = true; return; }
  const t = el("table", { class: "dets" }, el("tr", {}, el("th", {}, "Annex IX item"), el("th", {}, "This record can support"), el("th", {}, "How")));
  for (const r of eu.annexIX) t.append(el("tr", {}, el("td", {}, r.item), el("td", {}, r.canSupport), el("td", {}, r.how)));
  box.append(el("p", { class: "sub" }, `${eu.regulation} — ${eu.wording}. `, link(eu.url, "EUR-Lex")), el("div", { class: "scroll-table" }, t),
    el("p", { class: "sub" }, eu.art2_62.text));
  if (mv) {
    box.append(el("p", { class: "sub" }, `MARS field ${mv.marsField} (`, link(mv.source, "MARS dictionary"), `): a CONFIRMED episode maps to "${mv.episodeConfirmed}"; a claim with at least three counted passes to "${mv.claimWithAtLeast3CountedPasses}" — ${mv.terms[mv.claimWithAtLeast3CountedPasses]}.`));
  }
}

async function renderAnnexC(d) {
  const A = d.exports?.annexC;
  const [form, csv] = await Promise.all([fetch("data/TKM_S_276.annex_c.example.json").then((r) => r.json()),
    fetch("data/TKM_S_276.annex_c.example.csv").then((r) => r.text())]);
  const label = form["x-methaneclause"].label;
  $("annexc-sub").append("The seller's answer to the MARS notification, on the IEA's own Annex C labels. ", el("span", { class: "sim" }, label),
    ". Its SHA-256 is the claimHash the seller records with recordMitigationClaim; the verdict does not move.");
  const t = $("annexc-table");
  for (const [k, v] of Object.entries(form.fields)) {
    if (v == null) continue;
    const isDate = (A?.dateLabels || []).includes(k);
    t.append(el("tr", {}, el("td", {}, k), el("td", {}, v, isDate ? el("span", { class: "mode m-sim", title: label }, "[SIM] example form") : "")));
  }
  const list = $("annexc-checks");
  const h = await sha256hex(jcs(form));
  check(list, h === A?.claimHash, `claimHash = SHA-256 of the RFC 8785 form, recomputed here <code>${short(h)}</code>`);
  const rec = d.exports?.verdict?.annexC;
  if (rec) check(list, rec.claimHash === h, `recorded on the local chain by the seller [SIM] (recordMitigationClaim) <code>${short(rec.tx)}</code>; the episode stays CONFIRMED`);
  const rows = parseCsv(csv);
  check(list, rows.length > 3 && rows[0].join(",") === "part,label,answer", "CSV export carries the template's labels (part, label, answer)");
}

// the register's visible CSV columns, derived from each note exactly as the pipeline's export writes them
function cnRow(n) {
  const v = n.verdict;
  return [n.noteHash, n.ref, n.kind, n.status, String(n.amount.units), n.amount.display, n.payer, n.payee, v.chain,
    v.covenantContract || v.pool, n.kind === "rebate" ? String(v.episodeId) : `${v.claimId}/${v.installmentId}`,
    v.tx, n.supersedes || "", n.supersededBy || ""];
}
async function renderCreditNote(d) {
  const V = d.exports?.verdict;
  const list = $("cn-checks"), t = $("cn-table");
  if (!V) { $("cn-sub").textContent = "Run make demo for the verdict-only covenant."; return; }
  const [reg, csv] = await Promise.all([fetch("data/TKM_S_276.credit_note.json").then((r) => r.json()),
    fetch("data/TKM_S_276.credit_note.csv").then((r) => r.text())]);
  $("cn-sub").append("TKM_S_276 in a verdict-only covenant for a Türkiye-type importer ", el("span", { class: "sim" }, "[SIM]"),
    ": tier amounts 0 and no bond, so CONFIRMED is the verdict and nothing moves on chain. The parties settle it under the supply contract by bank transfer or credit note. A superseded note would be marked in this register (an off-chain export step); the chain records no supersession.");
  for (const n of reg.notes) {
    for (const [k, val] of [["Kind / status", `${n.kind} · ${n.status}`], ["Amount", `${n.amount.display} — ${n.amount.definition}`],
      ["Payer → payee", `${n.payer} → ${n.payee}`], ["Verdict", `${n.verdict.chain === "Anvil (local)" ? "the local test chain" : n.verdict.chain}: episode ${n.verdict.episodeId} ${n.verdict.state} (tier ${n.verdict.tier}), tx ${short(n.verdict.tx)}`],
      ["ref", `${n.ref} (${n.refSource})`], ["Settlement", n.settlement], ["Law", n.law]]) {
      t.append(el("tr", {}, el("td", {}, k), el("td", {}, val)));
    }
  }
  let hashesOk = true;
  for (const n of reg.notes) {
    const { noteHash, ...rest } = n;
    hashesOk = hashesOk && (await sha256hex(jcs(rest))) === noteHash;
  }
  check(list, hashesOk, "each note matches its noteHash (RFC 8785 + SHA-256, recomputed here)");
  const rows = parseCsv(csv).filter((r) => r.length > 1);
  const body = rows.slice(2);
  const visibleOk = body.length === reg.notes.length && body.every((r, i) => {
    const want = cnRow(reg.notes[i]);
    return want.every((c, k) => r[k] === c) && jcs(JSON.parse(r[r.length - 1])) === jcs(reg.notes[i]);
  });
  check(list, visibleOk, "CSV round trip: every visible column equals what its row's JSON says, and the JSON equals the register");
  check(list, reg.notes.every((n) => n.supersession === null || n.supersession.onChainEvent === null),
    "no note claims an on-chain supersession");
  check(list, reg.notes[0]?.amount.units === V.units, `amount ${reg.notes[0]?.amount.display} = the verdict-only claim's cap on chain (VERDICT_ONLY pool, policy units)`);
}

// ---------------------------------------------------------------- 6. live chain + self-checks
const rpcList = (s) => [s.rpc, ...(s.rpcFallbacks || [])];
const hostOf = (u) => new URL(u).hostname;
const rpcClient = (u) => createPublicClient({ transport: http(u, { retryCount: 0, timeout: 10_000 }) });
async function liveRead(d) {
  let last;
  for (const url of rpcList(d.sepolia)) {
    try { return { ...(await liveReadFrom(d, url)), via: hostOf(url) }; } catch (e) { last = e; }
  }
  throw last;
}
async function liveReadFrom(d, url) {
  const s = d.sepolia;
  const client = rpcClient(url);
  const abi = d.abi.MethaneCovenant;
  const [block, covenant, episode] = await Promise.all([
    client.getBlockNumber(),
    client.readContract({ address: s.methaneCovenant, abi, functionName: "getCovenant", args: [s.run.covenantId] }),
    client.readContract({ address: s.methaneCovenant, abi, functionName: "getEpisode", args: [BigInt(s.run.episodeId)] }),
  ]);
  const STATES = ["NONE", "PROPOSED", "CHALLENGED", "CONTESTED", "CONFIRMED", "VOID", "EXPIRED", "LINKED"];
  return { block, covenant, episode: { ...episode, stateName: STATES[Number(episode.state)] } };
}

async function selfChecks(d, live, liveErr, tamperOk) {
  const list = $("self-checks");
  const exp = await fetch("data/vectors/expected.json").then((r) => r.json());
  let n = 0;
  for (const [f, want] of Object.entries(exp)) {
    const v = await fetch(`data/vectors/${f}`).then((r) => r.json());
    if ((await sha256hex(jcs(v))) === want.evidenceHash) n++;
  }
  check(list, n === Object.keys(exp).length, `RFC 8785 parity: ${n}/${Object.keys(exp).length} Python test vectors hash identically in this browser`);
  const pins = pinMismatches(d);
  check(list, pins.length === 0, pins.length === 0
    ? `Pinned in this page's code and equal to data/app.json: the ML-DSA-44 context <code>${PINNED.mldsaContext}</code> and the KeyRegistry anchor of ${d.hybrid.chainId === 31337 ? `the local demo chain (Anvil, chainId ${d.hybrid.chainId}; not the Sepolia registry)` : `chainId ${d.hybrid.chainId}`}: registry <code>${short(PINNED.registry)}</code>, ${Object.keys(PINNED.pqPubKeyHash).length} witnesses' anchored key hashes`
    : `Pinned constants differ from data/app.json: ${pins.join(", ")}; signatures are checked against the pins`);
  check(list, tamperOk, "JS ↔ Python: ML-DSA-44 signatures made in Python verify here against the registry's anchored key hashes; ECDSA recovers to the witness addresses in the KeyRegistry record");
  if (live) {
    check(list, true, `Live read from Ethereum Sepolia at block ${fmt.format(live.block)}: covenant bond ${ethOf(live.covenant.remaining)} remaining, episode ${d.sepolia.run.episodeId} ${live.episode.stateName}`);
  } else {
    check(list, null, `Live Sepolia read unavailable (${liveErr}); showing the committed run record`);
  }
  const rc = d.receipts, steps = d.sepolia.run.steps;
  const via = Object.entries(rc?.via || {}).map(([h, k]) => `${h} (${k} read${k === 1 ? "" : "s"})`).join(", ");
  if (rc?.ok) {
    const same = steps.filter((x) => rc.status[txHash(x.tx)] === x.status).length;
    check(list, same === steps.length, `Sepolia receipts read live: ${same}/${steps.length} transaction statuses equal the recorded run (${steps.filter((x) => x.status === 0).length} of them status 0), via ${via}`);
  } else {
    check(list, null, `Sepolia receipts: ${rc?.read || 0} of ${steps.length} read live${via ? ` (via ${via})` : ""}; the others could not be read from the public endpoints tried (${(rc?.tried || rpcList(d.sepolia).map(hostOf)).join(", ")}) from this network. The explorer table links every transaction.`);
  }
  const a = d.adversarial;
  const LAYER = { validation: "witness validation", hash: "hash / signature", "re-check": "the witness's re-check (re-fetch)" };
  const layers = Object.entries(a.by_layer).map(([k, v]) => `${v.caught} by ${LAYER[k] || k}`).join(", ");
  check(list, null, `Adversarial set (ours): ${a.caught}/${a.n} bad inputs caught — ${layers}`);
}


// ================================================================ v2: the two-sided clause
const OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const CM_TILE = (id) => `https://api.carbonmapper.org/api/v1/layers/scene/${id}/ch4/{z}/{x}/{y}.png`;
const INSTR = { tan: "Tanager", emi: "EMIT" };
const COVENANT_OF = {
  TKM_S_276: ["Sepolia main covenant", "buyer: importer [SIM]"],
  DZA_S_012: ["DZA replay clause on a local test chain (Anvil) [SIM clock]", "buyer: EU importer [SIM]"],
};
// one legend: only the simulated mark is drawn; real, computed and projected values carry no badge
const tag = (kind) => (kind === "sim" ? el("span", { class: "sim" }, "[SIM]") : null);
// a MOCK badge only where a data/derived/mock/ placeholder is rendered; real files render without one
const mockBadge = (prov) => (prov && prov.mock ? el("span", { class: "mockb", title: `${prov.path} — ${prov.note || "MOCK"}` }, "MOCK data") : null);
const pct = (x, digits = 1) => `${(100 * x).toFixed(digits)} %`;
const tileStatus = { loaded: 0, failed: 0, layers: 0 };
const maps = [];

function sceneMap(box, p, facility, what) {
  const wrap = el("div", { class: "scene" });
  const div = el("div", { class: "scene-map", role: "img",
    "aria-label": `${what}: Carbon Mapper CH₄ enhancement of scene ${p.sceneName}, plume origin and the UNEP MARS source point` });
  const note = el("p", { class: "tile-note" });
  wrap.append(div, note);
  box.append(wrap);
  if (!window.L) { div.textContent = "Map library unavailable offline."; return; }
  const m = L.map(div, { scrollWheelZoom: false, attributionControl: true });
  m.setView([p.lat, p.lon], 13);
  L.tileLayer(OSM, { maxZoom: 17, attribution: "© OpenStreetMap" }).addTo(m);
  if (p.sceneId) {
    tileStatus.layers++;
    let ok = 0, bad = 0;
    const layerCm = L.tileLayer(CM_TILE(p.sceneId), { maxZoom: 17, opacity: 0.9, attribution: "Data by Carbon Mapper®" });
    layerCm.on("tileload", () => { ok++; tileStatus.loaded++; note.textContent = `CH₄ scene ${p.sceneName} · Data by Carbon Mapper®`; });
    layerCm.on("tileerror", () => { bad++; tileStatus.failed++; if (!ok) note.textContent = `CH₄ tile unavailable (Carbon Mapper unreachable); scene ${p.sceneName}`; });
    layerCm.addTo(m);
    note.textContent = `Loading CH₄ scene ${p.sceneName}…`;
  } else {
    note.textContent = `CH₄ tile: scene id not resolved for ${p.sceneName} (run make app-scenes).`;
  }
  L.circleMarker(facility, { radius: 7, color: "#1d2327", weight: 2, fillColor: "#ffffff", fillOpacity: 1 })
    .bindTooltip("UNEP MARS source point").addTo(m);
  L.circleMarker([p.lat, p.lon], { radius: 6, color: "#b45309", weight: 2, fillColor: "#f0a35b", fillOpacity: 0.4 })
    .bindTooltip(`plume origin ${p.plumeId}`).addTo(m);
  maps.push(m);
}

function fact(label, value, ...tags) {
  return el("div", { class: "fact" }, el("span", { class: "lbl" }, label), el("span", { class: "val" }, value, " ", ...tags));
}

function panel(step, title, state) {
  const p = el("article", { class: `panel ${state || ""}`, "aria-label": `${step}. ${title}` });
  p.append(el("h4", {}, el("span", { class: "step" }, step), title));
  return p;
}

function renderLedger(d, live) {
  const body = $("ledger-body");
  body.innerHTML = "";
  const s = d.sepolia, prop = s.run.steps.find((x) => x.step === "proposeEpisode");
  for (const name of ["TKM_S_276", "DZA_S_012"]) {
    const L2 = d.ledger[name], cov = COVENANT_OF[name];
    const card = el("div", { class: "ledger" });
    card.append(el("div", { class: "ledger-head" },
      el("h3", {}, name, " ", el("span", { class: "badge absent" }, `MARS: ${L2.mars.category}`)),
      el("p", { class: "sub" }, `${L2.sourceType}, ${L2.country} · ${cov[0]} · ${cov[1].replace(" [SIM]", "")} `, el("span", { class: "sim" }, "[SIM]"))));
    const row = el("div", { class: "panels" });
    const foot = el("div", { class: "ledger-foot" });  // full width under the three panels: the bound and why the refund is capped
    // ---- 1 leak
    const lk = L2.leak, p1 = panel("1", "Leak", "leak");
    sceneMap(p1, lk, L2.mars.facility, `${name} leak`);
    p1.append(
      fact("Detected", `${INSTR[lk.instrument] || lk.instrument} ${lk.sceneAt.slice(0, 10)} · ${fmt.format(lk.rateKgH)} ± ${fmt.format(lk.uncKgH)} kg/h · published ${lk.publishedAt.slice(0, 10)}`, tag("real")),
      fact("UNEP MARS", `"${L2.mars.category}" · last MARS plume ${L2.mars.lastPlume}`, tag("real")));
    if (name === "TKM_S_276") {
      const st = live?.episode?.stateName || "PROPOSED";
      p1.append(
        fact("Rebate on Sepolia", el("span", {}, `episode ${prop.episodeId} `, el("span", { class: `state ${st}` }, st), ` · tier ${prop.tier} · ${ethOf(prop.rebateWei)} · `, link(prop.tx, "proposeEpisode tx")), tag("real")),
        fact("Rebate in the local demo", `CONFIRMED · ${d.demo.rebate_eth} ETH withdrawn by the buyer`, tag("sim")));
    } else {
      const ra = d.replay?.variants?.A;
      const fin = ra?.txs?.["finalize (episode)"]?.[0];
      p1.append(fact("Rebate in the replay", fin
        ? el("span", {}, `episode ${ra.episodeId} CONFIRMED on the replay chain, rebate to the buyer · finalize tx `, el("code", {}, short(fin.tx)))
        : "run make demo for the replay", tag("sim"), el("span", { class: "sim" }, "[SIM clock]")));
    }
    row.append(p1, el("div", { class: "arrow", "aria-hidden": "true" }, "→"));
    // ---- 2 qualified clean passes
    const c = L2.clean;
    const ep = `${lk.sceneAt.slice(0, 10)} episode`;
    if (!c || c.n === 0) {
      const p2 = panel("2", "Qualified clean passes", "none");
      const status = c?.statusLabel || "no attestable clean pass yet";
      const yet = / yet$/.test(status), core = status.replace(/ yet$/, "");
      p2.append(el("p", { class: "big" }, `${core[0].toUpperCase()}${core.slice(1)} since the ${ep}${yet ? " yet" : ""}.`),
        fact("Carbon Mapper since MARS's last plume", `saw a plume on ${L2.postMars.detected} of ${L2.postMars.passes} passes over the sources within ${L2.postMars.radiusKm} km of the facility since ${L2.postMars.since}${L2.allPassesDetected ? " (PLUME_ON_SCENE on every one)" : ""}`, tag("real"), tag("comp")));
      if (c) {
        const refs = Object.entries(c.refusals).map(([k, v]) => `${k} ${v}`).join(", ");
        p2.append(fact("Since the episode's link window closed", `${day(c.since)}: ${c.candidates} candidate scene${c.candidates === 1 ? "" : "s"}, none attestable${refs ? ` (${refs})` : ""}`, tag("comp"), mockBadge(c.provenance)));
      }
      p2.append(fact("Refund", "nothing to release: the clause pays a refund only for qualified clean passes", tag("comp")));
      row.append(p2);
    } else {
      const p2 = panel("2", "Qualified clean passes", "clean");
      const mb = () => mockBadge(c.provenance);
      p2.querySelector("h4").append(" ", mb() || "");
      const ub = c.upperBoundBp;
      const instr = c.instruments.map((i) => INSTR[i] || i).join(" + ");
      p2.append(
        el("p", { class: "big" }, c.statusLabel ? `${c.statusLabel[0].toUpperCase()}${c.statusLabel.slice(1)} before the relapse` : `${c.n} qualified clean passes before the relapse`),
        fact("Counted since", `${day(c.since)} (the episode's link window closed)`, tag("comp"), mb()),
        fact("Counted", `${c.n} of ${c.candidates} candidate scenes · ${instr || "—"}`, tag("comp"), mb()));
      const how = el("div", { class: "more-body" });
      if (c.lowerBound) {
        how.append(fact("Lower bound", "a lower bound on what a witness at each pass's time would have counted (they saw at most today's plumes); re-checking later can only lower it", tag("comp")));
      }
      if (c.atCloudBin.n) {
        const byI = Object.entries(c.atCloudBin.byInstrument).sort((a, b) => b[1] - a[1]).map(([i, k]) => `${INSTR[i] || i} ×${k}`).join(", ");
        how.append(fact("Cloud gate", `${c.atCloudBin.n} of the ${c.n} counted passes sit at the ${c.maxCloudPct} % cloud value Carbon Mapper reports (${byI}); the gate is Carbon Mapper's not_cloudy flag`, tag("comp")));
      }
      if (how.childElementCount) {
        const parts = [c.lowerBound ? "why the count is a lower bound" : null, c.atCloudBin.n ? "the cloud gate" : null].filter(Boolean).join(" · ");
        p2.append(el("details", { class: "scenes more" }, el("summary", {}, parts[0].toUpperCase() + parts.slice(1)), how));
      }
      p2.append(
        fact(`Upper bound u(${c.n})`, ub == null ? "no qualified clean pass yet" : `plumes above the floor on at most ${(ub / 100).toFixed(1)} % of passes — two-sided 95 % (one-sided 97.5 %) Clopper–Pearson, N = ${c.n}`, tag("comp"), mockBadge(c.upperBoundProvenance)),
        fact("Floor used", c.floorKgH == null ? "pending the instrument floor table" : `${fmt.format(c.floorKgH)} kg/h (the highest floor among the counted instruments)`, tag("comp")));
      if (c.installments) {
        const ul = el("ul", { class: "inst", "aria-label": "Installments of the refund and what the rule yields at the relapse" });
        let paid = 0, lost = 0;
        for (const [after, bp, two, fin] of c.installments) {
          const reached = c.n >= after && (!two || c.instruments.length >= 2);
          if (reached && !fin) paid += bp; else lost += bp;
          const cond = [two ? "two instruments" : null, fin ? "MARS corroboration + cure term" : null].filter(Boolean).join(", ");
          ul.append(el("li", { class: reached && !fin ? "reached" : "forfeit" },
            el("span", { class: "inst-mark" }, reached && !fin ? "✓ reached" : "✕ forfeited"),
            ` ${after} passes → ${bp / 100} %${cond ? ` (${cond})` : ""}`));
        }
        const ra = d.replay?.variants?.A, rb = d.replay?.variants?.B;
        const rel = ra?.txs?.release || [];
        p2.append(fact("Installments under the rule", ul, tag("comp"), mb()),
          fact("At the relapse", `paid ${paid / 100} % of the cap, forfeited ${lost / 100} %`, tag("comp")));
        const more = el("div", { class: "more-body" });
        if (ra?.outcome) {
          const freed = BigInt(ra.outcome.releasedReserveWei);
          more.append(fact("On the replay chain", el("span", { id: "replay-a" }, `${ra.outcome.counted} qualified clean passes counted on chain; ${rel.length} installment release tx${rel.length === 1 ? "" : "s"} `, ...rel.map((t) => el("code", {}, short(t.tx) + " ")), `; at the cancel: paid ${ra.outcome.paidBp / 100} %, forfeited ${ra.outcome.forfeitedBp / 100} %, ${freed === 0n ? "no reserve was pending" : `${ethOf(freed)} of reserve freed`}`), tag("sim"), el("span", { class: "sim" }, "[SIM clock]")));
        }
        const sb = d.replay?.suretyBond, pull = ra?.txs?.["SuretyBond.pull"]?.[0];
        if (sb?.address && pull) {
          more.append(fact("Who gets the refund", el("span", { id: "replay-surety" }, `covenant A's bond was posted by a surety [SIM] through a SuretyBond contract (the covenant's seller); the released refunds and, after the term, the remaining bond (${ethOf(BigInt(sb.settleRefundWei))}) were pulled to it `, el("code", {}, short(pull.tx)), " — the operator cannot move them (status 0: NotFunder)"), tag("sim"), el("span", { class: "sim" }, "[SIM clock]")));
        }
        if (rb?.outcome) {
          more.append(fact("The other outcome [SIM look-ahead]", el("span", { id: "replay-b" }, `[SIM look-ahead] with today's origin points ${rb.outcome.counted} passes would have been counted; the MARS corroboration then scheduled the final ${lost / 100} %, and the relapse cancelled it while pending (${ethOf(BigInt(rb.outcome.releasedReserveWei))} of reserve freed); released installments stay released`), tag("sim")));
        }
        if (more.childElementCount) {
          p2.append(el("details", { class: "scenes more" }, el("summary", {}, "On the replay chain: transactions, who gets the refund, the other outcome"), more));
        }
      }
      const t = el("table", { class: "dets", "aria-label": "Candidate scenes and their qualification result" },
        el("tr", {}, el("th", {}, "Scene"), el("th", {}, "Instr."), el("th", {}, "Cloud %"), el("th", {}, "Result")));
      for (const sc of c.scenes) {
        const res = sc.qualified === true ? (sc.afterRelapse ? "✓ qualified · next claim" : "✓ counted") : `✕ ${sc.refusal || "undetermined"}`;
        t.append(el("tr", { class: sc.afterRelapse ? "after" : "" }, el("td", {}, day(sc.sceneAt)), el("td", {}, sc.instrument),
          el("td", {}, `${sc.cloudCoverPct} / ${sc.cloudCoverPctAssessed}${sc.notCloudy ? "" : " · cloudy"}`),
          el("td", { class: sc.qualified === true ? "ok-t" : "bad-t" }, res)));
      }
      p2.append(el("details", { class: "scenes" }, el("summary", {}, `All ${c.candidates} candidate scenes and why each counts or not`), el("div", { class: "scroll-table" }, t)));
      const u = ub == null ? `u(${c.n})` : `${(ub / 100).toFixed(1)} %`;
      foot.append(el("p", { class: "unit" }, `After ${c.n} qualified clean passes (${instr || "—"}), the frequency of plumes above ${c.floorKgH == null ? "the floor" : fmt.format(c.floorKgH) + " kg/h"} at this source is at most ${u}; before the episode it was ${c.d0 == null ? "d0 (pending)" : c.d0}. Observed, not abated. `, mb() || ""));
      foot.append(capReasonsBlock(d));
      row.append(p2, el("div", { class: "arrow", "aria-hidden": "true" }, "→"));
      // ---- 3 relapse
      const rl = L2.relapse, p3 = panel("3", "Relapse", "relapse");
      if (rl) {
        sceneMap(p3, rl, L2.mars.facility, `${name} relapse`);
        p3.append(
          fact("Detected", `${INSTR[rl.instrument] || rl.instrument} ${rl.sceneAt.slice(0, 10)} · ${fmt.format(rl.rateKgH)} ± ${fmt.format(rl.uncKgH)} kg/h · published ${rl.publishedAt.slice(0, 10)}`, tag("real")),
          fact("Effect", "a new attested detection freezes the claim while it is under review, then cancels every pending installment; released installments stay released", tag("comp")),
          relapseTxs(d));
      } else {
        p3.append(el("p", {}, "No attested detection after the run of qualified clean passes."));
      }
      row.append(p3);
    }
    card.append(row);
    if (foot.childElementCount) card.append(foot);
    body.append(card);
  }
}

// why the refund is capped: the pipeline's catalogue sentence verbatim, a reason for the cap, never
// evidence of repair; nothing typed here stands in for it
function capReasonsBlock(d) {
  const cr = d.capReasons, cat = cr.catalogue, box = el("div", { class: "capr" });
  box.append(el("p", { class: "sub" }, "Held out, the persistence rule called zero on passes that still showed a plume ",
    `${cr.falseZero.map((x) => pct(x)).join(" / ")} of the time (two test sets) `, tag("comp"), "."));
  if (cat) {
    box.append(el("p", { class: "capr-line" }, cat.sentence, " ", tag("comp"), mockBadge(cat.provenance)));
    if (cat.unit) box.append(el("p", { class: "sub" }, `Unit: ${cat.unit}.`));
  }
  return box;
}

function relapseTxs(d) {
  const ra = d.replay?.variants?.A;
  if (!ra) return fact("Cancel tx", "run make demo for the replay", tag("sim"));
  const early = ra.txs["relapse while the episode is PROPOSED"]?.[0], cancel = ra.txs["relapse (cancel)"]?.[0];
  const frz = d.replay?.variants?.B?.txs?.["release while the relapse is PROPOSED"]?.[0];
  return fact("On the replay chain", el("span", {},
    early ? el("span", {}, "cancel tried while the relapse was PROPOSED: status 0, ", el("strong", {}, early.revert || "reverted"), " ", el("code", {}, short(early.tx)), "; ") : "",
    cancel ? el("span", {}, "after CONFIRMED: relapse() cancelled the claim ", el("code", {}, short(cancel.tx))) : "",
    frz ? el("span", { id: "replay-freeze" }, "; [SIM look-ahead] releasing B's pending final while the relapse was PROPOSED: status 0, ", el("strong", {}, frz.revert || "reverted"), " ", el("code", {}, short(frz.tx))) : ""),
  tag("sim"), el("span", { class: "sim" }, "[SIM clock]"));
}

// ---------------------------------------------------------------- witness five steps
const R_EARTH = 6371.0088;
function haversineKm(a, b, c, e) {
  const r = Math.PI / 180, dl = (c - a) * r, dn = (e - b) * r;
  const h = Math.sin(dl / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(dn / 2) ** 2;
  return 2 * R_EARTH * Math.asin(Math.sqrt(h));
}
async function observerSteps(d) {
  const H = d.hybrid, e = H.entries[0], b = e.bundle, rec = d.observerReplay.receipts.find((r) => r.org === e.org);
  const c = d.cases[b.sourceName], m = c.mars;
  const t = (iso) => Math.floor(Date.parse(iso) / 1000);
  const domain = { name: "MethaneClause", version: "1", chainId: H.chainId, verifyingContract: H.verifyingContract };
  const canon = jcs(b), h = await sha256hex(canon);
  const msg = asMessage(e.attestation);
  const digest = hashTypedData({ domain, types: TYPES, primaryType: "Attestation", message: msg });
  const signer = await recoverTypedDataAddress({ domain, types: TYPES, primaryType: "Attestation", message: msg, signature: e.signature });
  const pqOk = (await pqAnchored(e.org, e.mldsa44.publicKey)) && ml_dsa44.verify(hexBytes(e.mldsa44.sig), enc.encode(canon), hexBytes(e.mldsa44.publicKey), { context: PQ_CTX });
  const dist = haversineKm(m.lat, m.lon, b.plumeLat, b.plumeLon);
  const minRate = d.observerReplay.minRateKgH, start = d.observerReplay.covenantStart;
  const linked = c.cmPlumes.some((p) => p.source_name === b.cmSourceName);
  return [
    { label: "Fetch", lines: [
      [null, `GET <a href="${rec.url}" target="_blank" rel="noopener">${rec.url.replace("https://", "")}</a> (${rec.mode}; Carbon Mapper sends no CORS header, so the witness fetches in the terminal and this is its receipt)`],
      [null, `retrievedAt ${rec.retrievedAt} · provider content hash <code>${short(rec.contentSha256, 12, 6)}</code>`]] },
    { label: "Validate", lines: [
      [b.schemaVersion === "methaneclause.bundle/1" && Object.keys(b).length === BUNDLE_ORDER.length, `SCHEMA · ${Object.keys(b).length} fields, ${b.schemaVersion}`],
      [b.emissionKgH >= 1 && b.emissionKgH < 100000, `RANGE · ${fmt.format(b.emissionKgH)} kg/h inside [1, 100,000)`],
      [b.emissionKgH >= minRate, `BELOW_FLOOR · ${fmt.format(b.emissionKgH)} ≥ the covenant's minimum rate ${fmt.format(minRate)} kg/h`],
      [t(b.sceneTimestamp) === b.detectedAt && b.detectedAt > start && t(b.publishedAt) >= b.detectedAt && t(b.publishedAt) <= t(rec.retrievedAt),
        `TIME · detected ${b.sceneTimestamp.slice(0, 10)} after the covenant start ${day(start)}; published ${b.publishedAt.slice(0, 10)}, before the fetch`],
      [b.sourceId === c.sourceId && linked, `NOT_COVERED · facility ${b.sourceName}; Carbon Mapper source ${b.cmSourceName} is linked to it`],
      [dist <= d.join.radius_km, `DISTANCE · plume origin ${dist.toFixed(2)} km from the MARS point (≤ ${d.join.radius_km} km)`],
      [null, "Clean passes add CLOUD_FLAG, CLOUD_FLAG_MISMATCH, NO_ORIGIN_INTERSECT, PLUME_ON_SCENE, MARS_PLUME_NEARBY, SCENE_BEFORE_SINCE, BEFORE_PUBLICATION_LAG, AFTER_TERM, DUPLICATE_SCENE, FLOOR_MISMATCH — each scene's result is on the ledger card"]] },
    { label: "Seal", lines: [
      [null, `RFC 8785 canonical bytes: ${enc.encode(canon).length} bytes, <code>${canon.slice(0, 60).replace(/</g, "&lt;")}…</code>`],
      [h === e.evidenceHash, `SHA-256 → evidenceHash <code>${short(h, 12, 6)}</code>${h === e.evidenceHash ? " (equals the signed value)" : " (differs from the signed value)"}`]] },
    { label: "Sign", lines: [
      [null, `EIP-712 digest <code>${short(digest, 12, 6)}</code> (domain MethaneClause v1, chain ${H.chainId})`],
      [signer.toLowerCase() === regAddr(e.org).toLowerCase(), `${wtag(e.org)} · ECDSA secp256k1 recovers to its key in the KeyRegistry record <code>${short(regAddr(e.org))}</code>`],
      [pqOk, `${wtag(e.org)} · ML-DSA-44 (FIPS 204) second signature over the same bytes verifies with the registry-anchored key`]] },
    { label: "Re-check", lines: [
      [rec.contentSha256 === b.providerContentSha256, `re-fetch content hash <code>${short(rec.contentSha256, 12, 6)}</code> ${rec.contentSha256 === b.providerContentSha256 ? "equals" : "differs from"} the bundle's providerContentSha256`],
      [null, "Tamper B below shows the other outcome: a list with one record missing hashes differently"]] },
  ];
}

let obsTimer = null;
function renderSteps(steps, upto) {
  const ol = $("obs-steps");
  ol.innerHTML = "";
  steps.forEach((s, i) => {
    const done = i < upto, ok = s.lines.every(([v]) => v !== false);
    const li = el("li", { class: `ostep ${done ? (ok ? "done" : "failed") : "waiting"}` });
    li.append(el("div", { class: "ostep-h" }, el("span", { class: "ostep-n" }, String(i + 1)), el("strong", {}, s.label),
      el("span", { class: "ostep-s" }, done ? (ok ? "✓ passed" : "✕ failed") : "waiting")));
    if (done) {
      const ul = el("ul", { class: "checks" });
      for (const [v, html] of s.lines) check(ul, v, html);
      li.append(ul);
    }
    ol.append(li);
  });
}
async function setupObserver(d) {
  const steps = await observerSteps(d);
  const lead = d.hybrid.entries[0];
  $("obs-sub").textContent = `Replayed from the committed demo run (${wname(lead.org)} on the ${lead.bundle.sceneTimestamp.slice(0, 10)} plume). Every check below runs again in your browser; the same five labels are printed by the witness in the terminal.`;
  renderSteps(steps, steps.length);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("obs-replay").addEventListener("click", () => {
    clearInterval(obsTimer);
    let i = 0;
    renderSteps(steps, 0);
    obsTimer = setInterval(() => { i++; renderSteps(steps, i); if (i >= steps.length) clearInterval(obsTimer); }, reduce ? 0 : 700);
  });
  $("obs-skip").addEventListener("click", () => { clearInterval(obsTimer); renderSteps(steps, steps.length); });
  const ctl = d.sepolia.sandbox.independentController;
  $("open-slot").replaceChildren("Open witness slot, live in the judge sandbox: organisation ", el("code", {}, "OBS_INDEPENDENT"),
    " has a published controller key (", el("a", { href: `${d.sepolia.explorer}/address/${ctl}`, target: "_blank", rel: "noopener" }, el("code", {}, short(ctl))),
    "), so anyone can register a key there; a signature from it reads \"registered through the open slot by a wallet we do not hold — see the registration tx\".");
  return steps.every((s) => s.lines.every(([v]) => v !== false));
}

// ---------------------------------------------------------------- tamper B: remove a detection
async function setupMissing(d) {
  const L2 = d.detectionList, relapse = d.ledger.DZA_S_012.relapse;
  const keep = new Set(L2.rows.map((r) => r.plumeId));
  $("miss-sub").textContent = `${L2.source}'s Carbon Mapper sightings as the witness's re-fetch hashes them (from the committed case file). Untick a row — or remove the ${relapse.sceneAt.slice(0, 10)} new leak — and the list no longer matches what the provider serves.`;
  $("miss-btn").textContent = `Tamper: remove the ${relapse.sceneAt.slice(0, 10)} detection`;
  $("miss-committed").textContent = L2.listSha256;
  const tbl = $("miss-table");
  async function evaluate() {
    tbl.innerHTML = "";
    tbl.append(el("tr", {}, el("th", {}, "In list"), el("th", {}, "Scene"), el("th", {}, "Instr."), el("th", { class: "num" }, "kg/h")));
    for (const r of [...L2.rows].reverse()) {  // newest first; the hash is over the receipt's order
      const cb = el("input", { type: "checkbox", "aria-label": `keep ${r.plumeId} in the list` });
      cb.checked = keep.has(r.plumeId);
      cb.addEventListener("change", () => { if (cb.checked) keep.add(r.plumeId); else keep.delete(r.plumeId); evaluate(); });
      tbl.append(el("tr", { class: keep.has(r.plumeId) ? "" : "removed", "data-plume": r.plumeId }, el("td", {}, el("label", { class: "tap" }, cb)), el("td", {}, r.sceneDay),
        el("td", {}, r.instrument), el("td", { class: "num" }, fmt.format(r.emissionKgH))));
    }
    const h = await sha256hex(jcs(L2.rows.filter((r) => keep.has(r.plumeId))));
    const same = h === L2.listSha256;
    $("miss-now").textContent = h;
    $("miss-now").className = same ? "" : "bad";
    const list = $("miss-checks");
    list.innerHTML = "";
    check(list, same, same ? `All ${L2.rows.length} records present: the hash equals the witness's re-fetch receipt`
      : `${L2.rows.length - keep.size} sighting(s) missing: re-fetch hash differs`);
    const v = $("miss-verdict");
    v.className = same ? "verdict" : "verdict bad";
    v.textContent = same ? "Untouched: the list is what the provider serves."
      : !keep.has(relapse.plumeId)
        ? `Missing sighting caught — a scene on ${relapse.sceneAt.slice(0, 10)} cannot be made to look like a clean pass by deleting its plume: the witness's re-fetch differs, and a plume on the scene refuses it as a clean pass.`
        : "Missing sighting caught — the re-fetch differs, so the witness refuses to sign and anyone can challenge.";
    return same;
  }
  TAMPER_ACTIONS.B = () => { keep.delete(relapse.plumeId); return evaluate(); };
  $("miss-btn").addEventListener("click", TAMPER_ACTIONS.B);
  $("miss-reset").addEventListener("click", () => { L2.rows.forEach((r) => keep.add(r.plumeId)); evaluate(); });
  return evaluate();
}

// ---------------------------------------------------------------- tamper C: edit a scene's cloud fields
const POOL_TYPES = {
  CleanPass: [
    { name: "claimId", type: "bytes32" }, { name: "sourceId", type: "bytes32" }, { name: "sceneId", type: "bytes32" },
    { name: "sceneHash", type: "bytes32" }, { name: "sceneAt", type: "uint64" }, { name: "publishedAt", type: "uint64" },
    { name: "instrumentId", type: "uint8" }, { name: "providerId", type: "bytes32" }, { name: "cloudBp", type: "uint16" },
    { name: "notCloudy", type: "bool" }, { name: "floorKgH", type: "uint32" }, { name: "rateFloorKgH", type: "uint32" },
    { name: "kind", type: "uint8" }, { name: "nonce", type: "uint64" }, { name: "expiry", type: "uint64" },
  ],
};
const POOL_UINTS = new Set(POOL_TYPES.CleanPass.filter((f) => f.type.startsWith("uint")).map((f) => f.name));
const asPoolMessage = (m) => Object.fromEntries(Object.entries(m).map(([k, v]) => [k, POOL_UINTS.has(k) ? BigInt(v) : v]));

async function setupCloud(d) {
  const C = d.cleanPassDemo, orig = C.bundle;
  const real = !C.provenance.mock;
  $("cloud-sub").textContent = `${real ? `The witness's scene record for the ${new Date(orig.sceneAt * 1000).toISOString().slice(0, 10)} ${INSTR[orig.instrument] || orig.instrument} scene at DZA_S_012` : "A placeholder scene record for DZA_S_012"}, signed as a clean pass by two test witnesses [SIM] on the local test chain, in the CleanPass EIP-712 domain. Paste in a cloudy scene's cloud fields and the sceneHash inside the signed struct changes: both signatures fail.`;
  $("cloud-mock").hidden = real;
  $("cloud-committed").textContent = C.sceneHash;
  const pctIn = $("cloud-pct"), assIn = $("cloud-assessed"), flag = $("cloud-flag");
  const reset = () => { pctIn.value = orig.cloudCoverPct; assIn.value = orig.cloudCoverPctAssessed; flag.value = String(orig.notCloudy); };
  reset();
  const num = (inp, dflt) => { const v = Number.parseInt(inp.value, 10); return Number.isFinite(v) ? v : inp.value === "" ? null : dflt; };
  async function evaluate() {
    const scene = { ...orig, cloudCoverPct: num(pctIn, orig.cloudCoverPct), cloudCoverPctAssessed: num(assIn, orig.cloudCoverPctAssessed), notCloudy: flag.value === "true" };
    const edited = new Set(["cloudCoverPct", "cloudCoverPctAssessed", "notCloudy"].filter((k) => scene[k] !== orig[k]));
    const tbl = $("cloud-table");
    tbl.innerHTML = "";
    for (const [k, v] of Object.entries(scene)) {
      tbl.append(el("tr", { class: edited.has(k) ? "edited" : "" }, el("td", {}, k), el("td", {}, typeof v === "object" ? JSON.stringify(v) : String(v))));
    }
    const canon = jcs(scene), h = await sha256hex(canon), same = h === C.sceneHash;
    $("cloud-now").textContent = h;
    $("cloud-now").className = same ? "" : "bad";
    const list = $("cloud-checks");
    list.innerHTML = "";
    let allOk = check(list, same, same ? "Scene record hashes to the signed sceneHash" : "sceneHash changed: this is not the scene the witnesses signed");
    const cloudBp = 100 * Math.max(scene.cloudCoverPct ?? 0, scene.cloudCoverPctAssessed ?? 0);
    const msg = asPoolMessage({ ...C.message, sceneHash: h, cloudBp: String(cloudBp), notCloudy: scene.notCloudy });
    for (const e of C.entries) {
      let rec = "";
      try { rec = await recoverTypedDataAddress({ domain: C.domain, types: POOL_TYPES, primaryType: "CleanPass", message: msg, signature: e.signature }); }
      catch (err) { rec = "unrecoverable"; }
      const want = regAddr(e.org);
      const ok = rec.toLowerCase() === want.toLowerCase();
      allOk = check(list, ok, ok ? `${wtag(e.org)} <span class="sim">[SIM]</span> · CleanPass ECDSA (EIP-712) recovers to its key in the KeyRegistry record <code>${short(want)}</code>`
        : `${wtag(e.org)} <span class="sim">[SIM]</span> · ECDSA recovers to <code>${short(rec)}</code>, not its registered <code>${short(want)}</code> → <b>InvalidSignature</b>`) && allOk;
      const pqOk = (await pqAnchored(e.org, e.mldsa44.publicKey)) && ml_dsa44.verify(hexBytes(e.mldsa44.sig), enc.encode(canon), hexBytes(e.mldsa44.publicKey), { context: PQ_CTX });
      allOk = check(list, pqOk, pqOk ? `${wtag(e.org)} · ML-DSA-44 over the scene bytes verifies` : `${wtag(e.org)} · ML-DSA-44 does not verify over the edited scene`) && allOk;
    }
    const v = $("cloud-verdict");
    v.className = allOk ? "verdict" : "verdict bad";
    v.textContent = allOk ? "Untouched: both witnesses' signatures verify over this exact scene."
      : "Both signatures fail — the pool would revert and no earn-back could move. This is the two-sided failure: an earn-back breaks exactly like a rebate.";
    return allOk;
  }
  for (const i of [pctIn, assIn, flag]) i.addEventListener("input", evaluate);
  const ct = C.cloudTamper;
  if (ct) {
    $("cloud-btn").textContent = `Tamper: paste in the cloud fields of the ${day(ct.sceneAt)} scene (${ct.cloudCoverPct} / ${ct.cloudCoverPctAssessed} %, not_cloudy ${ct.notCloudy}, refused as ${PLAIN[ct.refusal] || ct.refusal})`;
    TAMPER_ACTIONS.C = () => {
      pctIn.value = ct.cloudCoverPct; assIn.value = ct.cloudCoverPctAssessed; flag.value = String(ct.notCloudy); return evaluate();
    };
    $("cloud-btn").addEventListener("click", TAMPER_ACTIONS.C);
  } else {
    $("cloud-btn").hidden = true;
  }
  $("cloud-reset").addEventListener("click", () => { reset(); evaluate(); });
  return evaluate();
}

// ---------------------------------------------------------------- mode strip + personas
function renderStrip(d, live) {
  const s = $("strip-line");
  s.innerHTML = "";
  s.append(el("strong", {}, "Sources of this page: "),
    `data from Carbon Mapper and UNEP MARS (as of ${d.modeStrip.marsAsOf}) · chain: Ethereum Sepolia`,
    live ? ` (live, block ${fmt.format(live.block)})` : " (committed snapshot)", " and a local replay ", el("span", { class: "sim" }, "[SIM clock]"),
    ` · model: ${d.modeStrip.model === "RULE_IS_ENOUGH" ? "the persistence rule is enough" : d.modeStrip.model} · stake: the low bound only, a projection.`);
  if (d.mock.length) s.append(" ", el("span", { class: "mockb", title: d.mock.join(", ") }, `MOCK data in ${d.mock.length} block(s)`));
  $("mock-legend").hidden = !d.mock.length;
}

function personaSummary(name, d, live, box) {
  box.innerHTML = "";
  const s = d.sepolia, prop = s.run.steps.find((x) => x.step === "proposeEpisode");
  const dza = d.ledger.DZA_S_012.clean, tkm = d.ledger.TKM_S_276;
  const st = live?.episode?.stateName || "PROPOSED";
  const dt = (s) => el("span", { class: "date" }, s);
  const stat = (lbl, val, ...tags) => el("div", { class: "stat" }, el("span", { class: "lbl" }, lbl), el("span", { class: "val small" }, val, ...tags));
  const grid = el("div", { class: "cov" });
  const clk = () => el("span", { class: "simq", title: "simulated" }, "local test chain, pinned clock");
  let title, lede;
  if (name === "importer") {
    title = "Buyer: which sources owe me a rebate, and what do I file?";
    lede = "Which contracted sources had a confirmed leak, what rebate is due from the seller's bond, and what to file. The plumes, the MARS labels, the signatures and the chain execution are real; seller and buyer are simulated parties.";
    grid.append(
      stat("TKM_S_276 · Ethereum Sepolia", el("span", {}, `confirmed leak #${prop.episodeId} `, el("span", { class: `state ${st}` }, st), ` · rebate ${ethOf(prop.rebateWei)}`)),
      stat("TKM_S_276 · earn-back", el("span", {}, "no clean pass since ", dt(tkm.postMars.since))),
      stat("DZA_S_012 · earn-back", el("span", {}, `${dza.n} clean passes, then the new leak of `, dt(d.ledger.DZA_S_012.relapse.sceneAt.slice(0, 10)), " stops what is pending"), mockBadge(dza.provenance)),
      stat("What to file", "the IEA Annex D export, round-trip checked in the drawer below"));
  } else if (name === "seller") {
    title = "Seller: what I owe, what I earned back, what is near the floor";
    lede = "Read-only. The seller cannot delete a signed sighting; it can only appeal it, and then the independent witnesses must confirm it again. It earns part of a rebate back only from clean passes that independent witnesses sign.";
    const near = Object.values(d.cases).map((c) => {
      const q = c.cmPlumes.filter((p) => p.emission_auto != null);
      const last = q[q.length - 1];
      return last ? { name: c.source, rate: Math.round(last.emission_auto), day: last.scene_timestamp.slice(0, 10) } : null;
    }).filter(Boolean).sort((a, b) => a.rate - b.rate);
    grid.append(
      stat("I owe (the clause on Ethereum Sepolia)", `${ethOf(prop.rebateWei)} on TKM_S_276, confirmed leak #${prop.episodeId} ${st}`),
      stat("I earned back (DZA_S_012)", `${dza.n} clean passes before the new leak; the steps are in the story above and the transactions in the full record`, mockBadge(dza.provenance)),
      stat("Windows", `appeal ${d.policy.challengeWindow / 3600} h · contest ${d.policy.contestWindow / 3600} h · link ${d.policy.linkWindow / 86400} days`),
      stat("Closest to the clause's minimum rate", near.map((x) => `${x.name} ${fmt.format(x.rate)} kg/h (${x.day}) = ${(x.rate / d.policy.minRateKgH).toFixed(1)}× ${fmt.format(d.policy.minRateKgH)}`).join(" · ")));
  } else if (name === "observer") {
    title = "Witness: Fetch → Validate → Seal → Sign → Re-check";
    lede = "The five steps, replayed from the committed run and re-checked in this browser, are in the drawer below; the three tampers above are what a witness's signature must survive.";
    const e = d.hybrid.entries;
    grid.append(
      stat("Signed sightings in the demo", `${e.map((x) => x.org).join(" + ")} on ${e[0].bundle.plumeId}`),
      stat("Keys", "ECDSA secp256k1 on chain + ML-DSA-44 off chain ", el("span", { class: "simq", title: "simulated" }, "run by one person")),
      stat("Quorum", `${d.policy.k} of ${d.policy.n} distinct witness organisations, never the seller's or the buyer's`),
      stat("Open slot", "OBS_INDEPENDENT in the judge sandbox: anyone can register a key"));
  } else {
    title = "Sponsor: what the earn-back pool holds, reserves, releases and pays the witnesses";
    lede = "A lender or the buyer funds the earn-back pool. It releases only capped, cancellable steps for clean passes; a later confirmed leak cancels what is pending.";
    const ra = d.replay?.variants?.A?.sponsor, rb = d.replay?.variants?.B?.sponsor;
    if (ra) {
      grid.dataset.ledger = "replay";
      grid.append(
        stat("Funded (DZA_S_012, outcome A)", `the buyer opened ${ethOf(ra.openedByBuyerWei)}; the sponsor topped up ${ethOf(ra.sponsorTopUpWei)} for the witnesses' fees `, clk()),
        stat("Pool now (A)", `balance ${ethOf(ra.balanceWei)} · reserved ${ethOf(ra.reservedWei)} · released ${ethOf(ra.releasedWei)} to the seller's surety (the guarantor that posted its bond) · forfeited ${ethOf(ra.forfeitedWei)}`),
        stat("Witness fees paid (A)", `${ethOf(ra.observerFeesWei)}, split among the signing organisations' controllers`),
        stat("Remainder (A)", ra.sponsorWithdrawn ? `withdrawn by the sponsor: ${ethOf(ra.sponsorShareWei)}` : `${ethOf(ra.unreservedWei)} unreserved; after the pool closes each funder takes back a pro-rata share with sponsorWithdraw — the sponsor's is ${ethOf(ra.sponsorShareWei)} (not called in the replay)`));
      if (rb) {
        grid.append(stat("Outcome B", `top-up ${ethOf(rb.sponsorTopUpWei)} · released ${ethOf(rb.releasedWei)} · forfeited ${ethOf(rb.forfeitedWei)} · witness fees ${ethOf(rb.observerFeesWei)} · remainder ${ethOf(rb.unreservedWei)}, the sponsor's share ${ethOf(rb.sponsorShareWei)} `,
          el("span", { class: "simq", title: "simulated" }, "look-ahead")));
      }
    } else {
      grid.append(stat("Pool", "run make demo: the sponsor's numbers come from the DZA_S_012 replay"));
    }
    grid.append(stat("Evidence so far (DZA_S_012)", `${dza.n} clean passes`, mockBadge(dza.provenance)));
  }
  box.append(el("h3", {}, title), el("p", { class: "sub" }, lede), grid);
}

async function selfChecksV2(d, results) {
  const list = $("self-checks");
  const C = d.cleanPassDemo, vec = C.vector;
  const vd = hashTypedData({ domain: C.domain, types: POOL_TYPES, primaryType: "CleanPass", message: asPoolMessage(vec.message) });
  check(list, vd === vec.digest, `CleanPass EIP-712 parity: this browser's digest <code>${short(vd)}</code> equals the Python/Foundry test vector`);
  check(list, results.cloud, "Qualified clean pass <span class=\"sim\">[SIM] signers</span>: two witnesses' ECDSA and ML-DSA-44 signatures verify over the scene record (made in Python, checked here)");
  check(list, results.missing, `Detection list of ${d.detectionList.source}: hash equals the witness's re-fetch receipt`);
  check(list, results.observer, "Witness five steps replayed: every check passes on the committed run");
  check(list, null, d.mock.length ? `Mock inputs rendered with a MOCK badge: ${d.mock.join(", ")}` : "No mock input on this page: every block reads committed data files");
  const t = $("tile-check") || el("li", { id: "tile-check" });
  list.append(t);
  // pending until the tiles load or 4 s pass: the header pill shows one final count, never 8 then 9
  const t0 = Date.now();
  const upd = () => {
    const done = tileStatus.loaded > 0 || Date.now() - t0 >= 4000;
    t.className = tileStatus.loaded ? "pass" : "info";
    t.textContent = tileStatus.loaded
      ? `Carbon Mapper CH₄ tiles: ${tileStatus.loaded} loaded across ${tileStatus.layers} scenes (keyless tile layer, no CORS needed)`
      : `Carbon Mapper CH₄ tiles: ${tileStatus.failed ? "unavailable from this network" : done ? "not loaded within 4 s from this network" : "loading"}`;
    if (done) delete t.dataset.pending; else t.dataset.pending = "1";
    selfCheckPill();
    if (!done) setTimeout(upd, 400);
  };
  upd();
}

// ---------------------------------------------------------------- waiting windows (skip controls)
const dur = (secs) => {
  const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600), m = Math.floor((secs % 3600) / 60), x = Math.floor(secs % 60);
  return d ? `${d} d ${h} h ${m} min` : h ? `${h} h ${m} min ${x} s` : `${m} min ${x} s`;
};
const daysTxt = (secs) => {  // same rule as demo.py _days: minutes under a day
  const minutes = Math.max(1, Math.round(secs / 60));
  if (minutes < 1440) return `${minutes} min`;
  const n = (minutes / 1440).toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return `${n} ${n === "1" ? "day" : "days"}`;
};

function localAnvil() {
  const u = new URLSearchParams(location.search).get("anvil");
  if (!u) return null;
  try { const h = new URL(u).hostname; return ["127.0.0.1", "localhost", "[::1]"].includes(h) ? u : null; } catch (e) { return null; }
}
async function rpc(url, method, params = []) {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) });
  const j = await r.json();
  if (j.error) throw new Error(j.error.message);
  return j.result;
}

function setupClock(d, live) {
  const C = d.demoClock || {}, ol = $("clock-windows");
  ol.innerHTML = "";
  const after = { "challenge window": `anyone finalizes → CONFIRMED; the buyer withdraws ${d.demo.rebate_eth} ETH` };
  const rsk = d.replay?.skippedSeconds || {};
  const sk = Object.fromEntries([...Object.entries(rsk).map(([w, x]) => [`DZA replay · ${w}`, x]),
    ...Object.entries(C.skippedSeconds || {}).map(([w, x]) => [`v1 run · ${w}`, x])]);
  const names = Object.keys(sk);
  if (!names.length) ol.append(el("li", {}, "The recorded run skipped no window."));
  for (const w of names) {
    const li = el("li", { class: "win" });
    const res = el("p", { class: "sub win-res" });
    const btn = el("button", { class: "btn", type: "button" }, `Skip this window (${daysTxt(sk[w])})`);
    btn.addEventListener("click", () => {
      btn.disabled = true;
      li.classList.add("skipped");
      const bare = w.split(" · ")[1];
      res.textContent = `[SIM: chain clock advanced ${daysTxt(sk[w])} — ${bare}] Then: ${(w.startsWith("v1") && after[bare]) || "the next step of the recorded run"}.`;
    });
    li.append(el("strong", {}, w), " ", el("span", { class: "sim" }, "[SIM clock]"), el("br"), btn, res);
    ol.append(li);
  }
  const total = Object.values(sk).reduce((a, b) => a + b, 0);
  const g = C.clock?.genesis;
  const loc = $("clock-local");
  const rg = d.replay?.clock?.genesis;
  loc.textContent = `Recorded by make demo on two pinned clocks [SIM]: the DZA replay from ${rg ? new Date(rg * 1000).toISOString().slice(0, 10) : "—"}, then v1 from ${g ? new Date(g * 1000).toISOString().slice(0, 10) : "—"}; one second per block, ${daysTxt(total)} skipped in total (skippedSeconds in out/demo/replay.json and run.json). The clock is never set back.`;
  const anvil = localAnvil();
  if (anvil) {
    const lb = el("button", { class: "btn", type: "button" }, "Skip one challenge window on your local chain");
    const out = el("span", { class: "sub" });
    lb.addEventListener("click", async () => {
      try {
        const b = await rpc(anvil, "eth_getBlockByNumber", ["latest", false]);
        const now = Number(BigInt(b.timestamp)), target = now + d.policy.challengeWindow;  // forward only
        await rpc(anvil, "evm_setNextBlockTimestamp", [target]);
        await rpc(anvil, "evm_mine", []);
        out.textContent = ` [SIM: chain clock advanced ${daysTxt(target - now)} — challenge window] local chain time now ${new Date(target * 1000).toISOString()}`;
      } catch (e) { out.textContent = ` local chain unreachable: ${e.message}`; }
    });
    loc.append(el("br"), lb, out);
  }
  // Sepolia: the live episode's challenge deadline, counted down in real time
  const sep = $("clock-sepolia");
  const prop = d.sepolia.run.steps.find((x) => x.step === "proposeEpisode");
  const ep = live?.episode;
  if (!ep) {
    sep.append(el("p", {}, `Committed snapshot: episode ${prop.episodeId} was proposed on Sepolia (`, link(prop.tx, "tx"), "). Live countdown needs the Sepolia read."));
    return;
  }
  const deadline = Number(ep.deadline), stateP = el("p", { class: "countdown" }), note = el("p", { class: "sub" });
  sep.append(el("p", {}, `Main covenant, episode ${prop.episodeId}: `, el("span", { class: `state ${ep.stateName}` }, ep.stateName), " · ", link(prop.tx, "proposeEpisode tx")), stateP, note);
  const tick = () => {
    const left = deadline - Math.floor(Date.now() / 1000);
    if (ep.stateName !== "PROPOSED") { stateP.textContent = `Challenge window over: episode ${ep.stateName}.`; return false; }
    if (left > 0) { stateP.textContent = `Challenge window closes in ${dur(left)} (${new Date(deadline * 1000).toISOString().replace(".000", "")}).`; return true; }
    stateP.textContent = `Challenge window closed at ${new Date(deadline * 1000).toISOString().replace(".000", "")}; anyone may now finalize.`;
    return false;
  };
  note.textContent = `Real time on a public chain: no button moves this clock. The judge sandbox uses ${d.sepolia.sandboxWindows.challengeWindow / 60}-minute windows; the recorded local run shows the same loop completed.`;
  if (tick()) { const t = setInterval(() => { if (!tick()) clearInterval(t); }, 1000); }
}

// ---------------------------------------------------------------- verification time, manual vs tool
const SPEEDUP = 60;
function setupVerificationTime(d) {
  const v = d.verification || {}, prov = v.provenance || {};
  const sub = $("vtime-sub");
  sub.append(`The same question both ways for ${v.case || "the lead case"}: ${asWitness(v.question || "")} `, el("strong", {}, v.label || ""), " ", mockBadge(prov) || "");
  if (v.tool?.modeNote || v.manual?.mode) sub.append(el("br"), `Manual: ${v.manual?.mode || "—"}. Tool: ${v.tool?.modeNote || "—"}.`);
  const m = v.manual || { steps: [], n: 0 }, t = v.tool || { n: 0 };
  const ol = $("vtime-manual");
  for (const st of m.steps || []) {
    const med = m.stepMedianSeconds?.[st.id];
    ol.append(el("li", { "data-step": st.id }, st.text, med != null ? el("span", { class: "vt" }, ` median ${dur(med)}`) : ""));
  }
  const mnote = $("vtime-manual-note");
  if (!m.n) {
    mnote.textContent = "Not timed yet (manual protocol, five runs: make measure-verification-manual).";
    $("vtime-replay").disabled = true;
    $("vtime-replay-skip").disabled = true;
  } else {
    mnote.textContent = `n = ${m.n}, median ${dur(m.medianSeconds)} (range ${dur(m.minSeconds)}–${dur(m.maxSeconds)}).`;
    let timer = null;
    const items = [...ol.children];
    const finish = () => { clearInterval(timer); items.forEach((li) => li.classList.add("done")); mnote.textContent = `Manual: median ${dur(m.medianSeconds)} over n = ${m.n} runs.`; };
    $("vtime-replay").addEventListener("click", () => {
      clearInterval(timer);
      items.forEach((li) => li.classList.remove("done", "now"));
      let i = 0, left = m.stepMedianSeconds[m.steps[0].id], el0 = 0;
      items[0]?.classList.add("now");
      timer = setInterval(() => {
        el0 += SPEEDUP / 10; left -= SPEEDUP / 10;
        mnote.textContent = `Replaying at ${SPEEDUP}× speed: ${dur(el0)} elapsed`;
        if (left <= 0) {
          items[i].classList.replace("now", "done");
          i++;
          if (i >= items.length) { finish(); return; }
          items[i].classList.add("now");
          left = m.stepMedianSeconds[m.steps[i].id];
        }
      }, 100);
    });
    $("vtime-replay-skip").addEventListener("click", finish);
  }
  const tb = $("vtime-tool");
  tb.append(t.n
    ? el("p", {}, `Command line, fresh process each time: n = ${t.n}, median ${t.medianSeconds} s (range ${t.minSeconds}–${t.maxSeconds} s). `, el("span", { class: "sub" }, `${asWitness(t.what || "")}. ${t.machine || ""}; ${t.runs?.[0]?.mode || ""}.`))
    : el("p", { class: "sub" }, "Not measured yet: make measure-verification."));
  $("vtime-run").addEventListener("click", async () => {
    const list = $("vtime-checks"), watch = $("vtime-watch");
    list.innerHTML = "";
    const t0 = performance.now();
    let raf = 0;
    const tick = () => { watch.textContent = `${((performance.now() - t0) / 1000).toFixed(3)} s`; raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    const H = d.hybrid, rec = d.observerReplay.receipts;
    const domain = { name: "MethaneClause", version: "1", chainId: H.chainId, verifyingContract: H.verifyingContract };
    const results = [];
    for (const e of H.entries) {
      const canon = jcs(e.bundle);
      results.push([(await sha256hex(canon)) === e.evidenceHash, `${wtag(e.org)}: bundle hashes to the attested evidenceHash`]);
      const r = await recoverTypedDataAddress({ domain, types: TYPES, primaryType: "Attestation", message: asMessage(e.attestation), signature: e.signature });
      results.push([r.toLowerCase() === regAddr(e.org).toLowerCase(), `${wtag(e.org)}: ECDSA recovers to the key in the KeyRegistry record`]);
      results.push([(await pqAnchored(e.org, e.mldsa44.publicKey)) && ml_dsa44.verify(hexBytes(e.mldsa44.sig), enc.encode(canon), hexBytes(e.mldsa44.publicKey), { context: PQ_CTX }), `${wtag(e.org)}: ML-DSA-44 verifies with the registry-anchored key`]);
    }
    results.push([rec.every((x) => x.contentSha256 === H.entries[0].bundle.providerContentSha256), "re-fetch receipts match the bundle's provider content hash"]);
    const ms = performance.now() - t0;
    cancelAnimationFrame(raf);
    watch.textContent = `${(ms / 1000).toFixed(3)} s`;
    for (const [ok, txt] of results) check(list, ok, txt);
    check(list, null, `This browser, this run: ${ms.toFixed(0)} ms for the hash and signature half of the tool path (the fetch runs in the witness's terminal).`);
  });
}

// ---------------------------------------------------------------- explorer table, judge path, deep links
// front word for committed labels (cure-arc step names, the verification record): an observer organisation is a witness
const asWitness = (t) => String(t).replace(/\b([Oo])bserver(s?)\b/g, (_, o, pl) => `${o === "O" ? "W" : "w"}itness${pl ? "es" : ""}`);
const txHash = (url) => (String(url).match(/0x[0-9a-fA-F]{64}/) || [""])[0].toLowerCase();
const STEP_LABEL = {
  createCovenant: () => el("span", {}, "createCovenant: the seller ", el("span", { class: "sim" }, "[SIM]"), " posts the bond"),
  proposeEpisode: () => "proposeEpisode: two witnesses' signatures, rebate held",
};

// each receipt from the first endpoint that has it (publicnode prunes older receipts; the fallbacks keep them)
async function liveReceipts(d) {
  const s = d.sepolia, clients = rpcList(s).map((u) => [hostOf(u), rpcClient(u)]);
  const status = {}, via = {};
  await Promise.all(s.run.steps.map(async (x) => {
    const h = txHash(x.tx);
    for (const [name, c] of clients) {
      try {
        const r = await c.getTransactionReceipt({ hash: h });
        status[h] = r.status === "success" ? 1 : 0;
        via[name] = (via[name] || 0) + 1;
        return;
      } catch (e) { /* not on this endpoint: try the next */ }
    }
  }));
  const n = Object.keys(status).length;
  return { ok: n === s.run.steps.length, read: n, status, via, tried: clients.map(([h]) => h) };
}

function renderExplorer(d) {
  const s = d.sepolia, ex = s.explorer, run = s.run, rc = d.receipts;
  const n0 = run.steps.filter((x) => x.status === 0).length;
  const all = d.measurements?.transactions?.logged;
  $("explorer-sub").textContent = `${all ? `${run.steps.length} of our ${all} Sepolia transactions (all in data/derived/sepolia_txs.json)` : `${run.steps.length} transactions`} on ${run.network} (chain ${run.chainId}), ${n0} of them rejected with receipt status 0 and the bond unchanged. Recorded in data/derived/testnet_run.json (main deployment) and data/derived/sepolia_txlog.jsonl (the judge sandbox [SIM] rows); the "receipt now" column is each receipt read from the public RPC when this page loaded.`;
  const t = $("explorer-tx");
  t.innerHTML = "";
  t.append(el("tr", {}, el("th", {}, "Transaction"), el("th", {}, "Recorded"), el("th", {}, "Receipt now"), el("th", { class: "num" }, "Gas"), el("th", {}, "Explorer")));
  for (const x of run.steps) {
    const h = txHash(x.tx), now = rc?.status?.[h];
    const rec = x.status === 1 ? el("span", { class: "state CONFIRMED" }, "status 1")
      : el("span", {}, el("span", { class: "state REVERTED" }, "status 0"), ` ${x.reason}`);
    const live = now === undefined ? el("span", { class: "sub" }, "not read")
      : el("span", { class: now === x.status ? "ok-t" : "bad-t" }, `${now === x.status ? "✓" : "✕"} status ${now}`);
    t.append(el("tr", { "data-tx": h },
      el("td", {}, x.status === 0 ? `rejected: ${REJECT_WHY[x.step] || x.step}` : STEP_LABEL[x.step]?.() || asWitness(x.step)),
      el("td", {}, rec), el("td", { class: "xlive" }, live), el("td", { class: "num" }, fmt.format(x.gasUsed)), el("td", {}, link(x.tx, short(h, 10, 6)))));
  }
  const a = $("explorer-addr");
  a.innerHTML = "";
  a.append(el("tr", {}, el("th", {}, "What"), el("th", {}, "Address")));
  const sb = s.sandbox, sim = () => el("span", { class: "sim" }, "[SIM]");
  const rows = [
    [["MethaneCovenant (main, verified source)"], s.methaneCovenant, "#code"],
    [["KeyRegistry (main)"], s.keyRegistry, "#code"],
    [["MethaneCovenant (judge sandbox, published keys) ", sim()], sb.methaneCovenant, "#code"],
    [["KeyRegistry (judge sandbox) ", sim()], sb.keyRegistry, "#code"],
    [["Seller ", sim()], s.seller, ""],
    [["Buyer ", sim()], s.buyer, ""],
    ...Object.entries(s.observers).map(([org, addr]) => [[`Witness ${org} (prototype key) `, sim()], addr, ""]),
  ];
  for (const [what, addr, frag] of rows) {
    a.append(el("tr", {}, el("td", {}, ...what), el("td", {}, link(`${ex}/address/${addr}${frag}`, addr))));
  }
}

function renderJudgePath(d, live) {
  const s = d.sepolia, ex = s.explorer, run = s.run;
  const prop = run.steps.find((x) => x.step === "proposeEpisode"), tam = run.steps.find((x) => x.step === "reject_tamper");
  const rejected = run.steps.filter((x) => x.status === 0);
  const ol = $("judge-steps");
  ol.innerHTML = "";
  const code = (t) => el("code", {}, t);
  const here = (href, text) => el("a", { href, class: "deep" }, text);
  const step = (title, open, sees, ...verify) => {
    const v = el("ul", { class: "jverify" });
    for (const x of verify) v.append(el("li", {}, ...x));
    ol.append(el("li", {},
      el("div", { class: "jtitle" }, el("strong", {}, title), open ? " " : null, open),
      el("p", { class: "jsee" }, el("span", { class: "lbl" }, "You see: "), ...sees),
      el("div", { class: "jv" }, el("span", { class: "lbl" }, "Check it yourself:"), v)));
  };
  const st = live?.episode?.stateName || "PROPOSED";
  step("Read the covenant live from Ethereum Sepolia", null,
    [live ? `the header reads block ${fmt.format(live.block)}; episode ${run.episodeId} is ` : "the committed snapshot (the live read failed from this network); episode ",
      el("span", { class: `state ${st}` }, st), live ? `, bond ${ethOf(live.covenant.remaining)} remaining.` : "."],
    ["Explorer: ", link(`${ex}/address/${s.methaneCovenant}#readContract`, "MethaneCovenant, Read Contract"), ` → getEpisode(${run.episodeId}) · `, link(prop.tx, "proposeEpisode tx")],
    ["Repository: ", code("contracts/deployments/11155111.json"), " (addresses) and ", code("data/derived/testnet_run.json"), " (every transaction)"]);
  step("Edit the signed rate", here("?tamper=A", "open with tamper A"),
    ["the canonical hash changes and both witnesses' ECDSA and ML-DSA-44 checks turn red."],
    ["Explorer: the same edit sent to Sepolia with the real signatures, ", link(tam.tx, `status 0, ${tam.reason}`)],
    ["Command: ", code("make demo"), " (local chain: the tampered submission is a real transaction that reverts)"]);
  step(`Remove a detection from ${d.detectionList.source}'s list`, here("?tamper=B", "open with tamper B"),
    ["the list no longer hashes to the witness's re-fetch receipt, so the witness refuses to sign."],
    ["Repository: ", code(d.detectionList.from.split(" ")[0]), "; RFC 8785 + SHA-256 of its quantified plumes is the receipt hash"]);
  step("Edit a scene's cloud fields (the refund side)", here("?tamper=C", "open with tamper C"),
    ["the sceneHash inside the signed clean pass changes and both signatures fail: a refund breaks exactly like a rebate."],
    ["Repository: ", code(d.cleanPassDemo.provenance.path), " (the scene) and ", code(d.cleanPassDemo.vector.path), " (the digest this browser re-derives)"],
    ["Command: ", code("make test-contracts"), " (the same CleanPass digest in Foundry)"]);
  step(`Open the ${rejected.length} rejections on the explorer`, here("#explorer", "go to the table"),
    [`${rejected.map((x) => x.reason).join(", ")}: each a real Sepolia transaction with receipt status 0; the table re-reads every receipt.`],
    ["Explorer: ", ...rejected.flatMap((x, i) => [i ? " · " : "", link(x.tx, x.reason)])]);
  step("Sign with your own key", here("#witness", "on this page: Become a witness"),
    ["your wallet joins the judge sandbox ", el("span", { class: "sim" }, "[SIM]"), " as witness OBS_JUDGE, so one of the two signatures is one we do not hold."],
    ["Command: ", code("make judge-attest"), " then ", code("make judge-propose"), " with ", code("JUDGE_PK"), " set; without test ETH, ", code("make judge-local")],
    ["Explorer: ", link(`${ex}/address/${s.sandbox.methaneCovenant}`, "sandbox MethaneCovenant")]);
  step("File it", here("#export", "go to the export"),
    ["IEA Annex D, the seller's Annex C and the credit note, each re-hashed and round-tripped through CSV in this browser."],
    ["Repository: ", code("data/derived/export/TKM_S_276.annex_d.json"), "; command: ", code("make export")]);
}

// each tamper's own buttons: after its Reset the page no longer says that tamper is applied (body[data-tamper], the
// deep-link note); tampering again hides the note, the verdict under the tamper speaks for itself
const TAMPER_BUTTONS = { A: ["tamper-btn", "reset-btn"], B: ["miss-btn", "miss-reset"], C: ["cloud-btn", "cloud-reset"] };
function watchTamperResets() {
  const note = $("deeplink-note");
  for (const [key, [edit, reset]] of Object.entries(TAMPER_BUTTONS)) {
    $(reset).addEventListener("click", () => {
      if (document.body.dataset.tamper === key) delete document.body.dataset.tamper;
      if (note.dataset.key !== key || note.hidden) return;
      note.textContent = `Reset: tamper ${key} is undone and its checks are green again. The link (?tamper=${key}) applied it when the page opened; the button below applies it again.`;
      note.dataset.state = "reset";
    });
    $(edit).addEventListener("click", () => { if (note.dataset.key === key) note.hidden = true; });
  }
}

async function applyDeepLink() {
  watchTamperResets();
  const q = new URLSearchParams(location.search).get("tamper");
  if (!q) return;
  const key = { a: "A", rate: "A", b: "B", missing: "B", c: "C", cloud: "C" }[q.toLowerCase()];
  const note = $("deeplink-note");
  note.hidden = false;
  if (!key || !TAMPER_ACTIONS[key]) {
    note.textContent = `Unknown tamper "${q}" in the link: nothing was edited. Use ?tamper=A, B or C.`;
    return;
  }
  const box = $(`tamper-${key.toLowerCase()}-box`);
  if (box) box.open = true;
  await TAMPER_ACTIONS[key]();
  document.body.dataset.tamper = key;
  note.dataset.key = key;
  note.dataset.state = "applied";
  note.textContent = `Opened with ?tamper=${key}: tamper ${key} is applied below and its checks are red. The self-checks at the bottom ran first, on the untouched data. Reset restores it.`;
  const h = $(`tamper-${key.toLowerCase()}`);
  (box || h).querySelector("summary") ? box.querySelector("summary").after(note) : h.after(note);  // the note sits under the tamper it describes
  // A lands on the three-step proof strip, whose red third step is tamper A's verdict (its heading and note follow it);
  // B and C do not drive the strip, so they land on their own section, where their note and red verdict sit
  const target = key === "A" ? $("moment") : h;
  requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
}

async function buildLine() {
  try {
    const r = await fetch("BUILD.json");
    if (!r.ok) return;
    const b = await r.json(), line = $("build-line");
    if (b.dev) return;  // app/BUILD.json is a stub; make pages writes the real record
    line.append("This copy of the page is a static export (make pages) of commit ", el("code", {}, b.sourceCommit.slice(0, 12)),
      b.sourceDirty ? " with uncommitted changes to app/" : "", "; BUILD.json lists the SHA-256 of every file it serves.");
    line.hidden = false;
  } catch (e) { /* served from app/: no export record */ }
}

// ================================================================ layer 1: four actors, two stories on real data
const simw = (w) => el("span", { class: "simw", title: "simulated party in this demo" }, w);
const ICON = {
  seller: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 21V10l6-4v4l6-4v4l6-4v15zM7 17h2M12 17h2M17 17h1"/></svg>',
  buyer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 10l9-6 9 6M5 10v9h14v-9M10 19v-5h4v5"/></svg>',
  witness: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  sponsor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/></svg>',
};
const ACTORS = [  // persona ids kept for ?persona= deep links (importer = the buyer, witness = the witnesses)
  { id: "seller", persona: "seller", name: "Seller", role: () => "Gas exporter. Posts a bond for named sources; earns part of a rebate back with clean passes." },
  { id: "buyer", persona: "importer", name: "Buyer", role: () => "EU gas importer. Gets the rebate when a leak is confirmed, and files the evidence." },
  { id: "witness", persona: "observer", name: "Witnesses", role: (d) => `Independent organisations. Each fetches the public satellite record, seals it and signs; the clause needs ${d.policy.k} of ${d.policy.n}.` },
  { id: "sponsor", persona: "sponsor", name: "Sponsor", role: () => "The seller's lender, as a sustainability-linked margin step, or the buyer. Funds the earn-back pool and pays the witnesses' fees." },
];
function renderActors(d, live) {
  const ul = $("actors");
  ul.innerHTML = "";
  for (const a of ACTORS) {
    const view = el("div", { class: "view", id: `view-${a.persona}` });
    const det = el("details", { class: "actor-more", "data-persona": a.persona }, el("summary", {}, "What they see"), view);
    personaSummary(a.persona, d, live, view);
    ul.append(el("li", { class: "actor sim", id: `actor-${a.id}` },
      el("div", { class: "actor-h" }, el("span", { class: "ico", html: ICON[a.id] }), el("h3", {}, a.name)),
      el("p", {}, a.role(d)), det));
  }
}

const PLAIN = {
  CLOUD_FLAG: "cloudy", CLOUD_FLAG_MISMATCH: "cloud fields disagree", NO_ORIGIN_INTERSECT: "missed the source",
  SCENE_BEFORE_SINCE: "before the claim began", PLUME_ON_SCENE: "a plume on the scene", MARS_PLUME_NEARBY: "a MARS plume nearby",
  FLOOR_MISMATCH: "satellite could not see a leak this size", BEFORE_PUBLICATION_LAG: "too recent to sign",
  AFTER_TERM: "after the clause ended", DUPLICATE_SCENE: "scene already used", SCENE_NOT_PUBLISHED: "not published yet",
  UNKNOWN_INSTRUMENT: "unknown satellite", FUTURE: "dated in the future",
  InvalidSignature: "an edited record", SelfAttestation: "the seller signing as a witness", NotCovered: "a source outside the clause",
  Replay: "the same signatures twice", SourceUnderReview: "a new leak under review",
};
const plain = (code) => el("span", { class: "plain", title: code }, PLAIN[code] || code);
const SAT = (inst) => (inst === "tan" ? "Tanager-1" : inst === "emi" ? "EMIT (on the ISS)" : inst);
const iso = (unix) => new Date(Number(unix) * 1000).toISOString().replace(".000Z", " UTC").replace("T", " ");
const localTx = (tx) => el("span", {}, el("code", { title: tx }, short(tx)), " ", el("span", { class: "simq", title: "simulated" }, "local run, pinned clock"));

function chapters(d, live) {
  const s = d.sepolia, steps = s.run.steps, st = (k) => steps.find((x) => x.step === k);
  const T = d.ledger.TKM_S_276, Z = d.ledger.DZA_S_012, tk = d.cases.TKM_S_276, dz = d.cases.DZA_S_012;
  const create = st("createCovenant"), prop = st("proposeEpisode"), fin = st("finalize"), wd = st("withdraw_buyer"), wp = st("withdraw_proposer");
  const rejected = steps.filter((x) => x.status === 0 && !x.source);
  const cure = steps.filter((x) => x.source === "judge cure arc");
  const k = d.policy.k, n = d.policy.n, win = d.policy.challengeWindow;
  const A = d.replay?.variants?.A, out = A?.outcome, sp = A?.sponsor, rules = d.cleanLook, c = Z.clean;
  const state = live?.episode?.stateName;
  const F = (label, ...v) => el("li", {}, el("span", { class: "lbl" }, label), ...v);
  const tkmSince = T.postMars.since;
  const recent = tk.cmPlumes.filter((p) => p.scene_timestamp.slice(0, 10) >= tkmSince && p.emission_auto != null);
  const tkmMap = (until, extra = {}) => ({ caseName: "TKM_S_276", facility: T.mars.facility, plumes: tk.cmPlumes, since: tkmSince, until, ...extra });
  const dzaMap = (until, extra = {}) => ({ caseName: "DZA_S_012", facility: Z.mars.facility, plumes: dz.cmPlumes, since: Z.leak.sceneAt.slice(0, 10), until, ...extra });
  const pc = (bp) => `${bp / 100} %`;
  const inst = rules.installments;
  const reached = (i) => c.n >= i.afterPasses && (!i.twoInstruments || c.instruments.length >= 2) && !i.isFinal;
  const leakDay = T.leak.sceneAt.slice(0, 10), leakAt = { scene: T.leak, focus: [T.leak.lat, T.leak.lon] };
  const ch = [];
  // ---- penalty arc: TKM_S_276, "Marked Absent"
  ch.push({ arc: "tkm", short: "Marked absent", head: `The registry marks it “${T.mars.category}”.`,
    body: [`UNEP IMEO MARS last recorded a plume at this source (${T.sourceType.toLowerCase()}, ${T.country}) on ${T.mars.lastPlume}. Its record today labels the source “${T.mars.category}”. Nothing in a gas contract moves.`],
    facts: [F("UNEP MARS", `label “${T.mars.category}” · last MARS plume ${T.mars.lastPlume}`)],
    map: tkmMap(T.mars.lastPlume), card: () => moneyCard("tkm", 0, d) });
  ch.push({ arc: "tkm", short: "Still seen", head: `Satellites keep seeing it: ${T.postMars.detected} of ${T.postMars.passes}.`,
    body: [`Since MARS's last plume, Carbon Mapper looked at the sources within ${T.postMars.radiusKm} km of the facility on ${T.postMars.passes} satellite passes and saw a plume on ${T.postMars.detected}.`],
    facts: [F("Carbon Mapper", `a plume on ${T.postMars.detected} of ${T.postMars.passes} passes since ${tkmSince}`),
      F("Latest quantified plumes", recent.slice(-3).reverse().map((p) => `${p.scene_timestamp.slice(0, 10)} ${fmt.format(Math.round(p.emission_auto))} kg/h`).join(" · ")),
      F("At stake", `${d.stake.kt_ch4_per_yr.low} kt CH₄ a year at this source, the low bound of the band — a stake, never a measured cut`)],
    map: tkmMap(leakDay, { sky: "both" }), card: () => moneyCard("tkm", 0, d) });
  ch.push({ arc: "tkm", short: "The clause", head: "A buyer writes methane into the deal.",
    body: ["The ", simw("buyer"), " and the ", simw("seller"), ` agree: if ${k} of ${n} independent witnesses confirm a leak at a named source, the seller's bond pays the buyer a rebate. The seller posts the bond on Ethereum Sepolia.`],
    facts: [F("Bond posted", `${ethOf(create.bondWei)} · `, link(create.tx, "createCovenant tx")),
      F("Rules fixed first", "policyHash ", el("code", {}, short(s.run.policyHash, 10, 4)), " committed before any evidence"),
      F("Clause term", `from ${day(s.run.covenantStart)} `, el("span", { class: "simq", title: "simulated" }, "start backdated"))],
    map: tkmMap(leakDay), card: () => moneyCard("tkm", 1, d) });
  ch.push({ arc: "tkm", short: "Caught again", head: `Caught again: ${fmt.format(T.leak.rateKgH)} kg/h.`,
    body: [`${leakDay} — ${SAT(T.leak.instrument)} sees ${fmt.format(T.leak.rateKgH)} ± ${fmt.format(T.leak.uncKgH)} kg/h at the facility. Carbon Mapper publishes it on ${T.leak.publishedAt.slice(0, 10)}; this is the plume the clause settles.`],
    facts: [F("Sighting", `${SAT(T.leak.instrument)} · scene ${T.leak.sceneName}`), F("Image", "methane enhancement on the map · Data by Carbon Mapper®")],
    map: tkmMap(leakDay, { ...leakAt, sky: T.leak.instrument }), card: () => moneyCard("tkm", 1, d) });
  ch.push({ arc: "tkm", short: "Witnesses sign", head: `${k} of ${n} witnesses sign. The contract checks.`, tamper: true,
    body: [`Two witness organisations each fetch the plume record, seal it (RFC 8785 + SHA-256) and sign it. The contract checks both signatures against its key registry and holds a tier ${prop.tier} rebate of ${ethOf(prop.rebateWei)} from the bond. We also sent ${rejected.length} bad proposals; the contract refused each one.`],
    facts: [F("Proposed", link(prop.tx, "proposeEpisode tx"), ` · rebate held ${ethOf(prop.rebateWei)}`),
      F("Refused", ...rejected.flatMap((x, i) => [i ? " · " : "", el("a", { href: x.tx, target: "_blank", rel: "noopener", title: x.reason }, PLAIN[x.reason] || x.reason)]))],
    map: tkmMap(leakDay, leakAt), card: () => moneyCard("tkm", 2, d) });
  ch.push({ arc: "tkm", short: "Appeal window", head: `Anyone can appeal for ${win / 3600} hours.`,
    body: [`Until ${iso(prop.deadline)}, anyone may appeal with a deposit and an objective reason; the `, simw("seller"), ` may contest, and then the rebate needs ${k + 1} of ${n} witnesses. No appeal stood: the window closed and the leak was finalized.`],
    facts: [F("Window opened by", link(prop.tx, "proposeEpisode tx")), F("Window closed", iso(prop.deadline)), F("Leak now", state ? `${state} (read live from Ethereum Sepolia)` : "CONFIRMED in the committed record")],
    map: tkmMap(leakDay, leakAt), card: () => moneyCard("tkm", 2, d) });
  ch.push({ arc: "tkm", short: "Paid", head: "Confirmed. The buyer is paid.",
    body: ["After the window anyone may finalize; the ", simw("buyer"), ` withdraws ${ethOf(wd.amountWei)} from the `, simw("seller"), "'s bond. No bank, registry or court had to approve it."],
    facts: [F("Finalized", link(fin.tx, "finalize tx")), F("Paid to the buyer", `${ethOf(wd.amountWei)} · `, link(wd.tx, "withdraw tx")),
      ...(wp ? [F("Proposer's deposit back", `${ethOf(wp.amountWei)} · `, link(wp.tx, "withdraw tx"))] : [])],
    map: tkmMap(leakDay, leakAt), card: () => moneyCard("tkm", 3, d) });
  // ---- earn-back arc: DZA_S_012
  const fe = A?.txs?.["finalize (episode)"]?.[0], rel = A?.txs?.release || [];
  const early = A?.txs?.["relapse while the episode is PROPOSED"]?.[0], cancel = A?.txs?.["relapse (cancel)"]?.[0];
  ch.push({ arc: "dza", short: "Leak confirmed", head: `A leak in ${Z.country}, confirmed.`,
    body: [`${Z.leak.sceneAt.slice(0, 10)} — ${SAT(Z.leak.instrument)} sees ${fmt.format(Z.leak.rateKgH)} ± ${fmt.format(Z.leak.uncKgH)} kg/h at a source in UNEP's record (${Z.sourceType.toLowerCase()}). The witnesses sign, the appeal window passes, the leak is confirmed and the `, simw("buyer"), " is rebated from the bond."],
    facts: [F("UNEP MARS", `label “${Z.mars.category}”`), F("Confirmed", fe ? localTx(fe.tx) : "run make demo")],
    map: dzaMap(Z.leak.sceneAt.slice(0, 10), { scene: Z.leak, sky: Z.leak.instrument, focus: [Z.leak.lat, Z.leak.lon] }), card: () => moneyCard("dza", 0, d) });
  const refusals = Object.entries(c.refusals).flatMap(([r, x], i) => [i ? ", " : "", `${x} `, plain(r)]);
  const cureLinks = cure.filter((x) => /clean pass/.test(x.step));
  const oc = A?.txs?.openClaim?.[0], bad = A?.txs?.["attestCleanPass (seeded bad pass)"]?.[0];
  const chal = A?.txs?.["challengePass CLOUD_FLAG"]?.[0], res = A?.txs?.["resolvePass WITHDRAW_CLEAN"]?.[0];
  ch.push({ arc: "dza", short: "Clean passes", head: `Clean passes start to count: ${c.n}.`,
    body: [`A later pass that sees no plume counts only on a clear-sky scene, from a satellite able to see a leak this size, with no MARS plume within ${rules.marsWindowDays} days — once ${k} witnesses sign it and its own appeal window passes. ${c.n} of ${c.candidates} candidate scenes counted (a lower bound).`],
    facts: [F("Refused", ...refusals),
      ...(oc ? [F("Earn-back claim opened", localTx(oc.tx))] : []),
      ...(bad && chal ? [F("A cloudy pass slipped in on purpose", "appealed as ", plain("CLOUD_FLAG"), " ", localTx(chal.tx), ...(res ? [" · withdrawn ", localTx(res.tx)] : []))] : []),
      ...(cureLinks.length ? [F("On Ethereum Sepolia too", ...cureLinks.flatMap((x, i) => [i ? " · " : "", link(x.tx, /counted/.test(x.step) ? "clean pass counted" : "clean pass signed")]),
        " ", el("span", { class: "simq", title: "simulated" }, "the author's test wallet"))] : []),
      F("Why a clean pass counts", el("a", { href: "#rules" }, "the rules"))],
    map: dzaMap(day(c.since), { sky: "both", focus: Z.mars.facility }), card: () => moneyCard("dza", 1, d) });
  ch.push({ arc: "dza", short: "Earn-back steps", head: `Earn-back: ${pc(inst[0].shareBp)} at ${inst[0].afterPasses} passes, ${pc(inst[0].shareBp + inst[1].shareBp)} at ${inst[1].afterPasses}.`,
    body: ["The earn-back pool, opened by the ", simw("buyer"), " and topped up by the ", simw("sponsor"), ", pays each step to the ", simw("seller"), `'s side — here its surety, which posted the bond: ${sp ? ethOf(sp.releasedWei) : "—"} in ${rel.length} steps. The last ${pc(inst[2].shareBp)} needs ${inst[2].afterPasses} passes, two satellites, a MARS corroboration and a ${rules.cureTermDays}-day cure term (about ${rules.cureTermMonths} months).`],
    facts: [F("Steps", ...inst.map((i, j) => el("span", { class: reached(i) ? "ok-t" : "bad-t" }, `${j ? " · " : ""}${i.afterPasses} passes → ${pc(i.shareBp)} ${reached(i) ? "✓" : "✕"}`))),
      F("Released", ...rel.flatMap((t, i) => [i ? " · " : "", localTx(t.tx)]))],
    map: dzaMap(day(c.since), { focus: Z.mars.facility }), card: () => moneyCard("dza", 2, d) });
  ch.push({ arc: "dza", short: "New leak", head: "A new leak stops the earn-back.",
    body: [`${Z.relapse.sceneAt.slice(0, 10)} — ${SAT(Z.relapse.instrument)} sees ${fmt.format(Z.relapse.rateKgH)} ± ${fmt.format(Z.relapse.uncKgH)} kg/h again. While the new leak is under review the claim is frozen; once it is confirmed, everything still pending is cancelled: paid ${out ? pc(out.paidBp) : "—"}, forfeited ${out ? pc(out.forfeitedBp) : "—"}. Released steps stay released.`],
    facts: [F("Cancel tried during review", early ? el("span", {}, "refused (", plain(early.revert), ") · ", localTx(early.tx)) : "—"),
      F("Cancelled", cancel ? localTx(cancel.tx) : "—"),
      F("Outcome", sp ? `${ethOf(sp.releasedWei)} paid back, ${ethOf(sp.forfeitedWei)} forfeited` : "—")],
    map: dzaMap(Z.relapse.sceneAt.slice(0, 10), { scene: Z.relapse, sky: Z.relapse.instrument, focus: [Z.relapse.lat, Z.relapse.lon] }), card: () => moneyCard("dza", 3, d) });
  return ch;
}

// the money in the clause at each chapter (committed amounts only)
function moneyCard(arc, at, d) {
  const box = el("div", { class: "mc" });
  const row = (label, val, cls = "") => el("div", { class: `mc-row ${cls}` }, el("span", {}, label), el("b", {}, val));
  if (arc === "tkm") {
    const steps = d.sepolia.run.steps, create = steps.find((x) => x.step === "createCovenant");
    const prop = steps.find((x) => x.step === "proposeEpisode"), wd = steps.find((x) => x.step === "withdraw_buyer");
    const bond = BigInt(create.bondWei), r = BigInt(prop.rebateWei);
    box.append(el("strong", {}, "Money in the clause"),
      row(at >= 2 ? "Seller's bond left" : "Seller's bond", at >= 1 ? ethOf(at >= 2 ? bond - r : bond) : "not posted yet"),
      row("Rebate held", at === 2 ? ethOf(r) : "—", at === 2 ? "held" : ""),
      row("Paid to the buyer", at === 3 ? ethOf(wd.amountWei) : "—", at === 3 ? "paid" : ""));
  } else {
    const inst = d.cleanLook.installments, c = d.ledger.DZA_S_012.clean, out = d.replay?.variants?.A?.outcome;
    const bar = el("div", { class: "mc-bar", role: "img", "aria-label": "Earn-back steps of the rebate" });
    let paid = 0;
    for (const i of inst) {
      const ok = at >= 2 && c.n >= i.afterPasses && (!i.twoInstruments || c.instruments.length >= 2) && !i.isFinal;
      if (ok) paid += i.shareBp;
      bar.append(el("span", { class: ok ? "ok" : at >= 3 ? "lost" : "", style: `flex:${i.shareBp}` }, `${i.shareBp / 100} %`));
    }
    box.append(el("strong", {}, "Earn-back of the rebate"), bar,
      row("Clean passes counted", at >= 1 ? String(c.n) : "0"),
      row(at >= 3 ? "Paid / forfeited" : "Paid back", at >= 3 && out ? `${out.paidBp / 100} % / ${out.forfeitedBp / 100} %` : `${paid / 100} %`, at >= 3 ? "held" : ""));
  }
  return box;
}

let stage = null;
function stageMap() {
  if (stage || !window.L) return stage;
  const m = L.map("stage-map", { scrollWheelZoom: false, attributionControl: false });
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 18 }).addTo(m);
  stage = { m, layer: L.layerGroup().addTo(m), cm: null, sceneId: null };
  maps.push(m);
  return stage;
}
function drawStage(c) {
  const st = stageMap(), mp = c.map;
  $("stage-attrib").textContent = "Imagery © Esri, Maxar, Earthstar Geographics · Data by Carbon Mapper® · UNEP IMEO MARS";
  if (!st) { $("stage-map").textContent = "Map library unavailable offline; the story panel has every value."; return; }
  const { m, layer } = st;
  layer.clearLayers();
  const [flat, flon] = mp.facility;
  L.circle([flat, flon], { radius: 1500, color: "#ffffff", weight: 1.2, dashArray: "4 5", fill: false }).addTo(layer);
  for (const p of mp.plumes) {
    if (p.lat == null) continue;
    const dday = p.scene_timestamp.slice(0, 10);
    if (dday > mp.until) continue;
    const recent = dday >= mp.since;
    L.circleMarker([p.lat, p.lon], { radius: recent ? 5 : 3.5, weight: 1, color: recent ? "#7c2d12" : "#57534e",
      fillColor: recent ? "#f97316" : "#d6d3d1", fillOpacity: 0.85 })
      .bindTooltip(`${dday} · ${p.instrument}${p.emission_auto == null ? "" : ` · ${fmt.format(Math.round(p.emission_auto))} kg/h`}`).addTo(layer);
  }
  L.circleMarker([flat, flon], { radius: 7, color: "#111827", weight: 2, fillColor: "#ffffff", fillOpacity: 1 })
    .bindTooltip(`${mp.caseName} · UNEP MARS source point`).addTo(layer);
  const sid = mp.scene?.sceneId || null;
  if (sid !== st.sceneId) {
    if (st.cm) { st.cm.remove(); st.cm = null; }
    if (sid) {  // Carbon Mapper's CH4 tiles as plain <img> tiles: no pixel access, so no CORS needed on a hosted page
      tileStatus.layers++;
      st.cm = L.tileLayer(CM_TILE(sid), { maxZoom: 18, opacity: 0.85, className: "cm-tiles" }).addTo(m);
      st.cm.on("tileload", () => { tileStatus.loaded++; });
      st.cm.on("tileerror", () => { tileStatus.failed++; });
    }
    st.sceneId = sid;
  }
  m.invalidateSize();
  m.setView(mp.focus || mp.facility, mp.scene ? 14 : 13, { animate: false });
  drawSky(mp);
}
// satellites in the sky: the pass dates and images are real; the drawn position is not (purple, dashed orbit)
function drawSky(mp) {
  const svg = $("stage-sky"), box = svg.getBoundingClientRect();
  const w = Math.max(240, Math.round(box.width)), h = Math.max(160, Math.round(box.height));
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.replaceChildren();
  if (!mp.sky) return;
  const ns = "http://www.w3.org/2000/svg";
  const mk = (t, a) => { const n = document.createElementNS(ns, t); for (const [k, v] of Object.entries(a)) n.setAttribute(k, v); return n; };
  svg.append(mk("path", { d: `M8,48 Q${w / 2},-10 ${w - 8},48`, class: "orbit" }));
  const note = mk("text", { x: w - 10, y: 66, "text-anchor": "end", class: "sky-t" });
  note.textContent = "drawn sky path";
  svg.append(note);
  const sats = mp.sky === "both" ? [["tan", 0.26], ["emi", 0.74]] : [[mp.sky, 0.3]];
  for (const [inst, fx] of sats) {
    const x = Math.round(w * fx), y = 34;
    svg.append(mk("polygon", { points: `${x},${y + 8} ${w / 2 - 12},${h / 2} ${w / 2 + 12},${h / 2}`, class: "beam" }));
    const g = mk("g", { class: "sat", transform: `translate(${x},${y})` });
    g.append(mk("rect", { x: -6, y: -6, width: 12, height: 12, rx: 2 }), mk("rect", { class: "panel", x: -22, y: -3, width: 14, height: 6 }),
      mk("rect", { class: "panel", x: 8, y: -3, width: 14, height: 6 }));
    const t = mk("text", { x, y: y + 24, "text-anchor": "middle", class: "sat-t" });
    t.textContent = SAT(inst);
    svg.append(g, t);
  }
}

let CH = [], chIdx = 0;
function renderChapter(i, focus) {
  chIdx = Math.max(0, Math.min(CH.length - 1, i));
  const c = CH[chIdx], arcCh = CH.filter((x) => x.arc === c.arc), j = arcCh.indexOf(c);
  $("ds-count").textContent = `${c.arc === "tkm" ? "Penalty case" : "Earn-back case"} · step ${j + 1} of ${arcCh.length}`;
  $("ds-arc").textContent = c.arc === "tkm" ? "Penalty · TKM_S_276" : "Earn-back · DZA_S_012";
  $("case-title").textContent = c.arc === "tkm" ? "“Marked Absent” case" : "Earn-back case · DZA_S_012";
  $("ds-head").textContent = c.head;
  $("ds-body").replaceChildren(...c.body);
  const facts = $("ds-facts");
  facts.replaceChildren(...c.facts);
  if (c.tamper) {
    const b = el("button", { class: "btn danger", type: "button" }, "Try the first one yourself");
    b.addEventListener("click", goCheat);
    facts.append(el("li", {}, b));
  }
  $("stage-card").replaceChildren(c.card());
  for (const b of document.querySelectorAll(".arc-btn")) b.setAttribute("aria-pressed", String(b.dataset.arc === c.arc));
  $("ds-dots").replaceChildren(...arcCh.map((x, n) => {
    const k = CH.indexOf(x);
    const b = el("button", { type: "button", class: "dot-btn" + (k === chIdx ? " now" : ""), "aria-label": `Step ${n + 1}: ${x.short}`, title: x.short });
    if (k === chIdx) b.setAttribute("aria-current", "step");
    b.addEventListener("click", () => renderChapter(k, true));
    return b;
  }));
  $("ds-prev").disabled = chIdx === 0;
  $("ds-next").disabled = chIdx === CH.length - 1;
  document.body.dataset.chapter = String(chIdx + 1);
  drawStage(c);
  if (focus) {
    $("ds-head").focus({ preventScroll: true });
    // phone: the map sits above the panel; bring it back into view when the chapter changes
    const st = document.querySelector(".stage").getBoundingClientRect();
    if (window.matchMedia("(max-width: 980px)").matches && (st.bottom < 60 || st.top > innerHeight)) {
      document.querySelector(".stage").scrollIntoView({ block: "start" });
    }
  }
}
function setupStory(d, live) {
  CH = chapters(d, live);
  $("ds-prev").addEventListener("click", () => renderChapter(chIdx - 1, true));
  $("ds-next").addEventListener("click", () => renderChapter(chIdx + 1, true));
  let onScreen = false;
  new IntersectionObserver((es) => { onScreen = es.some((x) => x.isIntersecting); }, { threshold: 0.35 }).observe($("player"));
  document.addEventListener("keydown", (e) => {
    if (!onScreen || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.target.closest?.("input, select, textarea, [contenteditable], .leaflet-container, [role=slider], summary")) return;
    if (e.key === "ArrowRight") { renderChapter(chIdx + 1, true); e.preventDefault(); }
    if (e.key === "ArrowLeft") { renderChapter(chIdx - 1, true); e.preventDefault(); }
  });
  $("player").addEventListener("click", (e) => { if (!e.target.closest("a, button, input")) $("player").focus({ preventScroll: true }); });
  for (const b of document.querySelectorAll(".arc-btn")) {
    b.addEventListener("click", () => renderChapter(CH.findIndex((x) => x.arc === b.dataset.arc), true));
  }
  let rs = 0;
  window.addEventListener("resize", () => { clearTimeout(rs); rs = setTimeout(() => drawSky(CH[chIdx].map), 150); });
  const q = Number.parseInt(new URLSearchParams(location.search).get("ch") || "", 10);
  renderChapter(Number.isFinite(q) ? q - 1 : 0, false);
}

// ================================================================ the failure moment, from the first screen
function goCheat() {
  if (!TAMPER_ACTIONS.A) return;
  TAMPER_ACTIONS.A();
  document.body.dataset.tamper = "A";
  if ($("deeplink-note").dataset.key === "A") $("deeplink-note").hidden = true;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("tamper").scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  $("rate-input").focus({ preventScroll: true });
}

// ================================================================ "Become a witness" (app/wallet.js; chain-locked to the Sepolia sandbox)
const WITNESS_FLOW = [
  ["connect", "Connect your wallet (Ethereum Sepolia)"],
  ["join", "Join the registry as witness OBS_JUDGE"],
  ["open", "Open your own sandbox clause (you post its small bond)"],
  ["sign", "Sign the TKM_S_276 sighting with your key"],
  ["cosign", "Add the second witness's signature (a published sandbox key)"],
  ["submit", "Send both signatures to the contract"],
  ["finalize", "Finalize after the judge sandbox's appeal window"],
];
function setupWitness(d) {
  const plan = $("witness-plan"), log = $("witness-log"), msg = $("witness-msg"), btn = $("witness-btn");
  const winMin = d.sepolia.sandboxWindows.challengeWindow / 60;
  WITNESS_FLOW[WITNESS_FLOW.length - 1][1] = `Finalize after the ${winMin}-minute appeal window`;
  plan.replaceChildren(...WITNESS_FLOW.map(([id, label]) => el("li", { "data-step": id }, label)));
  let w = null, at = 0;
  const mem = {};
  const mark = () => [...plan.children].forEach((li, i) => { li.className = i < at ? "done" : i === at ? "now" : ""; });
  const show = (r) => {
    for (const s of r.steps || []) {
      const li = el("li", { class: s.status === "failed" ? "fail" : "pass" });
      if (s.sim) li.append(el("span", { class: "simq", title: "simulated" }, "published sandbox key"), " ");
      li.append(s.label);
      if (s.tx?.link) li.append(" · ", link(s.tx.link, `tx, status ${s.tx.status}`));
      log.append(li);
    }
  };
  const ACT = {
    connect: () => w.connect(),
    join: () => w.joinAsObserver({ org: "OBS_JUDGE" }),
    open: () => w.openCovenant(),
    sign: async () => { const r = await w.signAttestation({ case: "TKM_S_276" }); if (r.ok) mem.a = r; return r; },
    cosign: async () => { const r = await w.cosignAttestation({ case: "TKM_S_276" }); if (r.ok) mem.b = r; return r; },
    submit: async () => { const r = await w.submitEpisode({ bundle: mem.a.bundle, signatures: [mem.a, mem.b] }); if (r.ok) mem.episodeId = r.episodeId; return r; },
    finalize: () => w.finalizeEpisode(mem.episodeId),
  };
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    msg.className = "verdict";
    try {
      if (!w) {
        msg.textContent = "Loading the wallet module…";
        const W = await import("./wallet.js");
        w = W.createWallet({ ethereum: window.ethereum || null, data: await W.loadWalletData("data/wallet.json") });
      }
      const [id, label] = WITNESS_FLOW[at];
      msg.textContent = `${label}…`;
      const r = await ACT[id]();
      if (!(r.error?.code === "NO_WALLET")) show(r);
      if (!r.ok) {
        msg.className = r.error.code === "NO_WALLET" ? "verdict note" : "verdict bad";
        msg.textContent = r.error.code === "NO_WALLET"
          ? "No browser wallet on this page. Install MetaMask (or any EIP-1193 wallet) with a little Sepolia test ETH, or run the same flow from a terminal: make judge-local."
          : r.error.code === "WINDOW_OPEN"
            ? `The appeal window is still open (${dur(r.secondsLeft || 0)} left). Press again when it has passed.`
            : `${r.error.code}: ${r.error.message}`;
        document.body.dataset.witness = r.error.code;
        return;
      }
      at++;
      mark();
      if (at >= WITNESS_FLOW.length) {
        msg.textContent = "Done: your signature was one of the two the clause needed, and the contract confirmed the leak.";
        btn.hidden = true;
        return;
      }
      msg.textContent = `Next: ${WITNESS_FLOW[at][1]}.`;
      btn.textContent = WITNESS_FLOW[at][1];
    } catch (e) {
      msg.className = "verdict bad";
      msg.textContent = `${e.code || "ERROR"}: ${e.message || e}`;
    } finally {
      btn.disabled = false;
    }
  });
  mark();
}

// ---------------------------------------------------------------- no wallet: a throwaway key made in this tab
// Signs the same EIP-712 Attestation the witnesses signed (the TKM_S_276 record of tamper A) with a key generated here,
// recovers the signer from the signature, and compares it with the KeyRegistry record. Nothing is sent anywhere.
function setupThrowaway(d) {
  const btn = $("witness-try"), out = $("witness-try-out");
  const H = d.hybrid, e = H.entries[0];
  const domain = { name: "MethaneClause", version: "1", chainId: H.chainId, verifyingContract: H.verifyingContract };
  const rej = d.sepolia.run.steps.find((x) => x.step === "reject_tamper" && x.status === 0);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    out.replaceChildren(el("li", { class: "info" }, "Making a key in this tab…"));
    try {
      const A = await import("https://cdn.jsdelivr.net/npm/viem@2.57.1/accounts/+esm");
      const acct = A.privateKeyToAccount(A.generatePrivateKey());
      const message = asMessage(e.attestation);
      const signature = await acct.signTypedData({ domain, types: TYPES, primaryType: "Attestation", message });
      const rec = await recoverTypedDataAddress({ domain, types: TYPES, primaryType: "Attestation", message, signature });
      const match = rec.toLowerCase() === acct.address.toLowerCase();
      const known = Object.entries(REG).find(([, r]) => r.address?.toLowerCase() === rec.toLowerCase());
      out.replaceChildren();
      check(out, match, `Signed by <code>${short(acct.address)}</code> · recovered <code>${short(rec)}</code> · ${match ? "match" : "no match"}`);
      check(out, null, `What you signed: the EIP-712 Attestation for the ${e.bundle.sourceName} sighting, evidenceHash <code>${short(e.attestation.evidenceHash)}</code> (the record of tamper A)`);
      const li = el("li", { class: known ? "fail" : "info" });
      li.append(known ? `This key recovers to ${wname(known[0])}'s registered key: unexpected.`
        : "This throwaway key is not in the KeyRegistry, so the contract would refuse this signature and the bond would not move. ");
      if (!known && rej) li.append("The same refusal (a signer that is not registered), recorded on Ethereum Sepolia: ", link(rej.tx, `status 0, ${rej.reason}`), ".");
      out.append(li);
      out.append(el("li", { class: "info" }, "No transaction, no test ETH: the key lives only in this tab and is gone when you reload. To make your signature count, join the registry with a wallet (the button above)."));
      document.body.dataset.throwaway = match && !known ? "ok" : "bad";
    } catch (err) {
      out.replaceChildren();
      check(out, false, `Could not sign in this browser: ${String(err.message || err).replace(/[<>&]/g, "")}`);
      document.body.dataset.throwaway = "error";
    } finally {
      btn.disabled = false;
    }
  });
}

// ================================================================ layer 2: the drawer
let sourcesLead = null;
function openTrust(open = true) {
  const body = $("trust-body"), t = $("trust-toggle");
  if (open === !body.hidden) return;
  body.hidden = !open;
  t.setAttribute("aria-expanded", String(open));
  if (open) {
    requestAnimationFrame(() => {
      for (const m of maps) m.invalidateSize();
      if (sourcesLead) drawMap(sourcesLead, sourcesLead.postMars.since);
    });
  }
}
function setupTrust() {
  $("trust-toggle").addEventListener("click", () => openTrust($("trust-body").hidden));
  // a link to anything inside the drawer opens it first
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    const t = document.getElementById(a.getAttribute("href").slice(1));
    if (t && $("trust-body").contains(t)) {
      openTrust(true);
      requestAnimationFrame(() => t.scrollIntoView({ block: "start" }));
      e.preventDefault();
    }
  });
  const h = location.hash.slice(1);
  if (h && document.getElementById(h) && $("trust-body").contains(document.getElementById(h))) openTrust(true);
}
function renderRules(d) {
  const r = d.cleanLook, inst = r.installments, last = inst[inst.length - 1];
  const fl = Object.entries(r.instrumentFloorKgH).map(([i, v]) => `${SAT(i)} ${fmt.format(v)} kg/h`).join(", ");
  $("rules-lede").textContent = `A clean pass counts only on a clear-sky scene, from a satellite that could see a leak this size, with no UNEP MARS plume nearby — and the last step needs ${last.afterPasses} of them, two satellites and ${r.cureTermMonths} months. Read from ${r.source}.`;
  $("rules-list").replaceChildren(
    el("li", {}, el("strong", {}, "Clear sky. "), `Carbon Mapper's ${r.cloudRule} flag is set for the scene, consistent with a cloud cover of at most ${r.maxCloudPct} %; the witnesses sign the cloud fields, so editing them breaks their signatures (tamper C).`),
    el("li", {}, el("strong", {}, "Could have seen it. "), `The scene's footprint covers the source's plume origins, and the satellite's detection floor is at most the confirmed leak's rate (${fl}).`),
    el("li", {}, el("strong", {}, "Nothing seen. "), "No plume on the scene at the source; a plume on it refuses the pass (PLUME_ON_SCENE)."),
    el("li", {}, el("strong", {}, "No UNEP MARS plume nearby. "), `No UNEP MARS plume within ±${r.marsWindowDays} days (MARS_PLUME_NEARBY).`),
    el("li", {}, el("strong", {}, "Signed and open to appeal. "), `k witness organisations sign each pass, no earlier than ${r.publicationLagDays} days after the scene was taken; it counts after its own ${r.cleanWindowSeconds / 3600}-hour appeal window.`),
    el("li", {}, el("strong", {}, "Earn-back steps. "), inst.map((i) => `${i.afterPasses} passes → ${i.shareBp / 100} %${i.twoInstruments ? ", two satellites" : ""}${i.isFinal ? `, a MARS corroboration and a ${r.cureTermDays}-day cure term` : ""}`).join(" · "),
      ". Never more than the rebate the leak cost; a new confirmed leak cancels what is pending."),
    el("li", {}, el("strong", {}, "Appeal reasons. "), "Anyone may challenge a pass with a deposit and an objective reason code (CLOUD_FLAG, NO_ORIGIN_INTERSECT, PLUME_ON_SCENE, MARS_PLUME_NEARBY, FLOOR_MISMATCH and others); each scene's result is in the full record below."));
}

function selfCheckPill() {
  const pass = document.querySelectorAll("#self-checks li.pass").length, fail = document.querySelectorAll("#self-checks li.fail").length;
  const p = $("check-pill");
  if (document.querySelector("#self-checks li[data-pending]")) { p.textContent = "Checks running in your browser…"; p.className = "pill"; return; }
  p.textContent = fail ? `${fail} check${fail === 1 ? "" : "s"} failed in your browser` : `✓ ${pass} checks passed in your browser`;
  p.className = `pill ${fail ? "bad" : "ok"}`;
}

function applyPersonaLink() {
  const q = new URLSearchParams(location.search), want = (q.get("persona") || q.get("view") || "").toLowerCase();
  const det = document.querySelector(`.actor-more[data-persona="${want}"]`);
  if (!det) return;
  det.open = true;
  document.body.dataset.persona = want;
  requestAnimationFrame(() => det.closest(".actor").scrollIntoView({ block: "start" }));
}

// ---------------------------------------------------------------- boot
(async () => {
  const d = await fetch("data/app.json").then((r) => r.json());
  REG = d.hybrid.registeredKeys || {};
  // the cheat button works as soon as tamper A is set up (committed data only); the live reads and the self-checks
  // below run on, and a failure there cannot keep it disabled
  const cheat = $("cheat-btn");
  cheat.addEventListener("click", goCheat);
  const enableCheat = () => {
    if (!TAMPER_ACTIONS.A || !cheat.disabled) return;
    cheat.textContent = CHEAT_LABEL;
    cheat.disabled = false;
  };
  const tamperP = setupTamper(d).finally(enableCheat);
  try {
  setupTrust();
  sourcesLead = d.cases.TKM_S_276;
  renderSources(d);
  let live = null, liveErr = "";
  const receipts = liveReceipts(d).catch(() => ({ ok: false, read: 0 }));
  try { live = await liveRead(d); } catch (e) { liveErr = `no public endpoint answered from this network: ${rpcList(d.sepolia).map(hostOf).join(", ")}`; }
  d.receipts = await receipts;
  const pill = $("net-pill");
  pill.textContent = live ? `Ethereum Sepolia · live, block ${fmt.format(live.block)}` : "Ethereum Sepolia · committed snapshot";
  if (live) pill.classList.add("live");
  renderActors(d, live);
  setupStory(d, live);
  setupWitness(d);
  setupThrowaway(d);
  renderRules(d);
  renderStrip(d, live);
  renderJudgePath(d, live);
  renderExplorer(d);
  buildLine();
  renderLedger(d, live);
  renderCovenant(d, live);
  renderEpisodes(d, live);
  setupClock(d, live);
  setupVerificationTime(d);
  const tamperOk = await tamperP;
  const results = { observer: await setupObserver(d), missing: await setupMissing(d), cloud: await setupCloud(d) };
  await renderExport(d);
  await selfChecks(d, live, liveErr, tamperOk);
  await selfChecksV2(d, results);
  selfCheckPill();
  await applyDeepLink();
  applyPersonaLink();
  document.body.dataset.ready = "1";
  } finally {
    enableCheat();
  }
})();
