// Screenshots and PDF export with the locally installed Chrome or Edge (no npm packages).
//
//   node docs/tools/capture.mjs shot <url> <out.png> [--width 1440] [--height N] [--eval "<js run before the shot>"] [--clip "<css selector>"]
//   node docs/tools/capture.mjs pdf <file.html> <out.pdf>
//
// Set CHROME_PATH if the browser is somewhere unusual.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [mode, target, out, ...rest] = process.argv.slice(2);
const flag = (name, fallback = "") => {
    const i = rest.indexOf(`--${name}`);
    return i >= 0 ? rest[i + 1] : fallback;
};
if (!["shot", "pdf"].includes(mode) || !target || !out) {
    console.error("usage: capture.mjs shot <url> <out.png> [--width N] [--eval JS] [--clip SELECTOR] | pdf <file.html> <out.pdf>");
    process.exit(2);
}

const candidates = [
    process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
].filter(Boolean);
const browser = candidates.find((p) => existsSync(p));
if (!browser) throw new Error("Chrome/Edge not found; set CHROME_PATH");

const width = Number(flag("width", "1440"));
const profile = mkdtempSync(join(tmpdir(), "capture-"));
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(browser, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--hide-scrollbars", `--window-size=${width},900`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
    let targets;
    for (let i = 0; i < 50 && !targets; i++) {
        try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(200); }
    }
    const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r));
    let seq = 0;
    const pending = new Map();
    ws.addEventListener("message", (e) => {
        const msg = JSON.parse(e.data);
        if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    });
    const send = (method, params = {}) => new Promise((r) => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
    const evaluate = async (expression) =>
        (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result?.result?.value;

    await send("Page.enable");
    const url = mode === "pdf" ? pathToFileURL(resolve(target)).href : target;

    if (mode === "pdf") {
        await send("Page.navigate", { url });
        await sleep(2500);
        const pdf = await send("Page.printToPDF", { printBackground: true, preferCSSPageSize: true, displayHeaderFooter: false });
        writeFileSync(out, Buffer.from(pdf.result.data, "base64"));
    } else {
        await send("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: width < 600 });
        await send("Page.navigate", { url });
        await sleep(4000);
        const pre = flag("eval");
        if (pre) { await evaluate(pre); await sleep(3500); }
        // freeze chart animations so the capture is not mid-transition
        await evaluate("window.Chart && Object.values(Chart.instances).forEach(c => { c.options.animation = false; c.update('none'); })");
        await sleep(400);
        const sel = flag("clip");
        const clip = sel
            ? await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height, scale: 1 }; })()`)
            : { x: 0, y: 0, width, height: await evaluate("document.documentElement.scrollHeight"), scale: 1 };
        if (flag("height")) clip.height = Math.min(clip.height, Number(flag("height")));
        const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip });
        writeFileSync(out, Buffer.from(shot.result.data, "base64"));
    }
    console.log(`saved ${out}`);
    ws.close();
} finally {
    proc.kill();
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* browser may still hold the profile briefly */ }
}
