// Measures the BUILT app's pages in headless Chrome (tests/smoke/chrome.ts): Lighthouse is not
// installed and no package is added for it. Per page, cold cache, the median of RUNS loads:
// LCP, CLS (layout shifts without input), TBT (long tasks after the first paint, the part over
// 50 ms each), FCP, TTFB, the HTML and the JavaScript transferred. Two profiles: "mobile" is
// close to Lighthouse's (412 px, 4x CPU slowdown, 150 ms RTT, 1.6 Mbps down) but uses Chrome's
// own throttling, not Lighthouse's simulation, so it is a guide, not a Lighthouse score.
//
//   npx tsx tests/perf.ts http://127.0.0.1:3000 / /methodology /waivers /time-machine
import { Chrome, findChrome, type Tab } from "./smoke/chrome";

const RUNS = Number(process.env.PERF_RUNS ?? 3);

const OBSERVE = `(() => { const p = window.__perf = { lcp: 0, cls: 0, long: [] };
  new PerformanceObserver((l) => { for (const e of l.getEntries()) p.lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) p.cls += e.value; }).observe({ type: "layout-shift", buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) p.long.push([e.startTime, e.duration]); }).observe({ type: "longtask", buffered: true });
})()`;

const COLLECT = `(() => { const p = window.__perf; const nav = performance.getEntriesByType("navigation")[0];
  const fcp = performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? 0;
  const tbt = p.long.filter(([s]) => s >= fcp).reduce((a, [, d]) => a + Math.max(0, d - 50), 0);
  const js = performance.getEntriesByType("resource").filter((r) => /\\.js(\\?|$)/.test(r.name));
  return { ttfb: nav.responseStart, fcp, lcp: p.lcp, cls: p.cls, tbt, htmlKB: nav.transferSize / 1024,
    jsKB: js.reduce((a, r) => a + r.transferSize, 0) / 1024, jsRawKB: js.reduce((a, r) => a + r.decodedBodySize, 0) / 1024, scripts: js.length };
})()`;

type Result = { ttfb: number; fcp: number; lcp: number; cls: number; tbt: number; htmlKB: number; jsKB: number; jsRawKB: number; scripts: number };
type Profile = { name: string; width: number; cpu: number; net: Record<string, unknown> | null };

const PROFILES: Profile[] = [
  { name: "mobile", width: 412, cpu: 4, net: { offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 } },
  { name: "desktop", width: 1350, cpu: 1, net: null },
];

async function measure(chrome: Chrome, url: string, prof: Profile): Promise<Result> {
  const tab: Tab = await chrome.newTab();
  await tab.cdp("Network.enable");
  await tab.cdp("Network.setCacheDisabled", { cacheDisabled: true });
  if (prof.net) await tab.cdp("Network.emulateNetworkConditions", prof.net);
  await tab.cdp("Emulation.setCPUThrottlingRate", { rate: prof.cpu });
  await tab.setViewport(prof.width, prof.width < 768 ? 823 : 940);
  await tab.cdp("Page.addScriptToEvaluateOnNewDocument", { source: OBSERVE });
  await tab.goto(url);
  await new Promise((r) => setTimeout(r, 4000)); // late long tasks and shifts
  const r = await tab.evaluate<Result>(COLLECT);
  await tab.cdp("Page.close").catch(() => undefined);
  return r;
}

const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];

async function main(): Promise<void> {
  const [base, ...paths] = process.argv.slice(2);
  const exe = findChrome();
  if (!base || !paths.length || !exe) throw new Error("usage: tsx tests/perf.ts BASE PATH... (and Chrome installed)");
  for (const p of paths) await fetch(base + p); // warm the server's data cache
  const chrome = await Chrome.launch(exe);
  try {
    for (const prof of PROFILES) {
      for (const p of paths) {
        const runs: Result[] = [];
        for (let i = 0; i < RUNS; i++) runs.push(await measure(chrome, base + p, prof));
        const m = (k: keyof Result) => median(runs.map((r) => r[k]));
        console.log(
          `${prof.name.padEnd(7)} ${p.padEnd(14)} TTFB ${m("ttfb").toFixed(0)} ms  FCP ${m("fcp").toFixed(0)} ms  LCP ${m("lcp").toFixed(0)} ms  ` +
            `CLS ${m("cls").toFixed(3)}  TBT ${m("tbt").toFixed(0)} ms  HTML ${m("htmlKB").toFixed(0)} KB  JS ${m("jsKB").toFixed(0)} KB ` +
            `(${m("jsRawKB").toFixed(0)} KB raw, ${m("scripts")} files)`,
        );
      }
    }
  } finally {
    await chrome.close();
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
