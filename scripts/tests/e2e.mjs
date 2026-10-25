// One-file end-to-end check of the page (make e2e). The page is told in three layers: the five-second view (who pays
// whom, two money arrows, one button into the failure moment), the story on real data (TKM_S_276 penalty
// arc, DZA_S_012 earn-back arc) and the "Why you can trust it" drawer that keeps every proof of the former screen.
//
// Serves app/ from this checkout on an ephemeral port, opens it in a real browser (Playwright; the installed Google
// Chrome by default, E2E_CHANNEL= for Playwright's own Chromium) and checks what a judge clicks:
//   page ready with 0 failed self-checks and no "[SIM" text (simulated = purple, dashed, one legend); the first view;
//   the cheat button turns tamper A red; each tamper button turns its checks red and Reset restores them; every story
//   chapter is reachable and links the transactions app.json records; the four actors and the sponsor's pool; "Become
//   a witness" present and, without a wallet, explains itself (NO_WALLET); the drawer opens and holds the clean-look
//   rule, the DZA replay facts, the portfolio line, "Skip this window", the Annex D/C and credit-note round trips, the
//   judge's path and the explorer table against app.json; no horizontal scroll at 375 px (drawer closed and open); the
//   deep links ?tamper=A|B|C, ?persona=, ?ch= and #explorer; the story page (story.html): 9 chapters in four views, no
//   "[SIM" text, no page errors, the header fits at 1280 px, no horizontal scroll at 375 px. E2E_ROOT=out/pages runs the same checks on the static
//   export (make pages). Needs the network for the pinned CDN modules (viem, noble, Leaflet), so it is not part of
//   make check. The live Sepolia receipts are read through the public endpoints in app.json (primary, then keyless
//   fallbacks); the run logs which path served them, and E2E_LIVE=1 requires every receipt read live.
//   Pinned code: the import map's sha384 list equals the ML-DSA-44 module graph the CDN serves, an edited module is
//   refused, and a data file that differs from the pinned ML-DSA context or registry anchor turns its self-check red.
//   E2E_HEADED=1 runs the same checks in a visible browser window.
//   Exit status 1 on the first failure.
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(import.meta.url), "../../..");
const APP = resolve(ROOT, process.env.E2E_ROOT || "app");
const TOOLS = process.env.PLAYWRIGHT_DIR || join(ROOT, "out", "e2e-tools");
const { chromium } = createRequire(join(TOOLS, "package.json"))("playwright");

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".csv": "text/csv", ".png": "image/png", ".svg": "image/svg+xml" };
const server = createServer(async (req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
  const file = join(APP, path.endsWith("/") ? path + "index.html" : path);
  if (!file.startsWith(APP)) { res.writeHead(403).end(); return; }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" }).end(body);
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const BASE = `http://127.0.0.1:${server.address().port}/`;
const FULL = BASE + "index.html";  // the bare address sends a desktop screen to story.html

// Subresource integrity of the ML-DSA-44 verifier: app.js imports @noble/post-quantum as published package files (not a
// CDN-generated +esm bundle, which jsDelivr does not keep byte-stable), the import map in index.html maps its bare
// @noble/* imports to exact versions and lists the sha384 of every module in that graph. sriGraph() walks the graph from
// the CDN and hashes it; `E2E_SRI=print node scripts/tests/e2e.mjs` prints the import map to paste into index.html, and
// the run below requires the committed map to equal it.
const PQ_ENTRY = /from "(https:\/\/cdn\.jsdelivr\.net\/npm\/@noble\/post-quantum@[^"]+)"/;
async function sriGraph(entry, prefixes) {
  const { createHash } = await import("node:crypto");
  const seen = new Map(), todo = [entry];
  const resolveSpec = (spec, base) => {
    if (spec.startsWith("./") || spec.startsWith("../")) return new URL(spec, base).href;
    const p = Object.keys(prefixes).find((k) => spec.startsWith(k));
    if (!p) throw new Error(`sriGraph: bare import ${spec} in ${base} has no import-map prefix`);
    return prefixes[p] + spec.slice(p.length);
  };
  while (todo.length) {
    const url = todo.pop();
    if (seen.has(url)) continue;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`sriGraph: ${url} ${r.status}`);
    const body = Buffer.from(await r.arrayBuffer());
    seen.set(url, "sha384-" + createHash("sha384").update(body).digest("base64"));
    const code = body.toString("utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    for (const m of code.matchAll(/(?:^|[;\s])(?:import|export)\s*(?:[\w*{}\s,$]+\sfrom\s*)?["']([^"']+)["']/g)) todo.push(resolveSpec(m[1], url));
    if (/\bimport\s*\(/.test(code)) throw new Error(`sriGraph: dynamic import in ${url}`);
  }
  return Object.fromEntries([...seen].sort(([a], [b]) => a.localeCompare(b)));
}
const IMPORT_MAP = (html) => JSON.parse(html.match(/<script type="importmap">([\s\S]*?)<\/script>/)?.[1] || "{}");
if (process.env.E2E_SRI === "print") {
  const map = IMPORT_MAP(await readFile(join(APP, "index.html"), "utf8"));
  const entry = (await readFile(join(APP, "app.js"), "utf8")).match(PQ_ENTRY)[1];
  console.log(JSON.stringify({ imports: map.imports, integrity: await sriGraph(entry, map.imports || {}) }, null, 2));
  server.close();
  process.exit(0);
}

let n = 0;
const ok = (cond, msg) => {
  if (!cond) { console.log(`   FAIL  ${msg}`); throw new Error(msg); }
  n++; console.log(`   ok    ${msg}`);
};
const channel = process.env.E2E_CHANNEL === undefined ? "chrome" : process.env.E2E_CHANNEL || undefined;
const browser = await chromium.launch({ channel, headless: process.env.E2E_HEADED !== "1" });  // E2E_HEADED=1: a visible run
const t0 = Date.now();
const D = JSON.parse(await readFile(join(APP, "data", "app.json"), "utf8")), S = D.sepolia;
const eth = (wei) => { const w = BigInt(wei), f = (w % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return `${w / 10n ** 18n}${f ? "." + f : ""} ETH`; };
const fmt = new Intl.NumberFormat("en-US");

async function ready(page, url = FULL) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => { if (r.url().startsWith(BASE) && r.status() >= 400) errors.push(`HTTP ${r.status()} ${r.url()}`); });
  await page.goto(url);
  await page.waitForSelector("body[data-ready='1']", { timeout: 90_000 });
  return errors;
}
const count = (page, sel) => page.locator(sel).count();
const hasClass = async (page, sel, cls) => (await page.getAttribute(sel, "class") || "").split(/\s+/).includes(cls);
const openDrawer = async (page) => {
  if ((await page.getAttribute("#trust-toggle", "aria-expanded")) !== "true") await page.click("#trust-toggle");
  await page.waitForSelector("#trust-body:not([hidden])");
};

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const cdnReqs = [];
  page.on("request", (r) => { if (r.url().startsWith("https://cdn.jsdelivr.net/")) cdnReqs.push(r.url()); });
  const errors = await ready(page);
  console.log(`== desktop 1280 px (${FULL})`);
  ok(errors.length === 0, `no uncaught page errors and no same-origin 4xx (BUILD.json included)${errors.length ? ": " + errors[0] : ""}`);
  ok((await count(page, "#self-checks li.fail")) === 0, "self-checks: 0 failed");
  ok((await count(page, "#self-checks li.pass")) >= 6, `self-checks: ${await count(page, "#self-checks li.pass")} passed`);
  const pinLine = (await page.locator("#self-checks li.pass").allInnerTexts()).find((t) => t.includes("KeyRegistry anchor")) || "";
  ok(D.hybrid.chainId === 31337 && pinLine.includes("KeyRegistry anchor of the local demo chain (Anvil, chainId 31337; not the Sepolia registry)"),
    "the drawer's pin check names its registry as the local demo chain's (Anvil), not Sepolia's");
  const pill0 = await page.locator("#check-pill").innerText();
  ok(/^(✓ \d+ checks passed in your browser|Checks running in your browser…)$/.test(pill0), `header pill: a final count or "running", never a partial count (${pill0})`);

  // simulated = purple, dashed, one legend: no "[SIM" text anywhere on the page (drawer included), the mark is drawn
  const sim = await page.evaluate(() => ({ text: document.body.textContent.includes("[SIM"),
    attrs: [...document.querySelectorAll("[title],[aria-label]")].some((e) => /\[SIM/.test(e.title + (e.getAttribute("aria-label") || ""))),
    marks: document.querySelectorAll(".simk, .simq, .simw").length, legend: document.querySelectorAll(".keyl .lchip").length }));
  ok(!sim.text && !sim.attrs && sim.marks > 10 && sim.legend === 2,
    `no "[SIM" text or attribute on the page; ${sim.marks} purple dashed marks; one legend (real data / simulated)`);

  console.log("== layer 0: the first view");
  const HEADLINE_V4 = "No one runs the payout: once enough independent witnesses sign the same sighting and the appeal window passes, the contract pays the buyer from the bond. The seller can appeal — then the witnesses must confirm it again — but cannot delete the sighting or hold the money.";
  const line = (k) => page.locator(`#hero-h .l:nth-child(${k})`).innerText();
  ok((await page.locator("#hero-lead").innerText()) === "A speed camera for methane, written into the gas contract between an EU gas importer and its supplier."
    && (await line(1)) === "Satellites confirm a leak → the seller's bond pays the buyer."
    && (await line(2)) === "No new leak on later passes → the seller earns money back in steps, up to the rebate, from a sponsor's pool (for example its lender's)."
    && (await line(3)) === HEADLINE_V4, "the lead line and the three-line headline, verbatim");
  ok((await page.getAttribute('meta[name="description"]', "content")).endsWith(` ${HEADLINE_V4}`),
    "the meta description ends on the same headline sentence");
  const weights = await page.evaluate(() => [1, 2, 3].map((k) => { const s = getComputedStyle(document.querySelector(`#hero-h .l:nth-child(${k})`)); return [Number(s.fontWeight), parseFloat(s.fontSize)]; }));
  ok(weights[0][0] >= 600 && weights.slice(1).every(([w, f]) => w < 600 && f < weights[0][1]),
    `line 1 is bold; lines 2 and 3 are lighter and smaller (${JSON.stringify(weights)})`);
  ok((await count(page, ".analogy")) === 0, "no analogy paragraph repeating the headline");
  ok(/judge's 10-minute path/.test(await page.locator("#judge-teaser").innerText()) && (await page.locator("#judge-teaser a").getAttribute("href")) === "#judge-path",
    "the judge's path teaser sits in the first view and links the full path in the drawer");
  ok((await page.locator("#money-fig figcaption").innerText()).includes("a sponsor (the seller's lender, as a sustainability-linked margin step, or the buyer)"),
    "the sponsor is glossed on its first use after the headline");
  const fig = await page.locator("#money-fig").textContent();
  ok(!/agency [AB]|own satellite|satellites run by/i.test(fig) && /gets the rebate/.test(fig) && /funds the earn-back pool/.test(fig),
    "the picture: public satellites; buyer gets the rebate; sponsor funds the earn-back pool");
  const front = await page.evaluate(() => ["#hero", "#story", "#tamper", "#witness"].map((s) => document.querySelector(s).textContent).join(" "));
  ok(!/clean look/i.test(await page.evaluate(() => document.body.textContent)) && !/observer/i.test(front.replace(/data-persona/g, "")),
    "front words: 'clean pass' everywhere (no 'clean look'), 'witness' on the first view, story, tampers and witness panel");
  ok((await count(page, "#money-fig .arrow-red")) === 1 && (await count(page, "#money-fig .arrow-green")) === 1
    && (await count(page, "#money-fig .party")) === 3, "the picture: red arrow (bond → buyer), green arrow (pool → seller), three simulated parties");
  ok((await page.locator("#fine-amounts").innerText()) === "Amounts are representative test values; a production clause sets them in euros in the parties' policy."
    && (await page.locator("#fine-oneperson").innerText()) === "In this demo one person runs every role; in production each witness is a separate organisation with its own keys.",
    "the two small lines");
  const storyPill = page.locator(".top-right a.pill", { hasText: "Story map" });
  ok((await storyPill.count()) === 1 && (await storyPill.getAttribute("href")) === "story.html" && (await storyPill.isVisible()),
    "the top bar links back to the story map (Story map → story.html)");
  const above = await page.evaluate(() => ["#hero-h", "#money-fig", "#cheat-btn"].every((s) => document.querySelector(s).getBoundingClientRect().top < innerHeight));
  ok(above, "sentence, picture and the cheat button are in the first screen");
  const lead = D.hybrid.entries[0].bundle, tam = S.run.steps.find((x) => x.step === "reject_tamper");
  ok(!(await page.locator("#cheat-btn").isDisabled()), "the cheat button is enabled once the page is ready");
  ok((await page.locator("#cheat-btn").innerText()).includes(fmt.format(lead.emissionKgH)), "the cheat button names the signed rate from app.json");
  await page.click("#cheat-btn");
  await page.waitForFunction(() => document.querySelector("#tamper-verdict").classList.contains("bad"));
  ok((await hasClass(page, "#moment-3", "bad")) && (await page.locator("#moment-3 a").getAttribute("href")) === tam.tx
    && (await page.getAttribute("body", "data-tamper")) === "A",
    `the cheat button opens the failure moment: signatures fail, the bond does not move, the Sepolia revert (${tam.reason}) linked`);
  await page.click("#reset-btn");
  await page.waitForFunction(() => !document.querySelector("#tamper-verdict").classList.contains("bad"));

  // the browser-wallet back end (app/wallet.js): imported here from the page's origin, it loads from the pinned CDN in a
  // real browser and reproduces the Python signer's digests there; its on-chain flow is make wallet-local
  const wp = await page.evaluate(async () => {
    const W = await import("./wallet.js"), d = await W.loadWalletData("data/wallet.json"), P = d.parity, I = P.inputs;
    const { hashTypedData } = await import("https://cdn.jsdelivr.net/npm/viem@2.57.1/+esm");
    const b = { ...d.detections.TKM_S_276.bundleTemplate, covenantId: I.covenantId }, ev = await W.canonicalHash(b);
    const att = W.attestationFor(b, ev, { nonce: I.nonce, expiry: I.expiry });
    const cb = { ...d.cleanPasses.DZA_S_012.bundleTemplate, covenantId: I.covenantId, claimId: I.claimId }, sh = await W.canonicalHash(cb);
    const msg = W.cleanPassFor(cb, sh, { nonce: I.nonce, expiry: I.expiry });
    const none = await W.createWallet({ ethereum: null, data: d }).connect();
    return { ev: ev === P.evidenceHash, att: hashTypedData(W.attestationTypedData(att, I.chainId, I.covenant)) === P.attestationDigest,
      cp: hashTypedData(W.cleanPassTypedData(msg, I.chainId, I.pool)) === P.cleanPassDigest,
      key: W.publishedKey(d.sandboxKeys.prefix, "OBS_1").length === 66, noWallet: !none.ok && none.error.code === "NO_WALLET" };
  });
  ok(Object.values(wp).every(Boolean), `wallet module in the browser: evidenceHash, Attestation and CleanPass digests equal Python's; no wallet -> NO_WALLET (${JSON.stringify(wp)})`);

  console.log("== the failure moment: three tampers");
  const hashes = (pg, now, committed) => pg.evaluate(([a, b]) => [document.querySelector(a).textContent, document.querySelector(b).textContent], [now, committed]);
  for (const [name, btn, reset, verdict, checks, expect, now, committed] of [
    ["A · rate edited", "#tamper-btn", "#reset-btn", "#tamper-verdict", "#sig-checks", [/Hash changed/, /InvalidSignature/, /ML-DSA-44 signature does not verify/], "#h-now", "#h-committed"],
    ["B · sighting deleted", "#miss-btn", "#miss-reset", "#miss-verdict", "#miss-checks", [/re-fetch hash differs/], "#miss-now", "#miss-committed"],
    ["C · cloud fields edited", "#cloud-btn", "#cloud-reset", "#cloud-verdict", "#cloud-checks", [/sceneHash changed/, /InvalidSignature/, /ML-DSA-44 does not verify/], "#cloud-now", "#cloud-committed"],
  ]) {
    await page.evaluate((b) => { const box = document.querySelector(b).closest("details"); if (box) box.open = true; }, btn);
    const [h0, c0] = await hashes(page, now, committed);
    ok(/^0x[0-9a-f]{64}$/.test(h0) && h0 === c0, `tamper ${name}: the recomputed hash equals the committed one before`);
    ok(!(await hasClass(page, verdict, "bad")) && (await count(page, `${checks} li.fail`)) === 0, `tamper ${name}: green before`);
    await page.click(btn);
    await page.waitForFunction((s) => document.querySelector(s).classList.contains("bad"), verdict);
    const fails = await page.locator(`${checks} li.fail`).allInnerTexts();
    ok(fails.length > 0, `tamper ${name}: red after the button (${fails.length} checks fail)`);
    for (const re of expect) ok(fails.some((t) => re.test(t)), `tamper ${name}: the failing check says ${re.source}`);
    const [h1, c1] = await hashes(page, now, committed);
    ok(/^0x[0-9a-f]{64}$/.test(h1) && h1 !== c1, `tamper ${name}: the recomputed hash differs from the committed one`);
    await page.click(reset);
    await page.waitForFunction((s) => !document.querySelector(s).classList.contains("bad"), verdict);
    const [h2, c2] = await hashes(page, now, committed);
    ok((await count(page, `${checks} li.fail`)) === 0 && h2 === c2, `tamper ${name}: Reset restores green and the committed hash`);
  }

  console.log("== pinned code: subresource integrity and the ML-DSA / registry pins");
  const html = await readFile(join(APP, "index.html"), "utf8"), imap = IMPORT_MAP(html);
  const pqEntry = (await readFile(join(APP, "app.js"), "utf8")).match(PQ_ENTRY)?.[1];
  const graph = await sriGraph(pqEntry, imap.imports || {});
  ok(pqEntry && !pqEntry.includes("+esm") && JSON.stringify(imap.integrity) === JSON.stringify(graph),
    `import map: the sha384 of all ${Object.keys(graph).length} modules of the ML-DSA-44 verifier, as the CDN serves them now, equal index.html's integrity list`);
  // viem's own +esm bundle pulls @noble 1.x as +esm too (generated by the CDN, so no SRI there); the published files are ours
  const noble = [...new Set(cdnReqs.filter((u) => u.includes("/npm/@noble/") && !u.endsWith("/+esm")))];
  ok(noble.length === Object.keys(graph).length && noble.every((u) => u in imap.integrity),
    `every published @noble file the page loaded (${noble.length}) has an integrity entry`);
  const tags = [...html.matchAll(/<(?:script|link)\b[^>]*\b(?:src|href)="https:\/\/cdn\.jsdelivr\.net[^>]*>/g)].map((m) => m[0]);
  ok(tags.length >= 2 && tags.every((x) => /integrity="sha384-[A-Za-z0-9+/=]+"/.test(x) && /crossorigin="anonymous"/.test(x)) && (await page.evaluate(() => !!window.L)),
    `every CDN <script>/<link> in index.html carries integrity + crossorigin (${tags.length}) and Leaflet loaded under it`);
  {
    const sri = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const msgs = [];
    sri.on("console", (m) => msgs.push(m.text()));
    sri.on("pageerror", (e) => msgs.push(String(e)));
    await sri.route(pqEntry, async (route) => {
      const r = await route.fetch();
      await route.fulfill({ response: r, body: (await r.text()) + "\n// one line more than the published file\n" });
    });
    await sri.goto(FULL);
    const readyAnyway = await sri.waitForSelector("body[data-ready='1']", { timeout: 8_000 }).then(() => true, () => false);
    ok(!readyAnyway && msgs.some((x) => /integrity/i.test(x)),
      `an ML-DSA module edited in transit is refused by the browser (integrity), so the page claims no check: ${(msgs.find((x) => /integrity/i.test(x)) || "").slice(0, 90)}…`);
    await sri.close();
  }
  const pinCheck = (pg) => pg.locator("#self-checks li").filter({ hasText: /^Pinned/ });
  ok((await pinCheck(page).count()) === 1 && (await pinCheck(page).evaluate((x) => x.classList.contains("pass"))),
    "self-check: the pinned ML-DSA context and registry anchor equal app.json");
  for (const [what, edit, alsoRed] of [
    ["ML-DSA context", (j) => { j.hybrid.entries[0].mldsa44.context = "MethaneClause/v2"; }, null],
    ["registry anchor", (j) => { j.hybrid.registeredKeys.OBS_A.pqPubKeyHash = "0x" + "11".repeat(32); }, "#sig-checks"],
  ]) {
    const pg = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await pg.route(/\/data\/app\.json$/, async (route) => {
      const r = await route.fetch(), j = await r.json();
      edit(j);
      await route.fulfill({ response: r, body: JSON.stringify(j) });
    });
    await ready(pg);
    const li = pinCheck(pg);
    ok((await li.count()) === 1 && /^Pinned constants differ from data\/app\.json/.test(await li.innerText())
      && (await li.evaluate((x) => x.classList.contains("fail"))) && (!alsoRed || (await count(pg, `${alsoRed} li.fail`)) > 0),
      `a data file whose ${what} differs from the page's pin turns the self-check red${alsoRed ? " and the signature checks fail" : ""}: ${(await li.innerText()).slice(0, 100)}`);
    await pg.close();
  }

  console.log("== layer 1: actors and the two stories");
  ok((await count(page, "#actors > li.actor")) === 4, "four actors: seller, buyer, witnesses, sponsor");
  ok((await page.locator(".paths li").allInnerTexts()).length === 3, "three paths");
  // the sponsor's pool the DZA replay read back (app.json <- out/demo/replay.json), not a placeholder
  await page.locator("#actor-sponsor summary").click();
  const sp = D.replay.variants;
  const sv = await page.locator("#view-sponsor").innerText();
  const want = [sp.A.sponsor.openedByBuyerWei, sp.A.sponsor.sponsorTopUpWei, sp.A.sponsor.balanceWei, sp.A.sponsor.releasedWei,
    sp.A.sponsor.forfeitedWei, sp.A.sponsor.observerFeesWei, sp.A.sponsor.unreservedWei, sp.A.sponsor.sponsorShareWei,
    sp.B.sponsor.sponsorTopUpWei, sp.B.sponsor.sponsorShareWei].map(eth);
  ok(want.every((x) => sv.includes(x)) && /sponsorWithdraw/.test(sv) && /look-ahead/.test(sv) && !/pending —/.test(sv),
    `the sponsor's view shows the replay's pool: top-up, balance, released, forfeited, witness fees, remainder (${want.length} amounts)`);
  ok(/Fetch → Validate → Seal → Sign → Re-check/.test(await page.locator("#view-observer").textContent()), "the witnesses' view names the five steps");
  ok((await page.locator("#view-seller").textContent()).includes("it can only appeal it, and then the independent witnesses must confirm it again."),
    "the seller's view repeats the headline's appeal clause (canonical v3)");
  const lowKt = D.stake.kt_ch4_per_yr.low;
  ok((await page.locator("#covenant-body").textContent()).includes(`Lead-case stake, not abatement: ${lowKt} kt CH₄/yr, the low bound of the band`)
    && !/at least [\d.,]+ kt/.test(await page.evaluate(() => document.body.textContent)),
    `the stake chip names the band's low bound (${lowKt} kt CH₄/yr from app.json), never "at least"`);

  const heads = [], links = new Set(), codes = new Set(), factTexts = [];
  await page.locator(".arc-btn[data-arc='tkm']").click();
  let total = 0;
  for (let i = 0; i < 20; i++) {
    heads.push(await page.locator("#ds-head").innerText());
    factTexts.push(await page.locator("#ds-facts").innerText());
    for (const h of await page.locator("#ds-facts a[href^='https://']").evaluateAll((as) => as.map((a) => a.href))) links.add(h);
    for (const c of await page.locator("#ds-facts code[title]").evaluateAll((cs) => cs.map((c) => c.title))) codes.add(c);
    total++;
    if (await page.locator("#ds-next").isDisabled()) break;
    await page.click("#ds-next");
  }
  ok(total >= 10 && new Set(heads).size === total && heads.every((h) => h.length > 8),
    `every story chapter reachable with Next: ${total} chapters, each with its own headline`);
  ok(factTexts.some((t) => t.includes(`${D.stake.kt_ch4_per_yr.low} kt CH₄ a year at this source, the low bound of the band — a stake, never a measured cut`))
    && !factTexts.some((t) => /at least [\d.,]+ kt/.test(t)), "the \"Still seen\" chapter's stake is the band's low bound from app.json, never \"at least\"");
  const st = Object.fromEntries(S.run.steps.map((x) => [x.step, x]));
  const sepWant = ["createCovenant", "proposeEpisode", "finalize", "withdraw_buyer"].map((k) => st[k].tx)
    .concat(S.run.steps.filter((x) => x.status === 0).map((x) => x.tx));
  ok(sepWant.every((t) => links.has(t)), `the penalty arc links its ${sepWant.length} Sepolia transactions from app.json (create, propose, finalize, withdraw, every refusal)`);
  ok([...links].filter((h) => h.includes("/tx/")).every((h) => S.run.steps.some((x) => x.tx === h)), "every story transaction link is one app.json records");
  const A = D.replay.variants.A.txs;
  const repWant = [A["finalize (episode)"][0], A.openClaim[0], A["challengePass CLOUD_FLAG"][0], ...A.release,
    A["relapse while the episode is PROPOSED"][0], A["relapse (cancel)"][0]].map((t) => t.tx);
  ok(repWant.every((t) => codes.has(t)), `the earn-back arc shows its ${repWant.length} recorded local-run transactions (confirm, claim, appealed look, releases, frozen cancel, cancel)`);
  const inst = D.cleanLook.installments, out = D.replay.variants.A.outcome;
  ok(heads.some((h) => h.includes(`${inst[0].shareBp / 100} % at ${inst[0].afterPasses} passes`) && h.includes(`${(inst[0].shareBp + inst[1].shareBp) / 100} % at ${inst[1].afterPasses}`)),
    "the earn-back steps read from the policy (cleanLook)");
  ok((await page.locator("#ds-body").innerText()).includes(`paid ${out.paidBp / 100} %, forfeited ${out.forfeitedBp / 100} %`),
    "the new-leak chapter: paid and forfeited from the replay's outcome");
  const story = await page.evaluate(() => ["#hero", "#story", "#tamper", "#witness"].map((s) => document.querySelector(s).innerText).join("\n"));
  ok(!/\b[A-Z]+(?:_[A-Z]+)+\b/.test(story.replace(/\b(?:TKM_S_276|DZA_S_012|USA_S_077|OBS_[A-Z]+|JUDGE_PK)\b/g, "")),
    "no refusal or revert code on the first view, the story, the tampers or the witness panel (codes live in tooltips and the drawer)");
  ok(!/\S {2,}\S/.test(story) && !/\b(\w+ \w+) \1\b/.test(story), "no double spaces or doubled words in that text");
  await page.locator(".arc-btn[data-arc='dza']").click();
  ok((await page.locator("#case-title").textContent()) === "Earn-back case · DZA_S_012" && (await page.locator(".arc-btn[data-arc='dza']").textContent()) === "Earn-back · DZA_S_012",
    "the header's case name and the story tab agree on the earn-back case");
  const chBefore = Number(await page.getAttribute("body", "data-chapter"));
  await page.evaluate(() => { document.activeElement?.blur(); document.querySelector("#player").scrollIntoView({ block: "center" }); });
  await page.waitForTimeout(300);
  await page.keyboard.press("ArrowRight");
  ok(Number(await page.getAttribute("body", "data-chapter")) === chBefore + 1, "→ on the keyboard changes the chapter while the story is on screen, without focusing it first");
  await page.keyboard.press("ArrowLeft");
  ok((await page.locator("#ds-arc").textContent()).startsWith("Earn-back") && /^Earn-back case · step 1 of \d+$/.test(await page.locator("#ds-count").textContent()),
    "the arc buttons switch to the earn-back story");

  console.log("== become a witness");
  await page.waitForFunction(() => !document.querySelector("#self-checks li[data-pending]"), null, { timeout: 15_000 });
  const pillN = Number((await page.locator("#check-pill").innerText()).match(/(\d+) checks passed/)?.[1]);
  ok(pillN === (await count(page, "#self-checks li.pass")), `the header pill counts every passed check, the late tile check included (${pillN})`);
  ok((await count(page, "#witness-btn")) === 1 && (await count(page, "#witness-plan li")) === 7, "the witness button and its seven steps");
  await page.click("#witness-btn");
  await page.waitForFunction(() => document.body.dataset.witness, null, { timeout: 30_000 });
  ok((await page.getAttribute("body", "data-witness")) === "NO_WALLET" && /make judge-local/.test(await page.locator("#witness-msg").innerText())
    && (await hasClass(page, "#witness-msg", "note")) && (await count(page, "#witness-log li.fail")) === 0,
    "without a browser wallet the button explains itself once, as a neutral note naming make judge-local");
  await page.click("#witness-try");
  await page.waitForFunction(() => document.body.dataset.throwaway, null, { timeout: 30_000 });
  {
    const out = await page.locator("#witness-try-out").innerText();
    const m = out.match(/Signed by (0x[0-9a-fA-F]{6,}…[0-9a-fA-F]{4}) · recovered (0x[0-9a-fA-F]{6,}…[0-9a-fA-F]{4}) · match/);
    const rejLink = await page.locator("#witness-try-out a").getAttribute("href");
    ok((await page.getAttribute("body", "data-throwaway")) === "ok" && m && m[1] === m[2] && /not in the KeyRegistry/.test(out)
      && rejLink === tam.tx && (await count(page, "#witness-try-out li.fail")) === 0,
      `no wallet: a throwaway key signs the record in the tab, the signer is recovered (${m?.[1]}), and the page links the recorded refusal (${tam.reason})`);
  }

  console.log("== layer 2: the drawer");
  {
    const ep = await page.locator("#episode-body table.ep").innerText();
    const prop = S.run.steps.find((x) => x.step === "proposeEpisode");
    const m = ep.match(/plume (\d+) days later \((\d{4}-\d\d-\d\d)\)/), lead0 = Date.parse(D.hybrid.entries[0].bundle.sceneTimestamp);
    const days = m ? D.cases.TKM_S_276.cmPlumes.filter((p) => p.scene_timestamp.startsWith(m[2])).map((p) => Math.round((Date.parse(p.scene_timestamp) - lead0) / 864e5)) : [];
    ok(ep.includes(`tier ${prop.tier} · ${D.demo.rebate_eth} ETH withdrawn`) && days.length > 0 && days.every((x) => x === Number(m[1])),
      `episodes: the local demo's tier equals the tier Sepolia computed for the same rate (${prop.tier}); its linked plume is a TKM_S_276 scene in app.json (${m?.[2]}, ${m?.[1]} days later)`);
  }
  ok(await page.locator("#trust-body").isHidden(), "the drawer starts closed");
  await openDrawer(page);
  ok((await page.getAttribute("#trust-toggle", "aria-expanded")) === "true", "Why you can trust it opens (aria-expanded)");
  const last = inst[inst.length - 1];
  const rl = await page.locator("#rules-lede").innerText();
  ok(rl.includes(`the last step needs ${last.afterPasses} of them, two satellites and ${D.cleanLook.cureTermMonths} months`)
    && (await count(page, "#rules-list li")) >= 6, "the clean-look rule from the committed policy");
  ok(/confirmed leak/.test(await page.locator("#words").innerText()), "the word mapping is printed once");
  ok((await count(page, "#export-checks li.fail")) === 0 && (await count(page, "#export-checks li.pass")) >= 2,
    "Annex D: CSV round trip and embedded bundle hash pass");
  ok((await count(page, "#annexc-checks li.fail")) === 0 && (await count(page, "#annexc-checks li.pass")) >= 2,
    "Annex C: claimHash recomputed and matches the hash recorded on chain");
  ok((await count(page, "#annexc-table .simk")) > 0 && /example form/.test(await page.locator("#annexc-table").innerText()),
    "Annex C: the dates carry the form-level simulated mark");
  ok((await count(page, "#cn-checks li.fail")) === 0 && (await count(page, "#cn-checks li.pass")) >= 4,
    "credit note: noteHash, CSV round trip, no on-chain supersession, amount = the on-chain cap");

  // the replay facts are folded under the DZA card's "On the replay chain" summary: open it as a reader would
  await page.locator(".ledger details.more > summary").filter({ hasText: "On the replay chain" }).click();
  const ra = await page.locator("#replay-a").innerText(), rb = await page.locator("#replay-b");
  ok(/qualified clean passes counted on chain/.test(ra) && /paid 60 %, forfeited 40 %/.test(ra) && /no reserve was pending/.test(ra),
    "DZA card: the replay's outcome A renders from replay data (paid 60 %, forfeited 40 %, no reserve pending)");
  ok((await rb.locator(".simq").first().innerText()) === "look-ahead" && /reserve freed/.test(await rb.innerText()),
    "DZA card: outcome B renders with its purple look-ahead mark");
  ok(/SourceUnderReview/.test(await page.locator("#ledger").innerText()), "DZA card: the status-0 cancel while the relapse is PROPOSED is shown");
  ok(/SourceUnderReview/.test(await page.locator("#replay-freeze").innerText()), "DZA card: the release freeze (status-0 release in B) is shown");
  ok(/surety/.test(await page.locator("#replay-surety").innerText()), "DZA card: refunds and settlement went to the surety through the SuretyBond");

  const pfl = await page.locator("#portfolio-line").innerText();
  ok(/\d+ facilities/.test(pfl) && /plume that MARS did not see/.test(pfl) && /own no Carbon Mapper source/.test(pfl) && /NotCovered/.test(pfl),
    "clause card: the portfolio line renders the pipeline's sentence (selection, unsized leaves) and the NotCovered rejection");

  const win = page.locator("#clock-windows li.win").first();
  ok((await win.count()) === 1, "recorded run lists a skipped window");
  await win.locator("button").click();
  ok(await win.evaluate((li) => li.classList.contains("skipped")), "Skip this window marks the window skipped");
  ok(/chain clock advanced .+ — challenge window/.test(await win.locator(".win-res .simq").innerText()), "the skip carries its purple mark: chain clock advanced …");

  // the judge's path and the explorer table come from app.json, never typed in the page
  const deep = await page.locator("#judge-steps a.deep").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  ok(["?tamper=A", "?tamper=B", "?tamper=C", "#witness"].every((h) => deep.includes(h)), `judge's path links the three tamper deep links and the witness panel (${deep.join(" ")})`);
  const jl = await page.locator("#judge-steps a[href^='https://']").evaluateAll((as) => as.map((a) => a.href));
  const known = new Set([...S.run.steps.map((x) => x.tx), `${S.explorer}/address/${S.methaneCovenant}#readContract`,
    `${S.explorer}/address/${S.sandbox.methaneCovenant}`]);
  ok(jl.length > 0 && jl.every((h) => known.has(h)), `judge's path: all ${jl.length} explorer links are app.json's transactions and addresses`);
  const xt = await page.locator("#explorer-tx a").evaluateAll((as) => as.map((a) => a.href));
  ok(xt.length === S.run.steps.length && S.run.steps.every((x) => xt.includes(x.tx)),
    `explorer table: every Sepolia transaction of testnet_run.json linked (${xt.length}, ${S.run.steps.filter((x) => x.status === 0).length} status 0)`);
  const xa = await page.locator("#explorer-addr a").evaluateAll((as) => as.map((a) => a.textContent));
  const addrs = [S.methaneCovenant, S.keyRegistry, S.sandbox.methaneCovenant, S.sandbox.keyRegistry, S.seller, S.buyer, ...Object.values(S.observers)];
  ok(addrs.every((x) => xa.includes(x)), `explorer table: ${addrs.length} contracts and parties from the deployment record`);
  const rcpt = await page.locator("#self-checks li").filter({ hasText: "Sepolia receipts" }).innerText();
  const liveOk = /read live: (\d+)\/\1 /.test(rcpt);
  console.log(`         receipts path: ${liveOk ? `live, ${rcpt.match(/via (.*)$/)?.[1]}` : "not all live (committed record shown)"}`);
  ok(process.env.E2E_LIVE === "1" ? liveOk : liveOk || /could not be read from the public endpoints tried/.test(rcpt),
    `live receipts${process.env.E2E_LIVE === "1" ? " (E2E_LIVE=1: required)" : ""}: ${rcpt.slice(0, 120)}`);
  ok((await count(page, "#obs-steps li.ostep.done")) === 5, "the five witness steps replayed and passed");

  console.log("== deep links");
  for (const [key, verdict, checks, expect] of [
    ["A", "#tamper-verdict", "#sig-checks", /InvalidSignature/],
    ["B", "#miss-verdict", "#miss-checks", /re-fetch hash differs/],
    ["C", "#cloud-verdict", "#cloud-checks", /sceneHash changed/],
  ]) {
    const dl = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errs = await ready(dl, `${BASE}?tamper=${key}`);
    const red = await hasClass(dl, verdict, "bad"), fails = await dl.locator(`${checks} li.fail`).allInnerTexts();
    await dl.waitForTimeout(300);  // the scroll runs in the next animation frame
    const land = await dl.evaluate(([k]) => { const top = (s) => Math.round(document.querySelector(s).getBoundingClientRect().top);
      return k === "A" ? [top("#moment"), document.querySelector("#moment-3").classList.contains("bad"), innerHeight]
        : [top(`#tamper-${k.toLowerCase()}`), document.querySelector(`#tamper-${k.toLowerCase()}-box`).open, innerHeight]; }, [key]);
    ok(land[0] >= 0 && land[0] < land[2] / 2 && land[1],
      key === "A" ? `?tamper=A lands on the three-step proof strip (top ${land[0]} of ${land[2]} px) with its third step red`
        : `?tamper=${key} opens its section and lands on it (top ${land[0]} of ${land[2]} px)`);
    ok(errs.length === 0 && red && fails.some((t) => expect.test(t)) && (await count(dl, "#self-checks li.fail")) === 0
      && (await dl.getAttribute("body", "data-tamper")) === key && !(await dl.locator("#deeplink-note").isHidden()),
      `?tamper=${key} opens red (${fails.length} checks fail, ${expect.source}) with 0 failed self-checks`);
    const [editBtn, resetBtn] = { A: ["#tamper-btn", "#reset-btn"], B: ["#miss-btn", "#miss-reset"], C: ["#cloud-btn", "#cloud-reset"] }[key];
    const before = await dl.locator("#deeplink-note").innerText();
    await dl.locator(resetBtn).click();
    await dl.waitForFunction((s) => !document.querySelector(s).classList.contains("bad"), verdict);
    const after = await dl.locator("#deeplink-note").innerText();
    const tone = await dl.evaluate(() => { const n = document.querySelector("#deeplink-note"), s = getComputedStyle(n);
      return [n.dataset.state, s.color === getComputedStyle(document.querySelector("#self-checks li.pass")).color || s.backgroundColor === getComputedStyle(document.querySelector("#self-checks li.pass")).backgroundColor]; });
    const below = await dl.evaluate(([n, b]) => !!(document.querySelector(n).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING), ["#deeplink-note", editBtn]);
    ok(/^Opened with/.test(before) && after.startsWith(`Reset: tamper ${key} is undone`) && /the button below/.test(after) && below
      && (await dl.getAttribute("body", "data-tamper")) === null
      && (await count(dl, `${checks} li.fail`)) === 0 && tone[0] === "reset" && tone[1],
      `?tamper=${key} then Reset: the banner says the tamper is undone (green, no longer red) and body[data-tamper] is cleared`);
    await dl.locator(editBtn).click();
    await dl.waitForFunction((s) => document.querySelector(s).classList.contains("bad"), verdict);
    ok(await dl.locator("#deeplink-note").isHidden(), `?tamper=${key}, Reset, then its tamper button again: the stale banner is hidden`);
    await dl.close();
  }
  const pp = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await ready(pp, `${BASE}?persona=observer`);
  ok((await pp.locator("#actor-witness details").evaluate((x) => x.open)) && (await pp.getAttribute("body", "data-persona")) === "observer"
    && (await count(pp, "#self-checks li.fail")) === 0, "?persona=observer opens the witnesses' view (0 failed self-checks)");
  await pp.close();
  const pc = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await ready(pc, `${BASE}?ch=${total}`);
  ok((await pc.getAttribute("body", "data-chapter")) === String(total) && (await pc.locator("#ds-head").innerText()) === heads[total - 1],
    `?ch=${total} opens the last chapter`);
  await pc.close();
  const ph = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await ready(ph, `${BASE}#explorer`);
  ok(!(await ph.locator("#trust-body").isHidden()), "#explorer opens the drawer");
  await ph.close();

  console.log("== phone 375 px");
  {
    const pa = await browser.newPage({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await ready(pa, `${BASE}?tamper=A`);
    await pa.waitForTimeout(1500);   // late layout (maps, tiles, fonts) must not push the tamper out of view
    const pos = await pa.evaluate(() => { const r = document.querySelector("#moment").getBoundingClientRect(); return [Math.round(r.top), innerHeight, Math.round(scrollY)]; });
    ok(pos[2] > 0 && pos[0] >= -2 && pos[0] < pos[1] / 2 && (await hasClass(pa, "#moment-3", "bad")) && (await hasClass(pa, "#tamper-verdict", "bad")),
      `?tamper=A at 375 px scrolls to the proof strip: its top sits at ${pos[0]} px of ${pos[1]} (scrollY ${pos[2]}) and the verdict is red`);
    await pa.close();
  }
  const phone = await browser.newPage({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
  await ready(phone);
  const w = await phone.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]); w[1] = Math.min(w[1], 375);
  ok(w[0] <= w[1], `no horizontal scroll at 375 px (scrollWidth ${w[0]} ≤ ${w[1]})`);
  ok((await count(phone, "#self-checks li.fail")) === 0, "self-checks: 0 failed at 375 px");
  ok(await phone.evaluate(() => document.querySelector("#hero-h").getBoundingClientRect().top < innerHeight), "the sentence is in the first screen at 375 px");
  const cheatBox = await phone.evaluate(() => { const r = document.querySelector("#cheat-btn").getBoundingClientRect(); return [Math.round(r.top), Math.round(r.bottom), innerHeight]; });
  ok(cheatBox[0] >= 0 && cheatBox[1] <= cheatBox[2], `the cheat button is fully inside the first screen at 375×812 (${cheatBox[0]}–${cheatBox[1]} of ${cheatBox[2]})`);
  await phone.evaluate(() => { document.querySelector("#tamper-b-box").open = true; });
  const taps = await phone.evaluate(() => ["#ds-prev", "#ds-next", ".dot-btn", "label.tap"].map((s) => {
    const r = document.querySelector(s).getBoundingClientRect();
    return Math.min(r.width, r.height) >= 28 && Math.max(r.width, r.height) >= 44 && r.height >= 44;
  }));
  ok(taps.every(Boolean), `tap targets at 375 px are at least 44 px tall (prev, next, chapter dots, tamper B checkboxes: ${taps})`);
  const row = await phone.evaluate(() => {
    const tops = [...document.querySelectorAll(".dot-btn")].map((b) => Math.round(b.getBoundingClientRect().top));
    const box = (s) => document.querySelector(s).getBoundingClientRect();
    const dots = [...document.querySelectorAll(".dot-btn")].map((b) => b.getBoundingClientRect());
    const tabs = [...document.querySelectorAll(".arc-btn")].map((b) => Math.round(b.getBoundingClientRect().top));
    const bar = [...document.querySelectorAll(".top .brand, .top-right > *")].filter((e) => e.offsetParent)
      .map((e) => Math.round(e.getBoundingClientRect().top));
    return { n: tops.length, rows: new Set(tops).size, between: dots[0].left >= box("#ds-prev").right && dots.at(-1).right <= box("#ds-next").left,
      tabRows: new Set(tabs).size, barRows: 1 + bar.sort((a, b) => a - b).filter((t, i, a) => i > 0 && t - a[i - 1] > 10).length };
  });
  ok(row.n >= 7 && row.rows === 1 && row.between, `at 375 px the ${row.n} step dots sit on one row between the arrows`);
  ok(row.tabRows === 1, "at 375 px the Penalty and Earn-back tabs share one row");
  ok(row.barRows === 2, `at 375 px the top bar takes two rows with the Story map link (${row.barRows})`);
  await phone.evaluate(() => document.querySelector("#ds-next").scrollIntoView({ block: "end" }));
  await phone.locator("#ds-next").click();
  await phone.waitForTimeout(400);
  const mapSeen = await phone.evaluate(() => { const r = document.querySelector(".stage").getBoundingClientRect(); return r.bottom > 60 && r.top < innerHeight; });
  ok(mapSeen, "at 375 px the story map is in view after → changes the chapter");
  await openDrawer(phone);
  await phone.locator("#actor-seller summary").click();
  const w2 = await phone.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]); w2[1] = Math.min(w2[1], 375);
  ok(w2[0] <= w2[1], `no horizontal scroll at 375 px with the drawer and an actor view open (scrollWidth ${w2[0]} ≤ ${w2[1]})`);
  const dark = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" });
  await ready(dark);
  const ratios = await dark.evaluate(() => {
    const lum = (c) => { const v = c.match(/[\d.]+/g).slice(0, 3).map(Number).map((x) => x / 255).map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4));
      return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
    return ["#cheat-btn", "#witness-btn"].map((s) => { const cs = getComputedStyle(document.querySelector(s));
      const a = lum(cs.color), b = lum(cs.backgroundColor); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); });
  });
  ok(ratios.every((r) => r >= 4.5), `dark-scheme browser (the page keeps the story page's light theme): the cheat and witness buttons reach WCAG AA 4.5:1 (${ratios.map((r) => r.toFixed(2)).join(", ")})`);
  await dark.close();
  {  // own block: the names below do not collide with the main page checks
  console.log("== story page (story.html)");
  for (const [w, to] of [[1280, "story.html"], [375, ""]]) {  // the bare address: the map on a desktop, the full page on a phone
    const rp = await browser.newPage({ viewport: { width: w, height: 800 } });
    await rp.goto(BASE); await rp.waitForLoadState("domcontentloaded");
    const at = new URL(rp.url()).pathname.slice(1);
    ok(at === to, `the bare address at ${w} px opens ${to || "the full page"} (${at || "/"})`);
    if (w === 1280) {
      await rp.click("a.back"); await rp.waitForSelector("body[data-ready='1']", { timeout: 90_000 });
      ok(new URL(rp.url()).pathname === "/index.html", `"← Full page" opens the full page and stays there (${new URL(rp.url()).pathname})`);
    }
    await rp.close();
  }
  const openStory = async (vp) => {
    const sp = await browser.newPage({ viewport: vp });
    const sErr = [];
    sp.on("pageerror", (e) => sErr.push(String(e)));
    sp.on("console", (m) => { if (m.type() === "error" && !/^Failed to load resource:/.test(m.text())) sErr.push("console: " + m.text()); });
    sp.on("response", (r) => { if (r.url().startsWith(BASE) && r.status() >= 400) sErr.push(`HTTP ${r.status()} ${r.url()}`); });
    await sp.goto(BASE + "story.html");
    await sp.waitForSelector("html[data-story-ready='1']", { timeout: 90_000 });
    return [sp, sErr];
  };
  const textClash = () => {   // pairs of visible chart texts whose boxes intersect (runs in the page)
    const out = [];
    for (const id of ["c1", "c2", "c3"]) {
      const ts = [...document.querySelectorAll(`#${id} text`)].filter(t => t.style.display !== "none" && t.getBBox().width > 0), bs = ts.map(t => t.getBoundingClientRect());
      for (let a = 0; a < ts.length; a++) for (let b = a + 1; b < ts.length; b++) {
        const A = bs[a], B = bs[b];
        if (A.left < B.right - .5 && B.left < A.right - .5 && A.top < B.bottom - .5 && B.top < A.bottom - .5) out.push(`${id} "${ts[a].textContent.slice(0, 16)}" × "${ts[b].textContent.slice(0, 16)}"`);
      }
    }
    return out;
  };
  const [sp, sErr] = await openStory({ width: 1280, height: 800 });
  await sp.waitForFunction(() => window.story.prologue, null, { timeout: 10_000 });
  const pro = await sp.evaluate(() => [window.story.prologue, document.querySelectorAll("#tracksvg .band").length, document.querySelector("#tracksvg .err") ? 1 : 0, document.getElementById("story").scrollHeight - document.getElementById("story").clientHeight]);
  ok(pro[0] && pro[1] === 9 && pro[2] === 0 && pro[3] <= 1 && sErr.length === 0, `story opening screen: the timeline draws its ${pro[1]} bands, the story panel fits (+${pro[3]} px) and no console errors${sErr.length ? ": " + sErr[0] : ""}`);
  const nCh = await sp.evaluate(() => window.story.CH.length);
  let sTxt = "", cut = [];
  for (let i = 0; i < nCh; i++) for (const v of ["incident", "satellites", "contract", "verify"]) {
    const [txt, over, low, filled, clash] = await sp.evaluate(([i, v, f]) => { window.story.goChapter(i); window.story.setView(v); const el = document.getElementById("story"), low = Math.max(...[...document.querySelectorAll(".chart")].map(c => c.getBoundingClientRect().bottom));
      return [document.body.innerText, el.scrollHeight - el.clientHeight, Math.round(low - innerHeight), el.textContent.trim().length && document.getElementById("side").textContent.trim().length, (0, eval)(f)()]; }, [i, v, textClash.toString()]);
    for (const c of clash) cut.push(`ch${i + 1}/${v} ${c}`);
    if (!filled) cut.push(`ch${i + 1}/${v} empty panel`);
    sTxt += txt; if (over > 1) cut.push(`ch${i + 1}/${v} story +${over}px`); if (low > 0) cut.push(`ch${i + 1}/${v} charts +${low}px`);
  }
  ok(cut.length === 0, `story at 1280×800: the Demo story and side panels are filled, they and the chart row fit, and no two chart texts overlap, in every chapter and view${cut.length ? ": " + cut.join(", ") : ""}`);
  ok(nCh === 9, `story: ${nCh} chapters, each reachable in all four views`);
  {
    const create = S.run.steps.find((x) => x.step === "createCovenant"), bondTxt = eth(create.bondWei);   // the main page's "Bond posted"
    const seen = await sp.evaluate(() => [3, 4].map((i) => { window.story.goChapter(i); window.story.setView("incident");
      return [document.getElementById("side").innerText, document.getElementById("story").innerText,
        [...document.querySelectorAll("#c3 text")].map((t) => t.textContent),
        [...document.querySelectorAll("#side .st .simw")].map((s) => s.textContent)]; }));
    const backdated = S.run.createdAt > D.policy.start, num = bondTxt.replace(/ ETH$/, "");
    ok(seen.every(([side, , c3, marks]) => side.includes(`Bond ${bondTxt} locked`) && c3[0] === "Seller bond locked" && c3[1] === num
        && (backdated ? marks.includes("start backdated") : !/start backdated/.test(side)))
      && (!backdated || /created on .+ with its start backdated/.test(seen[0][1])),
      `story ch4–5: the seller's bond (${bondTxt}, the main page's "Bond posted") is locked from the clause chapter on, in the players and the money chart${backdated ? "; the clause player marks the backdated start purple; ch4 says the Sepolia clause was created later with its start backdated" : ""}`);
  }
  const hc = await sp.evaluate(async () => { location.hash = "#ch=3&view=contract"; await new Promise(r => setTimeout(r, 150)); return [window.story.chOf(window.story.T), window.story.view]; });
  ok(hc[0] === 2 && hc[1] === "contract", `story: a typed #ch=3&view=contract moves the page (chapter ${hc[0] + 1}, ${hc[1]})`);
  {   // the last chapter: its own side cards, then "Your turn" opens Verify on an end card, not a rewind
    const SJ = JSON.parse(await readFile(join(APP, "data", "story.json"), "utf8")).scene;
    const c9 = await sp.evaluate(() => { window.story.goChapter(window.story.CH.length - 1); window.story.setView("incident"); const side = document.getElementById("side");
      return [side.innerText, [...side.querySelectorAll(".simw")].map((s) => s.textContent), side.querySelectorAll(".chip.simc").length]; });
    const simParts = [...SJ.dza.clean.covenantLabel.matchAll(/([^,;:()[]+?)\s*\[SIM[^\]]*\]/g)].map((m) => m[1].trim());
    ok(simParts.length > 0 && simParts.every((w) => c9[1].includes(w)) && c9[1].includes("sponsor") && c9[2] >= 1 && /pinned clock/.test(c9[0]) && /What this does not prove/i.test(c9[0]),
      `story ch${nCh}: the replay marks ${simParts.join(", ")} and the sponsor purple, carries a "pinned clock" chip, and the "What this does not prove" card is shown`);
    const yt = await sp.evaluate(async () => { document.getElementById("yourturn").click(); await new Promise((r) => setTimeout(r, 150));
      return [location.hash, document.getElementById("story").textContent, document.getElementById("side").innerText, document.getElementById("casepill").innerText, window.story.view, window.story.CH.at(-2).head]; });
    ok(yt[0] === `#ch=${nCh}&view=verify` && yt[4] === "verify" && yt[1].includes(`chapter ${nCh} of ${nCh}`) && yt[1].includes(`Your turn: check the ${SJ.facility.country} case yourself`)
        && !yt[1].includes(yt[5]) && /check it yourself/i.test(yt[2]) && yt[3].includes(SJ.case),
      `story: "Your turn" stays on chapter ${nCh} (${yt[0]}) with an end card for the ${SJ.case} check, not chapter ${nCh - 1}'s text`);
    const vt = await sp.evaluate(() => [document.getElementById("moneysrc").textContent, document.querySelector(".tl").innerText]);
    ok(!vt[0].includes("replay") && !vt[1].includes(SJ.dza.source),
      `story: the end card's money and timeline label are the ${SJ.case} case, not the DZA replay (${vt[0]})`);
    const pr = await sp.evaluate(async () => { location.hash = "#prologue"; await new Promise((r) => setTimeout(r, 150)); return document.getElementById("side").innerText; });
    const H = SJ.headline;
    ok(pr.includes(`${H.n_cm_detected} of ${H.n_cm_passes}`) && pr.includes("(later)"),
      `story: the problem view quotes the whole record (${H.n_cm_detected} of ${H.n_cm_passes}) and marks the lead plume "(later)"`);
    const op = await sp.evaluate(() => { window.story.goChapter(0); window.story.setView("incident"); return [...document.querySelectorAll(".cast li")].map((l) => getComputedStyle(l).opacity); });
    ok(op.length > 0 && op.every((o) => o === "1"), "story players: resting rows keep full-opacity text (only the icon dims), so purple names keep their contrast");
  }
  ok(!/\[SIM/.test(sTxt), "story: no \"[SIM\" text in any chapter or view (simulated = purple, dashed, one legend)");
  ok(sTxt.includes("anyone may appeal with a deposit and an objective reason") && !/appeal with a bond/.test(sTxt),
    "story: an appeal is made with a deposit and an objective reason (the main page's words; \"bond\" stays the seller's)");
  ok(sErr.length === 0, `story: no uncaught page errors and no same-origin 4xx${sErr.length ? ": " + sErr[0] : ""}`);
  const hdr = await sp.evaluate(() => [document.getElementById("live").getBoundingClientRect().right, innerWidth]);
  ok(hdr[0] <= hdr[1], `story at 1280 px: the header fits, the Sepolia link is reachable (${Math.round(hdr[0])} ≤ ${hdr[1]})`);
  await sp.close();
  {   // a slow network: no imagery tile by the 15-s check shows the note; the first tile that arrives later removes it
    const IMG = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/**";
    const sl = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await sl.route(IMG, (r) => r.abort());
    await sl.goto(BASE + "story.html");
    await sl.waitForSelector("html[data-story-ready='1']", { timeout: 90_000 });
    await sl.evaluate(() => window.story.tileCheck());   // what the 15-s timer runs
    const shown = await sl.locator("#mapwrap .offline").count();
    await sl.unroute(IMG);
    await sl.evaluate(() => { window.story.goChapter(window.story.CH.length - 1); window.story.goChapter(0); });   // new views ask for new tiles
    const gone = await sl.waitForFunction(() => !document.querySelector("#mapwrap .offline"), null, { timeout: 30_000 }).then(() => true, () => false);
    ok(shown === 1 && gone, `story: the "tiles could not load" note shows when no imagery tile has arrived (${shown}) and goes away when one arrives later (${gone})`);
    await sl.close();
  }
  const [s10, s10Err] = await openStory({ width: 1024, height: 768 });   // the smallest desktop: money bars and chart captions
  await s10.waitForFunction(() => window.story.prologue, null, { timeout: 10_000 });
  const narrow = await s10.evaluate((f) => {
    const clash = (0, eval)(f);
    const bad = [], check = (tag) => {
      const bars = [...document.querySelectorAll('#c3 rect[fill="#f2eee7"]')].map(r => +r.getAttribute("width"));
      if (bars.length !== 3 || bars.some(w => w <= 20)) bad.push(`${tag} money bars ${bars.join("/")}px`);
      for (const id of ["c1", "c2", "c3"]) {
        const sb = document.getElementById(id).getBoundingClientRect();
        for (const t of document.querySelectorAll(`#${id} text`)) { const b = t.getBoundingClientRect(); if (b.width && (b.left < sb.left - 1 || b.right > sb.right + 1)) bad.push(`${tag} ${id} "${t.textContent.slice(0, 24)}"`); }
      }
    };
    check("prologue"); for (const c of clash()) bad.push(`prologue ${c}`);
    for (const v of ["incident", "satellites", "contract", "verify"]) window.story.CH.forEach((_, i) => { window.story.goChapter(i); window.story.setView(v); check(`ch${i + 1}/${v}`); for (const c of clash()) bad.push(`ch${i + 1}/${v} ${c}`); });
    return bad;
  }, textClash.toString());
  ok(narrow.length === 0 && s10Err.length === 0, `story at 1024×768: money bars wider than 20 px, every chart caption inside its chart and no two chart texts overlap, opening screen and every chapter × view${narrow.length ? ": " + narrow.slice(0, 3).join(", ") : ""}`);
  await s10.close();
  const [scam, scamErr] = await openStory({ width: 1440, height: 900 });   // the camera card follows a window resize
  const camState = () => scam.evaluate(() => {
    const box = document.getElementById("camimg"), r = box.getBoundingClientRect(), t = document.getElementById("hud-tag").getBoundingClientRect(), c = document.querySelector(".hud .cross").getBoundingClientRect();
    const m = document.querySelector("#camimg .mos").style.transform.match(/translate\(([-\d.]+)px,\s*([-\d.]+)px\)/), cx = c.left + c.width / 2, cy = c.top + c.height / 2;
    return { off: [Math.round(+m[1] - box.clientWidth / 2), Math.round(+m[2] - box.clientHeight / 2)], inside: t.left >= r.left - .5 && t.right <= r.right + .5, clear: !(t.left < cx + 34 && t.right > cx - 34 && t.top < cy + 34 && t.bottom > cy - 34) };
  });
  await scam.evaluate(() => { window.story.goChapter(4); window.story.setView("satellites"); });
  await scam.waitForTimeout(400);
  const cam1 = await camState();
  await scam.setViewportSize({ width: 1024, height: 768 });
  await scam.waitForTimeout(600);
  const cam2 = await camState();
  ok(cam1.inside && cam1.clear && cam2.inside && cam2.clear && cam2.off.join() === cam1.off.join() && scamErr.length === 0,
    `story camera card: after a 1440 → 1024 resize the scene stays centred on the reticle (${cam1.off} → ${cam2.off}) and the plume tag stays inside the card, clear of the aim point`);
  await scam.close();
  const [sph] = await openStory({ width: 375, height: 812 });
  const sw = await sph.evaluate(() => [document.documentElement.scrollWidth, innerWidth, document.querySelector(".narrow").getBoundingClientRect().top,
    Math.round(Math.max(...["#track", ".charts", ".tlhead"].map(s => document.querySelector(s).getBoundingClientRect().right)))]);
  ok(sw[0] <= sw[1] && sw[2] < 812 && sw[3] <= sw[1], `story at 375 px: no horizontal scroll (${sw[0]} ≤ ${sw[1]}), the timeline and charts end inside the screen (${sw[3]}) and the desktop note is in the first screen`);
  await sph.close();
  }
  console.log(`\nE2E OK: ${n} checks in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
} catch (e) {
  console.log(`\nE2E FAILED after ${n} checks: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
  server.close();
}
