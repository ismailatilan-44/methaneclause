// One-file end-to-end check of the page (make e2e). The page is told in three layers: the five-second view (who pays
// whom, two money arrows, the analogy, one button into the failure moment), the story on real data (TKM_S_276 penalty
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

let n = 0;
const ok = (cond, msg) => {
  if (!cond) { console.log(`   FAIL  ${msg}`); throw new Error(msg); }
  n++; console.log(`   ok    ${msg}`);
};
const channel = process.env.E2E_CHANNEL === undefined ? "chrome" : process.env.E2E_CHANNEL || undefined;
const browser = await chromium.launch({ channel, headless: true });
const t0 = Date.now();
const D = JSON.parse(await readFile(join(APP, "data", "app.json"), "utf8")), S = D.sepolia;
const eth = (wei) => { const w = BigInt(wei), f = (w % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return `${w / 10n ** 18n}${f ? "." + f : ""} ETH`; };
const fmt = new Intl.NumberFormat("en-US");

async function ready(page, url = BASE) {
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
  const errors = await ready(page);
  console.log(`== desktop 1280 px (${BASE})`);
  ok(errors.length === 0, `no uncaught page errors and no same-origin 4xx (BUILD.json included)${errors.length ? ": " + errors[0] : ""}`);
  ok((await count(page, "#self-checks li.fail")) === 0, "self-checks: 0 failed");
  ok((await count(page, "#self-checks li.pass")) >= 6, `self-checks: ${await count(page, "#self-checks li.pass")} passed`);
  const pill0 = await page.locator("#check-pill").innerText();
  ok(/^(✓ \d+ checks passed in your browser|Checks running in your browser…)$/.test(pill0), `header pill: a final count or "running", never a partial count (${pill0})`);

  // simulated = purple, dashed, one legend: no "[SIM" text anywhere on the page (drawer included), the mark is drawn
  const sim = await page.evaluate(() => ({ text: document.body.textContent.includes("[SIM"),
    attrs: [...document.querySelectorAll("[title],[aria-label]")].some((e) => /\[SIM/.test(e.title + (e.getAttribute("aria-label") || ""))),
    marks: document.querySelectorAll(".simk, .simq, .simw").length, legend: document.querySelectorAll(".keyl .lchip").length }));
  ok(!sim.text && !sim.attrs && sim.marks > 10 && sim.legend === 2,
    `no "[SIM" text or attribute on the page; ${sim.marks} purple dashed marks; one legend (real data / simulated)`);

  console.log("== layer 0: the first view");
  const h1 = await page.locator("#hero-h").innerText();
  const line = (k) => page.locator(`#hero-h .l:nth-child(${k})`).innerText();
  ok((await page.locator("#hero-lead").innerText()) === 'A speed camera for methane, written into the gas contract.'
    && (await line(1)) === "Satellites confirm a leak → the seller's bond pays the buyer."
    && (await line(2)) === "No new leak on later satellite passes → the seller earns money back in steps, never more than the rebate, from a sponsor's pool (for example its lender's)."
    && (await line(3)) === 'Once enough witnesses sign a sighting, no one — the seller included — can delete it or hold back the rebate on their own.', "the lead line and the three-line headline, verbatim (canonical v2)");
  ok((await page.locator(".analogy").innerText()) === "Public satellite records are the cameras; independent witnesses (separate organisations) each fetch the record and sign it; when enough of them sign the same leak and the appeal window passes, the seller's bond pays the buyer — the ticket goes to the buyer, not to a state. A clean pass never undoes a paid rebate or proves a repair; it only earns money back from the sponsor's pool.",
    "the body line, verbatim (canonical v2)");
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
  const above = await page.evaluate(() => ["#hero-h", "#money-fig", "#cheat-btn"].every((s) => document.querySelector(s).getBoundingClientRect().top < innerHeight));
  ok(above, "sentence, picture and the cheat button are in the first screen");
  const lead = D.hybrid.entries[0].bundle, tam = S.run.steps.find((x) => x.step === "reject_tamper");
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

  const heads = [], links = new Set(), codes = new Set();
  await page.locator(".arc-btn[data-arc='tkm']").click();
  let total = 0;
  for (let i = 0; i < 20; i++) {
    heads.push(await page.locator("#ds-head").innerText());
    for (const h of await page.locator("#ds-facts a[href^='https://']").evaluateAll((as) => as.map((a) => a.href))) links.add(h);
    for (const c of await page.locator("#ds-facts code[title]").evaluateAll((cs) => cs.map((c) => c.title))) codes.add(c);
    total++;
    if (await page.locator("#ds-next").isDisabled()) break;
    await page.click("#ds-next");
  }
  ok(total >= 10 && new Set(heads).size === total && heads.every((h) => h.length > 8),
    `every story chapter reachable with Next: ${total} chapters, each with its own headline`);
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
  ok((await page.locator("#ds-arc").textContent()).startsWith("Earn-back") && /chapter 1 of/.test(await page.locator("#ds-count").textContent()),
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

  console.log("== layer 2: the drawer");
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
    ok(errs.length === 0 && red && fails.some((t) => expect.test(t)) && (await count(dl, "#self-checks li.fail")) === 0
      && (await dl.getAttribute("body", "data-tamper")) === key && !(await dl.locator("#deeplink-note").isHidden()),
      `?tamper=${key} opens red (${fails.length} checks fail, ${expect.source}) with 0 failed self-checks`);
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
  const phone = await browser.newPage({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
  await ready(phone);
  const w = await phone.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]); w[1] = Math.min(w[1], 375);
  ok(w[0] <= w[1], `no horizontal scroll at 375 px (scrollWidth ${w[0]} ≤ ${w[1]})`);
  ok((await count(phone, "#self-checks li.fail")) === 0, "self-checks: 0 failed at 375 px");
  ok(await phone.evaluate(() => document.querySelector("#hero-h").getBoundingClientRect().top < innerHeight), "the sentence is in the first screen at 375 px");
  const taps = await phone.evaluate(() => ["#ds-prev", "#ds-next", ".dot-btn", "label.tap"].map((s) => {
    const r = document.querySelector(s).getBoundingClientRect();
    return Math.min(r.width, r.height) >= 28 && Math.max(r.width, r.height) >= 44 && r.height >= 44;
  }));
  ok(taps.every(Boolean), `tap targets at 375 px are at least 44 px tall (prev, next, chapter dots, tamper B checkboxes: ${taps})`);
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
  ok(ratios.every((r) => r >= 4.5), `dark mode: the cheat and witness buttons reach WCAG AA 4.5:1 (${ratios.map((r) => r.toFixed(2)).join(", ")})`);
  await dark.close();
  {  // own block: the names below do not collide with the main page checks
  console.log("== story page (story.html)");
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
  const hc = await sp.evaluate(async () => { location.hash = "#ch=3&view=contract"; await new Promise(r => setTimeout(r, 150)); return [window.story.chOf(window.story.T), window.story.view]; });
  ok(hc[0] === 2 && hc[1] === "contract", `story: a typed #ch=3&view=contract moves the page (chapter ${hc[0] + 1}, ${hc[1]})`);
  ok(!/\[SIM/.test(sTxt), "story: no \"[SIM\" text in any chapter or view (simulated = purple, dashed, one legend)");
  ok(sErr.length === 0, `story: no uncaught page errors and no same-origin 4xx${sErr.length ? ": " + sErr[0] : ""}`);
  const hdr = await sp.evaluate(() => [document.getElementById("live").getBoundingClientRect().right, innerWidth]);
  ok(hdr[0] <= hdr[1], `story at 1280 px: the header fits, the Sepolia link is reachable (${Math.round(hdr[0])} ≤ ${hdr[1]})`);
  await sp.close();
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
