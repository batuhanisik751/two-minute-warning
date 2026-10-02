// A small headless-Chrome driver for the layout check (tests/smoke/layout.test.ts) and for
// screenshots: it starts the Chrome already on the machine and talks the Chrome DevTools
// Protocol over Node's built-in WebSocket (Node 22), so the tests need no new dependency and
// no browser download. Chrome: CHROME_PATH, else the usual install locations (macOS, and
// google-chrome on the GitHub Ubuntu runners).
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
];

export function findChrome(): string | null {
  const env = process.env.CHROME_PATH?.trim();
  if (env) return existsSync(env) ? env : null;
  return CANDIDATES.find((c) => existsSync(c)) ?? null;
}

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; method: string };
type Message = { id?: number; method?: string; params?: Record<string, unknown>; sessionId?: string; result?: unknown; error?: { message: string } };

class Connection {
  private next = 1;
  private pending = new Map<number, Pending>();
  private listeners = new Set<(m: Message) => void>();

  constructor(private ws: WebSocket) {
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(String(ev.data)) as Message;
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
        else p.resolve(msg.result);
      } else {
        for (const l of this.listeners) l(msg);
      }
    });
    ws.addEventListener("close", () => {
      for (const p of this.pending.values()) p.reject(new Error(`${p.method}: the browser closed the connection`));
      this.pending.clear();
    });
  }

  send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}, sessionId?: string): Promise<T> {
    const id = this.next++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, method });
      this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  /** Resolves on the first event `method` of `sessionId` (after `timeoutMs`, rejects). */
  once(method: string, sessionId: string, timeoutMs = 30_000): Promise<Message> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.listeners.delete(l);
        reject(new Error(`timed out waiting for ${method}`));
      }, timeoutMs);
      const l = (m: Message) => {
        if (m.method === method && m.sessionId === sessionId) {
          clearTimeout(timer);
          this.listeners.delete(l);
          resolve(m);
        }
      };
      this.listeners.add(l);
    });
  }
}

export class Tab {
  constructor(
    private conn: Connection,
    private session: string,
  ) {}

  private send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    return this.conn.send<T>(method, params, this.session);
  }

  /** The layout viewport (CSS px). Below 768 px it is a phone (mobile viewport rules). */
  async setViewport(width: number, height = 900, deviceScaleFactor = 1): Promise<void> {
    await this.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor, mobile: width < 768 });
    await this.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))");
  }

  /** A raw DevTools call on this tab (the performance measurement, tests/perf.ts). */
  cdp<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    return this.send<T>(method, params);
  }

  async setColorScheme(scheme: "light" | "dark"): Promise<void> {
    await this.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
  }

  /** Loads `url` and waits for the load event and the web fonts. */
  async goto(url: string): Promise<void> {
    const loaded = this.conn.once("Page.loadEventFired", this.session);
    const nav = await this.send<{ errorText?: string }>("Page.navigate", { url });
    if (nav.errorText) throw new Error(`could not load ${url}: ${nav.errorText}`);
    await loaded;
    await this.evaluate("document.fonts.ready.then(() => true)");
  }

  async evaluate<T>(expression: string): Promise<T> {
    const r = await this.send<{ result: { value?: T }; exceptionDetails?: { text: string; exception?: { description?: string } } }>(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true },
    );
    if (r.exceptionDetails) {
      throw new Error(`page script failed: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    }
    return r.result.value as T;
  }

  /** A PNG of the whole page (not just the viewport). */
  async screenshot(path: string): Promise<void> {
    const m = await this.send<{ cssContentSize: { width: number; height: number }; cssLayoutViewport: { clientWidth: number } }>(
      "Page.getLayoutMetrics",
    );
    const shot = await this.send<{ data: string }>("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: m.cssLayoutViewport.clientWidth, height: Math.ceil(m.cssContentSize.height), scale: 1 },
    });
    writeFileSync(path, Buffer.from(shot.data, "base64"));
  }
}

export class Chrome {
  private constructor(
    private child: ChildProcess,
    private conn: Connection,
    private ws: WebSocket,
    private profile: string,
  ) {}

  /** Start headless Chrome. A slow start on a busy CI runner gets one more try; a failed start
   *  always kills the Chrome it spawned (a leftover Chrome kept the whole smoke run alive until
   *  the CI job timed out, 2026-09-29). */
  static async launch(executable: string, attempts = 2, startTimeoutMs = 60_000): Promise<Chrome> {
    let last: unknown;
    for (let i = 1; i <= attempts; i++) {
      try {
        return await Chrome.launchOnce(executable, startTimeoutMs);
      } catch (err) {
        last = err;
        console.warn(`  Chrome start attempt ${i}/${attempts} failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    throw last instanceof Error ? last : new Error(String(last));
  }

  private static async launchOnce(executable: string, startTimeoutMs: number): Promise<Chrome> {
    const profile = mkdtempSync(join(tmpdir(), "twm-chrome-"));
    const args = [
      "--headless=new",
      "--remote-debugging-port=0",
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-gpu",
      "--disable-extensions",
      "--disable-background-networking",
      "--disable-sync",
      "--hide-scrollbars",
      "--mute-audio",
      "--force-color-profile=srgb",
      // the GitHub Ubuntu runners cannot start Chrome's sandbox (only local pages are loaded)
      ...(process.platform === "linux" ? ["--no-sandbox"] : []),
      "about:blank",
    ];
    const child = spawn(executable, args, { stdio: ["ignore", "ignore", "pipe"] });
    const giveUp = () => {
      child.stderr?.removeAllListeners("data");
      child.stderr?.destroy();
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      child.unref();
      try {
        rmSync(profile, { recursive: true, force: true });
      } catch {
        /* best effort */
      }
    };
    try {
      const endpoint = await new Promise<string>((resolve, reject) => {
        let buf = "";
        const timer = setTimeout(
          () => reject(new Error(`Chrome did not start within ${startTimeoutMs / 1000} s`)),
          startTimeoutMs,
        );
        child.stderr!.on("data", (d) => {
          buf += String(d);
          const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
          if (m) {
            clearTimeout(timer);
            resolve(m[1]);
          }
        });
        child.once("exit", (code) => {
          clearTimeout(timer);
          reject(new Error(`Chrome exited with code ${code}: ${buf.slice(-500)}`));
        });
      });
      const ws = new WebSocket(endpoint);
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("could not connect to Chrome within 15 s")), 15_000);
        ws.addEventListener("open", () => (clearTimeout(timer), resolve()), { once: true });
        ws.addEventListener("error", () => (clearTimeout(timer), reject(new Error("could not connect to Chrome"))), { once: true });
      });
      return new Chrome(child, new Connection(ws), ws, profile);
    } catch (err) {
      giveUp();
      throw err;
    }
  }

  async newTab(): Promise<Tab> {
    const { targetId } = await this.conn.send<{ targetId: string }>("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await this.conn.send<{ sessionId: string }>("Target.attachToTarget", { targetId, flatten: true });
    await this.conn.send("Page.enable", {}, sessionId);
    await this.conn.send("Runtime.enable", {}, sessionId);
    return new Tab(this.conn, sessionId);
  }

  async close(): Promise<void> {
    try {
      await this.conn.send("Browser.close");
    } catch {
      // already gone
    }
    this.ws.close();
    if (this.child.exitCode === null) {
      await new Promise<void>((resolve) => {
        const t = setTimeout(() => {
          this.child.kill("SIGKILL");
          resolve();
        }, 5000);
        this.child.once("exit", () => {
          clearTimeout(t);
          resolve();
        });
      });
    }
    rmSync(this.profile, { recursive: true, force: true });
  }
}
