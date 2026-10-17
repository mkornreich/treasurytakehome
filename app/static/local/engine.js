// The text scanner in the browser: tesseract.js 7 with the same Tesseract engine and English model
// as the server, loaded only from this server (no outside sites). It reads a label the way the server
// pipeline (app/pipeline.py) does, in rounds of different clean-ups, and stops when every check is OK.
//
//   const session = new Session(files);
//   await session.firstRound();       // one plain reading per picture (turned upright if sideways)
//   session.readings();               // what /api/verify-readings takes: words, places, heading weight
//   await session.finish(application) // the whole check in the browser, with the rules in rules.js
//   session.stop();                   // start no more passes (the server answered first)

import Tesseract from "../vendor/tesseract-7.0.0/tesseract.esm.min.js";
import {
  View, binarize, darkestFromRGBA, enhance, grayFromRGBA, headingWeight, qualityNotices, skewDegrees,
} from "./imaging.js";
import { PASS, normalizeApplication, overall, verify } from "./rules.js";
import { loose } from "./text.js";
import { VOCABULARY, readWarning } from "./warning.js";

const VENDOR = new URL("../vendor/tesseract-7.0.0/", import.meta.url).href;
const SPARSE = "11"; // psm 11: labels are scattered blocks of text
const LAYOUT = "3"; // psm 3: page layout; finds text boxed in tables
const BLOCK = "6"; // psm 6: one block of text, for the enlarged warning paragraph
const MIN_UPRIGHT_WORDS = 12; // fewer words than this on the first pass -> the photo may be sideways
const MIN_SKEW_DEGREES = 0.8;
const WARNING_LINE_PX = 36;
const OCR_BUDGET_MS = 3500;
const PLAN = ["standard", "layout", "enhanced", "rotate-90", "rotate-270", "binarized", "enhanced-gray", "rotate-180"];
// A tiny WebAssembly module that uses SIMD: without SIMD, Tesseract runs 4-5 times slower.
const SIMD_TEST = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]);

export function supported() {
  try {
    return typeof Worker !== "undefined" && typeof createImageBitmap === "function" && WebAssembly.validate(SIMD_TEST);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- Tesseract workers

class Pool {
  constructor() {
    this.idle = [];
    this.waiting = [];
    this.count = 0;
  }

  async add() {
    const worker = await Tesseract.createWorker("eng", 1, {
      workerPath: `${VENDOR}worker.min.js`,
      corePath: `${VENDOR}tesseract-core-simd-lstm.wasm.js`,
      langPath: VENDOR.replace(/\/$/, ""),
      workerBlobURL: false,
      gzip: true,
    });
    worker.psm = null;
    this.count++;
    this.release(worker);
  }

  release(worker) {
    const next = this.waiting.shift();
    if (next) next(worker);
    else this.idle.push(worker);
  }

  acquire(psm) {
    const i = this.idle.findIndex((w) => w.psm === psm);
    if (i >= 0) return Promise.resolve(this.idle.splice(i, 1)[0]);
    if (this.idle.length) return Promise.resolve(this.idle.shift());
    return new Promise((resolve) => this.waiting.push(resolve));
  }

  /** Read one gray picture; returns [tesseract.js blocks, ms spent waiting for a free worker]. */
  async read(img, psm) {
    const asked = performance.now();
    const worker = await this.acquire(psm);
    const waited = performance.now() - asked;
    try {
      if (worker.psm !== psm) {
        await worker.setParameters({ tessedit_pageseg_mode: psm });
        worker.psm = psm;
      }
      const header = new TextEncoder().encode(`P5\n${img.width} ${img.height}\n255\n`);
      const pgm = new Uint8Array(header.length + img.data.length);
      pgm.set(header);
      pgm.set(img.data, header.length);
      const { data } = await worker.recognize(pgm, {}, { blocks: true, text: false });
      return [data.blocks || [], waited];
    } finally {
      this.release(worker);
    }
  }
}

let pool = null;
let starting = null;

/** Start loading the scanner (about 6 MB the first time; the browser keeps it). */
export function warmUp() {
  if (!starting) {
    pool = new Pool();
    starting = pool.add().then(() => {
      for (let i = 1; i < workerCount(); i++) pool.add().catch(() => {}); // more workers for batches; not waited for
    });
    starting.catch(() => { starting = null; pool = null; });
  }
  return starting;
}

export const isReady = () => Boolean(pool && pool.count > 0);

/** How many labels the browser reads at once: one Tesseract worker each, up to 3, leaving a core for the page. */
export const workerCount = () => Math.max(1, Math.min(3, (navigator.hardwareConcurrency || 2) - 1));

// ---------------------------------------------------------------- one pass

/** Run one OCR pass on a view; returns [evidence, skew in degrees, ms spent waiting for a worker]. */
async function readText(view, name, imageIndex, psm = SPARSE) {
  const [blocks, waited] = await pool.read(view.image, psm);
  const tokens = [];
  const words = [];
  const lines = [];
  for (const block of blocks) {
    for (const paragraph of block.paragraphs || []) {
      for (const line of paragraph.lines || []) {
        const lineWords = [];
        for (const word of line.words || []) {
          const text = word.text.trim();
          if (!text) continue;
          const { x0, y0, x1, y1 } = word.bbox;
          const w = { text, left: x0, top: y0, width: x1 - x0, height: y1 - y0 };
          tokens.push({ text, conf: word.confidence, box: view.boxOnOriginal(x0, y0, x1 - x0, y1 - y0, imageIndex), px: [x0, y0, x1 - x0, y1 - y0] });
          words.push(w);
          lineWords.push(w);
        }
        lines.push(lineWords);
      }
    }
  }
  const heading = headingWeight(view, words);
  const evidence = {
    source: "ocr", name, tokens, imageIndex, view, fields: {}, warningText: null,
    headerBold: heading.bold, headerBoldNote: heading.note, headingRatio: heading.ratio, headingTooSmall: heading.tooSmall,
  };
  return [evidence, skewDegrees(lines, view.image.width), waited];
}

const wordCount = (ev) => ev.tokens.filter((t) => /[\p{L}\p{N}]/u.test(t.text)).length;
const clearWords = (ev) => ev.tokens.filter((t) => t.conf >= 60 && (t.text.match(/[\p{L}\p{N}]/gu) || []).length >= 3).length;

// ---------------------------------------------------------------- one picture

async function decodePicture(file) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    bitmap = await createImageBitmap(file); // older browsers; a format the browser cannot open throws here
  }
  const { width, height } = bitmap;
  const canvas = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(width, height) : Object.assign(document.createElement("canvas"), { width, height });
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff"; // transparent areas read as white paper
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close?.();
  return { rgba: ctx.getImageData(0, 0, width, height).data, width, height };
}

/** The OCR passes for one label picture (pipeline.ImageReader). */
class PictureReader {
  constructor(decoded, index) {
    this.index = index;
    this.decoded = decoded;
    this.gray = grayFromRGBA(decoded.rgba, decoded.width, decoded.height);
    this.view = View.of(this.gray).fitForOcr();
    this.plan = [...PLAN];
    this.urgent = [];
    this.evidences = [];
    this.rotated = false;
    this.deskewed = false;
    this.fullPassMs = 0; // the longest pass so far, not counting the wait for a free worker
    this.waitedMs = 0;
    this.log = []; // [pass, ms] for diagnostics
    this.reread = new Set();
  }

  get hasMore() { return this.urgent.length > 0 || this.plan.length > 0; }

  get nextCost() {
    const step = (this.urgent.length ? this.urgent : this.plan)[0];
    return this.fullPassMs * (step instanceof View ? 0.35 : 1.0);
  }

  /** The darkest colour channel, with the current view's geometry (the glare passes share it). */
  darkest() {
    if (this.darkestView?.matrix !== this.view.matrix) {
      this.darkestChannel ??= darkestFromRGBA(this.decoded.rgba, this.decoded.width, this.decoded.height);
      this.darkestView = this.view.reproject(this.darkestChannel);
    }
    return this.darkestView;
  }

  async runNext() {
    this.waitedMs = 0;
    await this.step();
  }

  async read(view, name, psm = SPARSE) {
    const [evidence, skew, waited] = await readText(view, name, this.index, psm);
    this.waitedMs += waited;
    return [evidence, skew];
  }

  async step() {
    const started = performance.now();
    const step = this.urgent.length ? this.urgent.shift() : this.plan.shift();
    if (step instanceof View) { // a cropped warning paragraph
      const [evidence] = await this.read(step, "ocr:warning", BLOCK);
      this.evidences.push(evidence);
      this.log.push(["warning", Math.round(performance.now() - started)]);
      return;
    }
    let view;
    if (step === "standard" || step === "layout") view = this.view;
    else if (step.startsWith("rotate-")) view = this.view.rotate(Number(step.split("-")[1]));
    else if (step === "enhanced-gray") view = this.view.map(enhance);
    else view = this.darkest().map(step === "enhanced" ? enhance : binarize);
    const [evidence, skew] = await this.read(view, `ocr:${step}`, step === "layout" ? LAYOUT : SPARSE);
    this.evidences.push(evidence);
    this.fullPassMs = Math.max(this.fullPassMs, performance.now() - started - this.waitedMs);
    this.log.push([step, Math.round(performance.now() - started)]);
    if (step === "standard") await this.straighten(evidence, skew);
  }

  /** Turn a sideways picture upright, or queue a pass on a straightened view. */
  async straighten(evidence, skew) {
    if (!this.rotated && wordCount(evidence) < MIN_UPRIGHT_WORDS) {
      this.rotated = true;
      // The server asks Tesseract which way the text runs. Here, read both quarter turns and keep the clearer one.
      const started = performance.now();
      const turned = await Promise.all([90, 270].map(async (degrees) => {
        const view = this.view.rotate(degrees);
        const [ev, s] = await this.read(view, "ocr:standard");
        return { view, ev, s };
      }));
      this.log.push(["quarter turns", Math.round(performance.now() - started)]);
      const best = turned.reduce((a, b) => (clearWords(b.ev) > clearWords(a.ev) ? b : a));
      if (clearWords(best.ev) > Math.max(clearWords(evidence), 3)) {
        this.view = best.view;
        this.evidences.push(best.ev);
        return this.straighten(best.ev, best.s);
      }
    }
    if (!this.deskewed && Math.abs(skew) > MIN_SKEW_DEGREES) {
      this.deskewed = true;
      this.view = this.view.rotate(-skew);
      // A tilted picture is a photo: run the glare/contrast pass next, on the straightened view.
      this.plan = this.plan.filter((s) => s !== "enhanced");
      this.urgent.push("enhanced");
    }
    return undefined;
  }

  /** Crop the best reading of the warning paragraph and queue it to be read on its own. */
  queueWarningReread() {
    const readings = this.evidences
      .filter((e) => e.name !== "ocr:warning" && e.view)
      .map(readWarning)
      .filter((r) => r.located && !r.exact && !this.reread.has(r.evidence));
    if (!readings.length) return;
    const reading = readings.reduce((a, b) => (b.score > a.score ? b : a));
    this.reread.add(reading.evidence);
    // Long words only: "the", "of" and "a" also turn up in text next to the warning.
    const boxes = reading.tokens.filter((t) => t.px && loose(t.text).length >= 5 && VOCABULARY.has(loose(t.text))).map((t) => t.px);
    if (boxes.length < 4) return;
    const heights = boxes.map(([, , , h]) => h).sort((a, b) => a - b);
    const n = heights.length;
    const line = n % 2 ? heights[(n - 1) / 2] : (heights[n / 2 - 1] + heights[n / 2]) / 2;
    const left = Math.min(...boxes.map(([x]) => x)) - line;
    const top = Math.min(...boxes.map(([, y]) => y)) - 1.5 * line;
    const right = Math.max(...boxes.map(([x, , w]) => x + w)) + line;
    const bottom = Math.max(...boxes.map(([, y, , h]) => y + h)) + 1.5 * line;
    const scale = Math.min(4.0, Math.max(1.0, WARNING_LINE_PX / Math.max(line, 1)));
    // The same geometry, without that pass's filter.
    this.plan.unshift(reading.evidence.view.cropFrom(this.gray, Math.trunc(left), Math.trunc(top), Math.trunc(right), Math.trunc(bottom), scale));
  }
}

// ---------------------------------------------------------------- one application

const allPass = (checks) => Boolean(checks) && checks.every((c) => c.status === PASS);

export class Session {
  constructor(files) {
    this.files = files;
    this.started = performance.now();
    this.readers = null;
    this.notices = [];
    this.stopped = false;
  }

  get evidences() { return this.readers.flatMap((r) => r.evidences); }

  /** Start no more passes: the server answered first. The pass in progress completes. */
  stop() { this.stopped = true; }

  /** Decode the pictures and read each one once (turned upright first when it is sideways). */
  async firstRound() {
    await warmUp();
    const decoded = await Promise.all(this.files.map(decodePicture));
    this.readers = decoded.map((d, i) => new PictureReader(d, i));
    this.readers.forEach((r, i) => {
      const prefix = this.readers.length > 1 ? `Image ${i + 1}: ` : "";
      this.notices.push(...qualityNotices(r.gray).map((n) => prefix + n));
    });
    this.decodeMs = performance.now() - this.started;
    await Promise.all(this.readers.map((r) => r.runNext()));
    this.ocrMs = Math.round(performance.now() - this.started);
  }

  /** The first round, in the shape POST /api/verify-readings takes (no pictures). */
  readings() {
    return {
      ocr_ms: this.ocrMs,
      readings: this.evidences.map((ev) => ({
        image: ev.imageIndex,
        words: ev.tokens.slice(0, 5000).map((t) => ({
          text: t.text.slice(0, 200), conf: Math.max(0, Math.min(100, t.conf)), x: t.box.x, y: t.box.y, w: t.box.w, h: t.box.h,
        })),
        heading_weight: ev.headingRatio === null ? null : Math.min(20, ev.headingRatio),
        heading_too_small: ev.headingTooSmall,
      })),
    };
  }

  /** Finish the check in the browser: more passes while time allows, the rules from rules.js.
   *  Like the server, no new round starts after 3.5 s from the start of the check.
   *  `budgetMs: Infinity` (batches, where nobody waits for one label) runs every pass a label needs. */
  async finish(rawApplication, { budgetMs = OCR_BUDGET_MS } = {}) {
    const application = normalizeApplication(rawApplication);
    const deadline = this.started + budgetMs;
    let rulesMs = 0; // the rules, and preparing the warning re-reads: page work between the passes
    const timed = (fn) => {
      const started = performance.now();
      const value = fn();
      rulesMs += performance.now() - started;
      return value;
    };
    const check = () => timed(() => verify(application, this.evidences));
    let checks = check();
    while (!allPass(checks) && !this.stopped) {
      if (checks.some((c) => c.field.startsWith("warning") && c.status !== PASS)) timed(() => this.readers.forEach((r) => r.queueWarningReread()));
      const pending = this.readers.filter((r) => r.hasMore);
      if (!pending.length) break;
      const overBudget = performance.now() + Math.max(...pending.map((r) => r.nextCost)) > deadline;
      if (overBudget && !pending.some((r) => r.urgent.length)) break;
      await Promise.all(pending.map((r) => r.runNext()));
      checks = check();
    }
    const [status, headline] = overall(checks);
    const elapsed = Math.round(performance.now() - this.started);
    return {
      overall: status,
      headline,
      checks,
      notices: this.notices,
      engines: [...new Set(this.evidences.map((e) => e.name))].sort(),
      elapsed_ms: elapsed,
      timings_ms: {
        total: elapsed, decode: Math.round(this.decodeMs), rules: Math.round(rulesMs),
        passes: this.readers.map((r) => r.log),
      },
    };
  }
}
