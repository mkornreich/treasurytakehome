// Picture clean-up, geometry and measurements for the text scanner in the browser. Ports of
// app/imaging.py (View, enhance, binarize, quality_notices), app/extraction/typography.py (is the
// warning heading bold?) and the line-slope skew estimate in app/extraction/ocr.py.
// A picture here is {data: Uint8Array, width, height}: one byte of gray per pixel.
// No DOM is used, so Node can load this file too.

import { loose, median } from "./text.js";

export const OCR_MAX_SIDE = 2400; // larger pictures slow Tesseract without improving accuracy
export const OCR_MIN_SIDE = 1400; // small pictures are enlarged so fine print is legible

export const picture = (width, height, data = new Uint8Array(width * height)) => ({ data, width, height });
const clamp8 = (v) => (v <= 0 ? 0 : v >= 255 ? 255 : Math.round(v));

/** RGBA pixels -> gray, with OpenCV's fixed-point RGB2GRAY weights. */
export function grayFromRGBA(rgba, width, height) {
  const out = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) out[i] = (rgba[p] * 4899 + rgba[p + 1] * 9617 + rgba[p + 2] * 1868 + 8192) >> 14;
  return picture(width, height, out);
}

/** Per-pixel minimum of R, G, B: coloured ink washed out by glare stays dark in one channel. */
export function darkestFromRGBA(rgba, width, height) {
  const out = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) out[i] = Math.min(rgba[p], rgba[p + 1], rgba[p + 2]);
  return picture(width, height, out);
}

/** numpy.median of 8-bit values, via a histogram. */
function median8(values) {
  const hist = new Uint32Array(256);
  for (const v of values) hist[v]++;
  const n = values.length;
  if (!n) return 255;
  const nth = (k) => {
    let seen = 0;
    for (let v = 0; v < 256; v++) {
      seen += hist[v];
      if (seen > k) return v;
    }
    return 255;
  };
  return n % 2 ? nth((n - 1) / 2) : (nth(n / 2 - 1) + nth(n / 2)) / 2;
}

// ---------------------------------------------------------------- resampling

function axisTaps(src, dst) {
  const scale = src / dst;
  const at = (i) => Math.min(src - 1, Math.max(0, i));
  const taps = [];
  for (let d = 0; d < dst; d++) {
    if (scale > 1) { // shrink: average the source pixels each output pixel covers (INTER_AREA)
      const start = d * scale;
      const end = start + scale;
      const row = [];
      for (let s = Math.floor(start); s < Math.ceil(end); s++) {
        const w = Math.min(end, s + 1) - Math.max(start, s);
        if (w > 1e-9) row.push([at(s), w / scale]);
      }
      taps.push(row);
    } else { // enlarge: bicubic, a = -0.75 (INTER_CUBIC)
      const x = (d + 0.5) * scale - 0.5;
      const i = Math.floor(x);
      const t = x - i;
      const A = -0.75;
      const w0 = ((A * (t + 1) - 5 * A) * (t + 1) + 8 * A) * (t + 1) - 4 * A;
      const w1 = ((A + 2) * t - (A + 3)) * t * t + 1;
      const w2 = ((A + 2) * (1 - t) - (A + 3)) * (1 - t) * (1 - t) + 1;
      taps.push([[at(i - 1), w0], [at(i), w1], [at(i + 1), w2], [at(i + 2), 1 - w0 - w1 - w2]]);
    }
  }
  return taps;
}

/** Resize to width x height: area averaging when shrinking, bicubic when enlarging. */
export function resize(img, width, height) {
  const across = axisTaps(img.width, width);
  const down = axisTaps(img.height, height);
  const tmp = new Float32Array(img.height * width);
  for (let y = 0; y < img.height; y++) {
    const row = y * img.width;
    for (let x = 0; x < width; x++) {
      let v = 0;
      for (const [s, w] of across[x]) v += img.data[row + s] * w;
      tmp[y * width + x] = v;
    }
  }
  const out = new Uint8Array(width * height);
  for (let y = 0; y < height; y++) {
    const taps = down[y];
    for (let x = 0; x < width; x++) {
      let v = 0;
      for (const [s, w] of taps) v += tmp[s * width + x] * w;
      out[y * width + x] = clamp8(v);
    }
  }
  return picture(width, height, out);
}

// ---------------------------------------------------------------- geometry

const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const multiply = (a, b) => [
  a[0] * b[0] + a[1] * b[3] + a[2] * b[6], a[0] * b[1] + a[1] * b[4] + a[2] * b[7], a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
  a[3] * b[0] + a[4] * b[3] + a[5] * b[6], a[3] * b[1] + a[4] * b[4] + a[5] * b[7], a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
  0, 0, 1,
];
function invertAffine(m) {
  const det = m[0] * m[4] - m[1] * m[3];
  const [a, b, c, d] = [m[4] / det, -m[1] / det, -m[3] / det, m[0] / det];
  return [a, b, -(a * m[2] + b * m[5]), c, d, -(c * m[2] + d * m[5]), 0, 0, 1];
}

const cubicWeights = (t, out) => { // Keys, a = -0.75, as OpenCV's INTER_CUBIC
  const A = -0.75;
  out[0] = ((A * (t + 1) - 5 * A) * (t + 1) + 8 * A) * (t + 1) - 4 * A;
  out[1] = ((A + 2) * t - (A + 3)) * t * t + 1;
  out[2] = ((A + 2) * (1 - t) - (A + 3)) * (1 - t) * (1 - t) + 1;
  out[3] = 1 - out[0] - out[1] - out[2];
};

/** Affine warp (forward matrix), bicubic as cv2.warpAffine(INTER_CUBIC); outside pixels repeat the edge or take `border`. */
function warp(img, matrix, width, height, border = null) {
  const inv = invertAffine(matrix);
  const out = new Uint8Array(width * height);
  const { data, width: w, height: h } = img;
  const wx = new Float64Array(4);
  const wy = new Float64Array(4);
  for (let v = 0; v < height; v++) {
    let x = inv[1] * v + inv[2];
    let y = inv[4] * v + inv[5];
    for (let u = 0; u < width; u++, x += inv[0], y += inv[3]) {
      const x0 = Math.floor(x);
      const y0 = Math.floor(y);
      if (border !== null && (x0 < -2 || y0 < -2 || x0 > w + 1 || y0 > h + 1)) {
        out[v * width + u] = border;
        continue;
      }
      cubicWeights(x - x0, wx);
      cubicWeights(y - y0, wy);
      let value = 0;
      if (x0 >= 1 && y0 >= 1 && x0 + 2 < w && y0 + 2 < h) { // all 16 neighbours inside: no edge rules needed
        let at = (y0 - 1) * w + x0 - 1;
        for (let j = 0; j < 4; j++, at += w) {
          value += (data[at] * wx[0] + data[at + 1] * wx[1] + data[at + 2] * wx[2] + data[at + 3] * wx[3]) * wy[j];
        }
        out[v * width + u] = clamp8(value);
        continue;
      }
      for (let j = 0; j < 4; j++) {
        const sy = y0 - 1 + j;
        const inY = sy >= 0 && sy < h;
        const row = Math.min(h - 1, Math.max(0, sy)) * w;
        let line = 0;
        for (let i = 0; i < 4; i++) {
          const sx = x0 - 1 + i;
          const inside = inY && sx >= 0 && sx < w;
          const p = border !== null && !inside ? border : data[row + Math.min(w - 1, Math.max(0, sx))];
          line += p * wx[i];
        }
        value += line * wy[j];
      }
      out[v * width + u] = clamp8(value);
    }
  }
  return picture(width, height, out);
}

/** Quarter turns clockwise, pixel for pixel. Returns [picture, matrix from old to new pixels]. */
function quarterTurn(img, degrees) {
  const { data, width: w, height: h } = img;
  const out = new Uint8Array(w * h);
  if (degrees === 180) {
    for (let i = 0; i < out.length; i++) out[i] = data[out.length - 1 - i];
    return [picture(w, h, out), [-1, 0, w - 1, 0, -1, h - 1, 0, 0, 1]];
  }
  // 90: (x, y) -> (h-1-y, x). 270: (x, y) -> (y, w-1-x). The new picture is h wide, w high.
  for (let v = 0; v < w; v++) {
    for (let u = 0; u < h; u++) out[v * h + u] = degrees === 90 ? data[(h - 1 - u) * w + v] : data[u * w + (w - 1 - v)];
  }
  return [picture(h, w, out), degrees === 90 ? [0, -1, h - 1, 1, 0, 0, 0, 0, 1] : [0, 1, 0, -1, 0, w - 1, 0, 0, 1]];
}

/** A processed copy of an upload, plus the transform back to it (as app/imaging.View). */
export class View {
  constructor(image, matrix, originalSize) {
    this.image = image;
    this.matrix = matrix; // original pixels -> this view's pixels
    this.originalSize = originalSize; // [width, height]
  }

  static of(img) { return new View(img, IDENTITY, [img.width, img.height]); }

  derive(image, step) { return new View(image, multiply(step, this.matrix), this.originalSize); }

  /** A pixel-wise filter that does not move anything. */
  map(fn) { return new View(fn(this.image), this.matrix, this.originalSize); }

  /** Another rendering of the original upload, with this view's geometry applied. */
  reproject(source) { return new View(warp(source, this.matrix, this.image.width, this.image.height), this.matrix, this.originalSize); }

  get enlargement() { return Math.sqrt(Math.abs(this.matrix[0] * this.matrix[4] - this.matrix[1] * this.matrix[3])); }

  fitForOcr() {
    const { width: w, height: h } = this.image;
    const long = Math.max(w, h);
    let scale;
    if (long > OCR_MAX_SIDE) scale = OCR_MAX_SIDE / long;
    else if (long < OCR_MIN_SIDE) scale = Math.min(OCR_MIN_SIDE / long, 3.0);
    else return this;
    const size = [Math.round(w * scale), Math.round(h * scale)];
    return this.derive(resize(this.image, size[0], size[1]), [size[0] / w, 0, 0, 0, size[1] / h, 0, 0, 0, 1]);
  }

  /** Rotate clockwise by `degrees`, growing the canvas so nothing is cropped. */
  rotate(degrees) {
    if (Math.abs(degrees) < 0.01) return this;
    const quarter = ((Math.round(degrees) % 360) + 360) % 360;
    if (Math.abs(degrees - Math.round(degrees)) < 1e-9 && [90, 180, 270].includes(quarter)) {
      const [img, step] = quarterTurn(this.image, quarter);
      return this.derive(img, step);
    }
    const { width: w, height: h } = this.image;
    const angle = (-degrees * Math.PI) / 180; // OpenCV: positive angles turn counter-clockwise
    const a = Math.cos(angle);
    const b = Math.sin(angle);
    const m = [a, b, (1 - a) * (w / 2) - b * (h / 2), -b, a, b * (w / 2) + (1 - a) * (h / 2), 0, 0, 1];
    const newW = Math.trunc(h * Math.abs(b) + w * Math.abs(a) + 0.5);
    const newH = Math.trunc(h * Math.abs(a) + w * Math.abs(b) + 0.5);
    m[2] += newW / 2 - w / 2;
    m[5] += newH / 2 - h / 2;
    const edge = new Uint8Array(2 * w);
    edge.set(this.image.data.subarray(0, w));
    edge.set(this.image.data.subarray((h - 1) * w, h * w), w);
    return this.derive(warp(this.image, m, newW, newH, Math.trunc(median8(edge))), m);
  }

  /** A region of this view, optionally enlarged, with a plain margin around it. */
  crop(left, top, right, bottom, scale = 1.0, pad = 12) {
    const { width: w, height: h } = this.image;
    left = Math.max(0, left); top = Math.max(0, top); right = Math.min(w, right); bottom = Math.min(h, bottom);
    let region = picture(Math.max(0, right - left), Math.max(0, bottom - top));
    for (let y = 0; y < region.height; y++) region.data.set(this.image.data.subarray((top + y) * w + left, (top + y) * w + right), y * region.width);
    if (scale !== 1.0 && region.width && region.height) region = resize(region, Math.round(region.width * scale), Math.round(region.height * scale));
    const border = region.data.length ? Math.trunc(median8(region.data)) : 255;
    const out = picture(region.width + 2 * pad, region.height + 2 * pad);
    out.data.fill(border);
    for (let y = 0; y < region.height; y++) out.data.set(region.data.subarray(y * region.width, (y + 1) * region.width), (y + pad) * out.width + pad);
    return this.derive(out, [scale, 0, pad - left * scale, 0, scale, pad - top * scale, 0, 0, 1]);
  }

  /** crop() of this view drawn straight from `source` (the original upload), with this view's
   *  geometry: one warp of the region only, instead of warping the whole picture first. */
  cropFrom(source, left, top, right, bottom, scale = 1.0, pad = 12) {
    const { width: w, height: h } = this.image;
    left = Math.max(0, left); top = Math.max(0, top); right = Math.min(w, right); bottom = Math.min(h, bottom);
    const step = [scale, 0, pad - left * scale, 0, scale, pad - top * scale, 0, 0, 1];
    const width = Math.max(0, Math.round((right - left) * scale));
    const height = Math.max(0, Math.round((bottom - top) * scale));
    const region = warp(source, multiply([scale, 0, -left * scale, 0, scale, -top * scale, 0, 0, 1], this.matrix), width, height);
    const border = region.data.length ? Math.trunc(median8(region.data)) : 255;
    const out = picture(width + 2 * pad, height + 2 * pad);
    out.data.fill(border);
    for (let y = 0; y < height; y++) out.data.set(region.data.subarray(y * width, (y + 1) * width), (y + pad) * out.width + pad);
    return this.derive(out, step);
  }

  /** A box in this view's pixels -> fractions of the original upload. */
  boxOnOriginal(left, top, width, height, image = 0) {
    const inv = invertAffine(this.matrix);
    const xs = [];
    const ys = [];
    for (const [x, y] of [[left, top], [left + width, top], [left, top + height], [left + width, top + height]]) {
      xs.push(inv[0] * x + inv[1] * y + inv[2]);
      ys.push(inv[3] * x + inv[4] * y + inv[5]);
    }
    const [ow, oh] = this.originalSize;
    const x0 = Math.max(0, Math.min(...xs) / ow);
    const x1 = Math.min(1, Math.max(...xs) / ow);
    const y0 = Math.max(0, Math.min(...ys) / oh);
    const y1 = Math.min(1, Math.max(...ys) / oh);
    return { image, x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) };
  }
}

// ---------------------------------------------------------------- filters

const reflect101 = (i, n) => {
  if (n === 1) return 0;
  while (i < 0 || i >= n) i = i < 0 ? -i : 2 * n - 2 - i;
  return i;
};

function convolveSeparable(img, kernel) {
  const { data, width: w, height: h } = img;
  const r = (kernel.length - 1) / 2;
  const tmp = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = 0;
      for (let k = -r; k <= r; k++) v += data[y * w + reflect101(x + k, w)] * kernel[k + r];
      tmp[y * w + x] = v;
    }
  }
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = 0;
      for (let k = -r; k <= r; k++) v += tmp[reflect101(y + k, h) * w + x] * kernel[k + r];
      out[y * w + x] = v;
    }
  }
  return out;
}

function gaussianKernel(sigma, size) {
  const r = (size - 1) / 2;
  const k = Array.from({ length: size }, (_, i) => Math.exp(-((i - r) ** 2) / (2 * sigma * sigma)));
  const total = k.reduce((a, b) => a + b, 0);
  return k.map((v) => v / total);
}

/** Contrast-limited adaptive histogram equalization, as cv2.createCLAHE(clipLimit, (8, 8)). */
export function clahe(img, clipLimit = 2.5, tiles = 8) {
  const { data, width: w, height: h } = img;
  const extW = w % tiles ? w + tiles - (w % tiles) : w;
  const extH = h % tiles ? h + tiles - (h % tiles) : h;
  const tw = extW / tiles;
  const th = extH / tiles;
  const area = tw * th;
  const limit = Math.max(1, Math.trunc((clipLimit * area) / 256));
  const luts = new Uint8Array(tiles * tiles * 256);
  const hist = new Int32Array(256);
  for (let ty = 0; ty < tiles; ty++) {
    for (let tx = 0; tx < tiles; tx++) {
      hist.fill(0);
      for (let y = ty * th; y < (ty + 1) * th; y++) {
        const row = reflect101(y, h) * w;
        for (let x = tx * tw; x < (tx + 1) * tw; x++) hist[data[row + reflect101(x, w)]]++;
      }
      let clipped = 0;
      for (let i = 0; i < 256; i++) if (hist[i] > limit) { clipped += hist[i] - limit; hist[i] = limit; }
      const batch = Math.trunc(clipped / 256);
      let residual = clipped - batch * 256;
      for (let i = 0; i < 256; i++) hist[i] += batch;
      if (residual) {
        const step = Math.max(Math.trunc(256 / residual), 1);
        for (let i = 0; i < 256 && residual > 0; i += step, residual--) hist[i]++;
      }
      const lut = (ty * tiles + tx) * 256;
      let sum = 0;
      for (let i = 0; i < 256; i++) { sum += hist[i]; luts[lut + i] = clamp8((sum * 255) / area); }
    }
  }
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const fy = y / th - 0.5;
    let ty1 = Math.floor(fy);
    const ya = fy - ty1;
    const ty2 = Math.min(ty1 + 1, tiles - 1);
    ty1 = Math.max(ty1, 0);
    for (let x = 0; x < w; x++) {
      const fx = x / tw - 0.5;
      let tx1 = Math.floor(fx);
      const xa = fx - tx1;
      const tx2 = Math.min(tx1 + 1, tiles - 1);
      tx1 = Math.max(tx1, 0);
      const v = data[y * w + x];
      const lut = (row, col) => luts[(row * tiles + col) * 256 + v];
      out[y * w + x] = clamp8((lut(ty1, tx1) * (1 - xa) + lut(ty1, tx2) * xa) * (1 - ya) + (lut(ty2, tx1) * (1 - xa) + lut(ty2, tx2) * xa) * ya);
    }
  }
  return picture(w, h, out);
}

/** Even out glare and shadows (local contrast equalization), then sharpen. */
export function enhance(img) {
  const flat = clahe(img);
  const blurred = convolveSeparable(flat, gaussianKernel(2, 13));
  const out = new Uint8Array(flat.data.length);
  for (let i = 0; i < out.length; i++) out[i] = clamp8(1.6 * flat.data[i] - 0.6 * blurred[i]);
  return picture(img.width, img.height, out);
}

/** Adaptive threshold for light that changes across a curved bottle (block 31, C 15). The server
 *  smooths with a bilateral filter first; that is too slow here, so this pass uses the plain picture. */
export function binarize(img) {
  const { data, width: w, height: h } = img;
  // Three box blurs of radius 5 approximate the Gaussian (sigma 5) that OpenCV uses for a 31-pixel block.
  let mean = Float32Array.from(data);
  const box = (src, r, horizontal) => {
    const out = new Float32Array(src.length);
    const n = horizontal ? w : h;
    const lines = horizontal ? h : w;
    for (let line = 0; line < lines; line++) {
      const at = (i) => (horizontal ? line * w + Math.min(n - 1, Math.max(0, i)) : Math.min(n - 1, Math.max(0, i)) * w + line);
      let acc = 0;
      for (let i = -r; i <= r; i++) acc += src[at(i)];
      for (let i = 0; i < n; i++) {
        out[at(i)] = acc / (2 * r + 1);
        acc += src[at(i + r + 1)] - src[at(i - r)];
      }
    }
    return out;
  };
  for (let pass = 0; pass < 3; pass++) mean = box(box(mean, 5, true), 5, false);
  const out = new Uint8Array(data.length);
  for (let i = 0; i < out.length; i++) out[i] = data[i] - Math.round(mean[i]) > -15 ? 255 : 0;
  return picture(w, h, out);
}

/** Plain-language warnings about the photo itself, shown next to the results. */
export function qualityNotices(img) {
  const notices = [];
  const { width: w, height: h } = img;
  if (Math.max(w, h) < 700) notices.push("The picture has a low resolution. The tool cannot always read small text in it.");
  let sample = img;
  if (Math.max(w, h) > 1200) {
    const scale = 1200 / Math.max(w, h);
    sample = resize(img, Math.round(w * scale), Math.round(h * scale));
  }
  const { data, width: sw, height: sh } = sample;
  let sum = 0;
  let sumSq = 0;
  for (let y = 0; y < sh; y++) {
    for (let x = 0; x < sw; x++) {
      const at = (dx, dy) => data[reflect101(y + dy, sh) * sw + reflect101(x + dx, sw)];
      const lap = at(-1, 0) + at(1, 0) + at(0, -1) + at(0, 1) - 4 * at(0, 0);
      sum += lap;
      sumSq += lap * lap;
    }
  }
  const n = sw * sh;
  if (sumSq / n - (sum / n) ** 2 < 40) notices.push("The picture is not sharp. A sharp picture gives better results.");
  let total = 0;
  let bright = 0;
  for (const v of data) { total += v; if (v >= 250) bright++; }
  const mean = total / n;
  if (mean < 50) notices.push("The picture is very dark.");
  if (bright / n > 0.12 && mean < 200) notices.push("Glare or reflections make a part of the label hard to read.");
  return notices;
}

// ---------------------------------------------------------------- is the heading bold?

const BOLD_RATIO = 1.2;
const REGULAR_RATIO = 1.08;
const MIN_LETTER_PX = 10;
const MIN_HEADING_PX = 14;
const REFERENCE_HEIGHT = 48;
export const TOO_SMALL = "The text is too small to measure the type weight of the heading.";
export const SMALL_IN_PICTURE = "The heading in this picture is too small to measure its type weight.";

/** cv2.threshold(..., THRESH_OTSU) on 8-bit values: the threshold. */
function otsu(data) {
  const hist = new Float64Array(256);
  for (const v of data) hist[v]++;
  const scale = 1 / data.length;
  let mu = 0;
  for (let i = 0; i < 256; i++) mu += i * hist[i] * scale;
  let q1 = 0;
  let mu1 = 0;
  let maxSigma = 0;
  let maxVal = 0;
  const eps = 1.1920928955078125e-7;
  for (let i = 0; i < 256; i++) {
    const p = hist[i] * scale;
    mu1 *= q1;
    q1 += p;
    const q2 = 1 - q1;
    if (Math.min(q1, q2) < eps || Math.max(q1, q2) > 1 - eps) continue;
    mu1 = (mu1 + i * p) / q1;
    const mu2 = (mu - q1 * mu1) / q2;
    const sigma = q1 * q2 * (mu1 - mu2) * (mu1 - mu2);
    if (sigma > maxSigma) { maxSigma = sigma; maxVal = i; }
  }
  return maxVal;
}

/** Exact Euclidean distance from each ink pixel to the nearest non-ink pixel inside the picture. */
function distanceToPaper(ink, w, h) {
  const INF = 1e20;
  const f = new Float64Array(w * h);
  for (let i = 0; i < f.length; i++) f[i] = ink[i] ? INF : 0;
  const line = (get, set, n) => { // Felzenszwalb & Huttenlocher, squared distances along one line
    const d = new Float64Array(n);
    const v = new Int32Array(n);
    const z = new Float64Array(n + 1);
    let k = 0;
    v[0] = 0; z[0] = -INF; z[1] = INF;
    for (let q = 1; q < n; q++) {
      let s;
      while (true) {
        s = ((get(q) + q * q) - (get(v[k]) + v[k] * v[k])) / (2 * q - 2 * v[k]);
        if (s <= z[k] && k > 0) k--;
        else break;
      }
      if (s <= z[k]) { v[0] = q; z[0] = -INF; z[1] = INF; k = 0; continue; }
      k++; v[k] = q; z[k] = s; z[k + 1] = INF;
    }
    k = 0;
    for (let q = 0; q < n; q++) {
      while (z[k + 1] < q) k++;
      d[q] = (q - v[k]) ** 2 + get(v[k]);
    }
    for (let q = 0; q < n; q++) set(q, d[q]);
  };
  for (let x = 0; x < w; x++) line((y) => f[y * w + x], (y, val) => { f[y * w + x] = val; }, h);
  for (let y = 0; y < h; y++) {
    const row = f.slice(y * w, (y + 1) * w);
    line((x) => row[x], (x, val) => { f[y * w + x] = val; }, w);
  }
  return f;
}

/** Main stroke width of a word, in pixels of the original picture (typography.stroke_width). */
export function strokeWidth(img, word, scale) {
  const left = Math.max(0, word.left);
  const top = Math.max(0, word.top);
  const right = Math.min(img.width, word.left + word.width);
  const bottom = Math.min(img.height, word.top + word.height);
  if (right <= left || bottom <= top || word.height < MIN_LETTER_PX) return null;
  let crop = picture(right - left, bottom - top);
  for (let y = 0; y < crop.height; y++) crop.data.set(img.data.subarray((top + y) * img.width + left, (top + y) * img.width + right), y * crop.width);
  crop = resize(crop, Math.max(1, Math.round(crop.width * scale)), Math.max(1, Math.round(crop.height * scale)));
  const t = otsu(crop.data);
  let dark = 0;
  for (const v of crop.data) if (v <= t) dark++;
  const inkIsDark = dark / crop.data.length < 0.5; // ink is the minority color
  const ink = new Uint8Array(crop.data.length);
  for (let i = 0; i < ink.length; i++) ink[i] = (crop.data[i] <= t) === inkIsDark ? 1 : 0;
  const dist = distanceToPaper(ink, crop.width, crop.height);
  const values = [];
  for (let i = 0; i < ink.length; i++) if (ink[i]) values.push(Math.sqrt(dist[i]));
  if (values.length < 20) return null;
  values.sort((a, b) => a - b);
  const pos = 0.8 * (values.length - 1); // numpy.percentile(values, 80), linear
  const lo = Math.floor(pos);
  const p80 = values[lo] + (values[Math.min(lo + 1, values.length - 1)] - values[lo]) * (pos - lo);
  if (!Number.isFinite(p80) || p80 > 1e9) return null;
  return (2.0 * p80) / scale;
}

/** Heading stroke width / body stroke width, or null when the letters are too small (typography.header_boldness). */
export function headingRatio(img, header, body) {
  const heights = header.map((h) => h.height).filter((h) => h >= MIN_LETTER_PX);
  if (!heights.length) return null;
  const scale = Math.min(6.0, REFERENCE_HEIGHT / median(heights));
  const headerWidths = header.map((h) => strokeWidth(img, h, scale)).filter(Boolean);
  const bodyWords = body.filter((b) => (b.text.match(/\p{L}/gu) || []).length >= 3);
  const bodyWidths = bodyWords.map((b) => strokeWidth(img, b, scale)).filter(Boolean);
  if (!headerWidths.length || bodyWidths.length < 3) return null;
  return median(headerWidths) / median(bodyWidths);
}

/** Bold, regular, or could not tell (typography.judge_weight). */
export function judgeWeight(ratio) {
  if (ratio >= BOLD_RATIO) return [true, `The heading letters are ${ratio.toFixed(2)} times as thick as the warning text.`];
  if (ratio <= REGULAR_RATIO) return [false, `The heading letters and the warning text are almost the same thickness (${ratio.toFixed(2)} times).`];
  return [null, `The heading letters are only a little thicker than the warning text (${ratio.toFixed(2)} times).`];
}

/** Find "GOVERNMENT WARNING" among the words of one pass and measure its weight (ocr._header_weight).
 *  Returns {bold, note, ratio, tooSmall}: tooSmall is "" | "picture" | "text". */
export function headingWeight(view, words) {
  for (let i = 0; i < words.length - 1; i++) {
    if (loose(words[i].text) === "government" && loose(words[i + 1].text).startsWith("warning")) {
      // Judge print size on the upload itself: an enlarged crop only looks big.
      if (words[i].height / Math.max(view.enlargement, 1e-6) < MIN_HEADING_PX) {
        return { bold: null, note: SMALL_IN_PICTURE, ratio: null, tooSmall: "picture" };
      }
      const ratio = headingRatio(view.image, words.slice(i, i + 2), words.slice(i + 2, i + 40));
      if (ratio === null) return { bold: null, note: TOO_SMALL, ratio: null, tooSmall: "text" };
      const [bold, note] = judgeWeight(ratio);
      return { bold, note, ratio, tooSmall: "" };
    }
  }
  return { bold: null, note: "", ratio: null, tooSmall: "" };
}

/** Median slope of text lines with several words; positive = text runs downhill (ocr._skew_degrees). */
export function skewDegrees(lines, imageWidth) {
  const angles = [];
  for (const words of lines) {
    if (words.length < 3) continue;
    const xs = words.map((w) => w.left + w.width / 2);
    const ys = words.map((w) => w.top + w.height / 2);
    const span = Math.max(...xs) - Math.min(...xs);
    if (span < imageWidth * 0.15) continue;
    const mx = xs.reduce((a, b) => a + b, 0) / xs.length;
    const my = ys.reduce((a, b) => a + b, 0) / ys.length;
    let num = 0;
    let den = 0;
    xs.forEach((x, i) => { num += (x - mx) * (ys[i] - my); den += (x - mx) ** 2; });
    angles.push([(Math.atan(num / den) * 180) / Math.PI, span]);
  }
  if (!angles.length) return 0.0;
  angles.sort((a, b) => a[0] - b[0]);
  const total = angles.reduce((a, [, w]) => a + w, 0);
  let cumulative = 0;
  for (const [angle, weight] of angles) {
    cumulative += weight;
    if (cumulative >= total / 2) return angle;
  }
  return angles[angles.length - 1][0];
}
