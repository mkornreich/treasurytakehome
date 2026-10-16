// Text normalization and fuzzy phrase search: a port of app/verification/text.py.

import { ratio } from "./fuzz.js";

export const US_STATES = {
  al: "alabama", ak: "alaska", az: "arizona", ar: "arkansas", ca: "california",
  co: "colorado", ct: "connecticut", de: "delaware", dc: "district of columbia",
  fl: "florida", ga: "georgia", hi: "hawaii", id: "idaho", il: "illinois",
  in: "indiana", ia: "iowa", ks: "kansas", ky: "kentucky", la: "louisiana",
  me: "maine", md: "maryland", ma: "massachusetts", mi: "michigan", mn: "minnesota",
  ms: "mississippi", mo: "missouri", mt: "montana", ne: "nebraska", nv: "nevada",
  nh: "new hampshire", nj: "new jersey", nm: "new mexico", ny: "new york",
  nc: "north carolina", nd: "north dakota", oh: "ohio", ok: "oklahoma", or: "oregon",
  pa: "pennsylvania", ri: "rhode island", sc: "south carolina", sd: "south dakota",
  tn: "tennessee", tx: "texas", ut: "utah", vt: "vermont", va: "virginia",
  wa: "washington", wv: "west virginia", wi: "wisconsin", wy: "wyoming",
};
const STATE_NAMES = new Set(Object.values(US_STATES));
// Two-letter codes that are also common English words are only expanded after a comma.
const AMBIGUOUS_STATES = new Set(["in", "me", "or", "oh", "ok", "hi", "pa", "la", "ma", "de", "id"]);

const PUNCT = {
  "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
  "“": '"', "”": '"', "„": '"', "″": '"',
  "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
  " ": " ", "­": "",
};
const PUNCT_RE = new RegExp(`[${Object.keys(PUNCT).join("")}]`, "g");

/** Python len(): code points, not UTF-16 units. */
export const cpLen = (s) => Array.from(s).length;

/** Unicode-normalize quotes/dashes and collapse whitespace. Case is preserved. */
export function clean(text) {
  return text.normalize("NFKC").replace(PUNCT_RE, (c) => PUNCT[c]).replace(/\s+/g, " ").trim();
}

export const casefold = (text) => text.toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");

/** Comparison form that ignores case, punctuation and spacing: "STONE'S THROW" -> "stones throw". */
export function loose(text) {
  let out = casefold(clean(text)).replaceAll("&", " and ").replaceAll("'", "");
  out = out.replace(/[^\p{L}\p{N}_%]+|_/gu, " ");
  return out.replace(/\s+/g, " ").trim();
}

const STATE_RE = new RegExp(`(,\\s*)?\\b(${Object.keys(US_STATES).join("|")})\\b\\.?`, "gi");

/** Spell out US state abbreviations so "Bardstown, KY" matches "Bardstown, Kentucky". */
export function expandStates(text) {
  return clean(text).replace(STATE_RE, (whole, comma, code) => {
    code = code.toLowerCase();
    if (AMBIGUOUS_STATES.has(code) && !comma) return whole;
    return `${comma || ""}${US_STATES[code]}`;
  });
}

export const isState = (key) => key in US_STATES || STATE_NAMES.has(key);

export const similarity = (a, b) => ratio(loose(a), loose(b));

const isSpeck = (word) => {
  const n = cpLen(word);
  return n === 1 || (n === 2 && !/^\p{L}+$/u.test(word));
};

function windowScore(words, target, targetWords) {
  const joined = words.join(" ");
  let score = ratio(joined, target);
  if (score < 100) score = Math.max(score, ratio(joined.replaceAll(" ", ""), target.replaceAll(" ", "")));
  if (score < 100) {
    const kept = words.filter((w) => targetWords.has(w) || !isSpeck(w));
    if (kept.length > 0 && kept.length < words.length) score = Math.max(score, ratio(kept.join(" "), target));
  }
  return score;
}

/** Lexicographic comparison of two arrays of numbers/booleans, like Python tuples. */
export function compareKeys(a, b) {
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const x = Number(a[i]);
    const y = Number(b[i]);
    if (x !== y) return x < y ? -1 : 1;
  }
  return a.length - b.length;
}

export const splitWords = (text) => text.split(/\s+/).filter(Boolean);

/** Find the run of tokens that best matches `expected`, tolerating OCR noise. */
export function findPhrase(expected, tokens, transform = null) {
  const prepare = transform ? (s) => loose(transform(s)) : loose;
  const target = prepare(expected);
  if (!target) return null;
  const targetWords = new Set(target.split(" "));
  const nWords = splitWords(expected).length;
  const idx = [];
  tokens.forEach((t, i) => { if (loose(t.text)) idx.push(i); });
  if (!idx.length) return null;

  let best = null;
  let bestKey = [-1.0];
  for (let size = Math.max(1, nWords - 1); size < nWords + 3; size++) {
    for (let k = 0; k <= idx.length - size; k++) {
      const window = idx.slice(k, k + size).map((i) => tokens[i]);
      const raw = window.map((t) => t.text).join(" ").replace(/^[ ,.;:]+|[ ,.;:]+$/g, "");
      const prepared = prepare(raw);
      const score = windowScore(splitWords(prepared), target, targetWords);
      if (score < bestKey[0]) continue;
      const heights = window.filter((t) => t.box).map((t) => t.box.h);
      const height = heights.length ? heights.reduce((a, b) => a + b, 0) / heights.length : 0.0;
      // Ties: same word count, then exact capitalization, then the most prominent text.
      const key = [score, -Math.abs(size - nWords), clean(raw) === clean(expected), height];
      if (compareKeys(key, bestKey) > 0) {
        bestKey = key;
        best = { score, text: raw, start: idx[k], end: idx[k + size - 1] + 1, height };
      }
    }
  }
  return best;
}

/** Python's format(x, "g"): 6 significant digits, no trailing zeros. */
export function fmtG(x) {
  if (x === 0) return Object.is(x, -0) ? "-0" : "0";
  const [mantissa, exp] = x.toExponential(5).split("e");
  const e = Number(exp);
  const strip = (s) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);
  if (e < -4 || e >= 6) return `${strip(mantissa)}e${e < 0 ? "-" : "+"}${String(Math.abs(e)).padStart(2, "0")}`;
  return strip(x.toFixed(Math.max(0, 5 - e)));
}

/** Python's round(x, digits): halves go to the even neighbour. */
export function pyRound(x, digits = 0) {
  const f = 10 ** digits;
  const y = x * f;
  const r = Math.round(y);
  return (Math.abs(y % 1) === 0.5 ? 2 * Math.round(y / 2) : r) / f;
}

/** Median, as statistics.median. */
export function median(values) {
  const v = [...values].sort((a, b) => a - b);
  const n = v.length;
  return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}
