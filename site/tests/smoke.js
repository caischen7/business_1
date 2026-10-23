// Playwright smoke test for the Product Looker. Run with serve.py running:
//   cd site/tests && NODE_PATH=$(npm root -g) node smoke.js
// The page is wrapped in the skeleton claude.ai adds at publish time, and window.claude is mocked
// so the verdict path runs: the mock answer quotes one real review and one invented one, and the
// quote check must catch the invented one.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:8765/";
const OUT = path.join(__dirname, "out");
fs.mkdirSync(OUT, { recursive: true });
let failed = 0;
const check = (ok, msg) => { console.log((ok ? "ok   " : "FAIL ") + msg); if (!ok) failed++; };

(async () => {
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: scheme });
    const errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    // External fonts and cdnjs can be unreachable from a sandbox; only the page's own errors count.
    page.on("console", m => { if (m.type() === "error" && !/Failed to load resource: net::/.test(m.text())) errors.push(m.text()); });
    await page.addInitScript(() => {
      window.claude = { use: async name => {
        if (name === "sample") return async (prompt, opts) => {
          const pk = JSON.parse(prompt.slice(prompt.indexOf("{"), prompt.lastIndexOf("}") + 1).split("\n\nWrite a report")[0]);
          const real = pk.quotes[0] ? pk.quotes[0].text.split(" ").slice(0, 8).join(" ") : "none";
          const text = `## Verdict\nTest it.\n\n## Scorecard\n| item | rating |\n|---|---|\n| Demand | green |\n\nA buyer said "${real}" and another said "this quote was invented by the mock model".`;
          opts && opts.onText && opts.onText({ text, delta: text });
          return { text, truncated: false };
        };
        if (name === "downloads") return { save: async () => ({}) };
        return null;
      } };
    });
    await page.goto(BASE + "index.html");
    await page.waitForSelector(".score .chip", { timeout: 60000 });
    check(true, `${scheme}: example lookup rendered a scorecard`);
    const rows = await page.$$eval("input.comp", els => els.length);
    check(rows >= 3, `${scheme}: example lookup found ${rows} listings`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    check(!overflow, `${scheme}: no horizontal page scroll at 1280px`);
    await page.screenshot({ path: path.join(OUT, `lookup-${scheme}.png`), fullPage: true });

    // untick one comparable and check the page re-renders
    await page.click("input.comp >> nth=0");
    await page.waitForSelector(".score .chip");
    check(true, `${scheme}: unticking a listing re-renders`);

    // Claude verdict with quote check
    await page.click("#askClaude");
    await page.waitForSelector(".qcheck", { timeout: 20000 });
    const qc = await page.textContent(".qcheck");
    check(/1 of 2 quotes/.test(qc), `${scheme}: quote check flags the invented quote (${qc.trim().slice(0, 60)})`);

    // a search across all categories
    await page.fill("#q", "yoga mat");
    await page.selectOption("#cat", "");
    await page.click("#go");
    await page.waitForFunction(() => document.querySelector("#results h2") && /yoga mat/.test(document.querySelector("#results h2").textContent), null, { timeout: 120000 });
    check(true, `${scheme}: all-category search for "yoga mat" rendered`);

    // niches
    await page.click("#tab-niches");
    await page.waitForSelector("#nicheTable tbody tr");
    const nrows = await page.$$eval("#nicheTable tbody tr", els => els.length);
    check(nrows > 10, `${scheme}: niche board shows ${nrows} rows`);
    await page.waitForSelector("#nicheDetail h2");
    await page.screenshot({ path: path.join(OUT, `niches-${scheme}.png`), fullPage: true });
    await page.click("#nicheLook");
    await page.waitForSelector("#view-lookup:not([hidden]) .score .chip, #view-lookup:not([hidden]) #results h2", { timeout: 60000 });
    check(true, `${scheme}: "look up a product in this niche" ran a lookup`);

    // calculator
    await page.click("#tab-margin");
    await page.fill("#m_cpa", "100");
    const chip = await page.textContent("#calcOut .chip");
    check(/Losing/.test(chip), `${scheme}: calculator reacts to a $100 ad cost (${chip})`);

    // phone width
    await page.setViewportSize({ width: 390, height: 844 });
    await page.click("#tab-lookup");
    const pov = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    check(!pov, `${scheme}: no horizontal page scroll at 390px`);
    await page.screenshot({ path: path.join(OUT, `phone-${scheme}.png`), fullPage: false });

    check(errors.length === 0, `${scheme}: no console errors${errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""}`);
    await page.close();
  }
  await browser.close();
  console.log(failed ? `${failed} check(s) failed` : "all checks passed");
  process.exit(failed ? 1 : 0);
})();
