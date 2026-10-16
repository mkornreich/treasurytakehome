// Government Health Warning Statement check (27 CFR 16.21 / 16.22): a port of app/verification/warning.py.

import { opcodes, partialRatioAlignment, ratio } from "./fuzz.js";
import { clean, cpLen, loose } from "./text.js";

export const CANONICAL_WARNING =
  "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink " +
  "alcoholic beverages during pregnancy because of the risk of birth defects. " +
  "(2) Consumption of alcoholic beverages impairs your ability to drive a car or " +
  "operate machinery, and may cause health problems.";
const CANON_WORDS = CANONICAL_WARNING.split(" ");
const CANON_KEYS = CANON_WORDS.map(loose);
const BODY_WORDS = CANON_WORDS.slice(2);
const BODY_KEYS = CANON_KEYS.slice(2);
export const VOCABULARY = new Set(CANON_KEYS);

const MINOR_WORD_SIMILARITY = 75; // "rnachinery" vs "machinery" -> probably a misread, not a rewrite
const LOW_CONFIDENCE = 75;
const NUMERALS = new Set(["1", "2"]); // "(1)" and "(2)"
const CONFUSABLE = { 0: "o", 1: "l", i: "l", t: "l", "|": "l", "!": "l", 5: "s", $: "s" };

const confusable = (text) =>
  text.replace(/[01it|!5$]/g, (c) => CONFUSABLE[c]).replaceAll("rn", "m").replaceAll("vv", "w");
const subsetOf = (items, set) => items.every((x) => set.has(x));
const isUpper = (s) => s.toUpperCase() === s && s.toLowerCase() !== s;

export class WarningReading {
  constructor(evidence, located, fields = {}) {
    Object.assign(this, {
      evidence, located, score: 0.0, tokens: [], headerTokens: [], headerFound: "",
      headerCaps: false, headerColon: false, major: [], minor: [], marks: new Set(), diff: [],
    }, fields);
  }

  get text() { return this.tokens.map((t) => t.text).join(" "); }
  get boxes() { return this.tokens.filter((t) => t.box).map((t) => t.box); }
  get headerBoxes() { return this.headerTokens.filter((t) => t.box).map((t) => t.box); }
  get wordingOk() { return this.located && !this.major.length; }
  get exact() { return this.wordingOk && !this.minor.length; }
}

/** The token range that best aligns with the given (normalized) words. */
function locate(tokens, needleKeys) {
  const keys = tokens.map((t) => loose(t.text));
  const starts = [];
  let pos = 0;
  for (const key of keys) {
    starts.push(pos);
    pos += cpLen(key) + 1;
  }
  const haystack = keys.join(" ");
  const needle = needleKeys.join(" ");
  if (!haystack.trim()) return [0, 0, 0.0];
  if (cpLen(haystack) <= cpLen(needle)) return [0, tokens.length, ratio(haystack, needle)];
  const a = partialRatioAlignment(needle, haystack);
  let begin = 0;
  let end = 0;
  starts.forEach((s, i) => {
    if (s <= a.destStart) begin = i;
    if (s < a.destEnd) end = i;
  });
  return [begin, end + 1, a.score];
}

/** Index of the "GOVERNMENT" of a "GOVERNMENT WARNING" pair, closest to token `near`. */
function findHeader(tokens, near) {
  let best = null;
  for (let i = 0; i < tokens.length - 1; i++) {
    if (ratio(loose(tokens[i].text), "government") >= 80 && loose(tokens[i + 1].text).startsWith("warn")) {
      if (best === null || Math.abs(i - near) < Math.abs(best - near)) best = i;
    }
  }
  return best;
}

const isNoise = (words) => words.every((w) => loose(w).replace(/[^a-z]/g, "").length < 3);

function misread(expectedKeys, seenKeys, confs) {
  if (expectedKeys.length && subsetOf(expectedKeys, NUMERALS)) return true;
  if (confusable(expectedKeys.join("")) === confusable(seenKeys.join(""))) return true;
  return confs.length > 0 && confs.reduce((a, b) => a + b, 0) / confs.length < LOW_CONFIDENCE;
}

const stripPunct = (s) => s.replace(/^[.,:;]+|[.,:;]+$/g, "");

function compare(found, confs) {
  confs = confs && confs.length ? confs : found.map(() => 100.0);
  const foundKeys = found.map(loose);
  const major = [];
  const minor = [];
  const marks = new Set();
  const diff = [];
  for (const [op, a0, a1, b0, b1] of opcodes(BODY_KEYS, foundKeys)) {
    const expected = BODY_WORDS.slice(a0, a1);
    const seen = found.slice(b0, b1);
    const joinedExpected = BODY_KEYS.slice(a0, a1).join("").replaceAll(" ", "");
    const joinedSeen = foundKeys.slice(b0, b1).join("").replaceAll(" ", "");
    if (op === "equal" || (op === "replace" && joinedExpected === joinedSeen)) {
      diff.push({ op: "equal", text: expected.join(" ") });
      if (op === "equal") {
        expected.forEach((want, k) => {
          const got = seen[k];
          if (stripPunct(clean(want)).toLowerCase() === stripPunct(clean(got)).toLowerCase()
              && clean(want).toLowerCase() !== clean(got).toLowerCase()) {
            minor.push(`punctuation “${want}” read as “${got}”`);
          }
        });
      }
    } else if (op === "delete") {
      diff.push({ op: "missing", text: expected.join(" ") });
      const keys = BODY_KEYS.slice(a0, a1);
      if (subsetOf(keys, NUMERALS) || (a1 - a0 === 1 && cpLen(BODY_KEYS[a0]) <= 3)) {
        minor.push(`“${expected.join(" ")}” not read`); // "(1)", or a short word such as "or"
      } else {
        major.push(`missing: “${expected.join(" ")}”`);
        for (let i = a0; i < a1; i++) marks.add(`w${i}`);
      }
    } else if (op === "insert") {
      const text = seen.join(" ");
      diff.push({ op: "extra", text });
      if (isNoise(seen) || misread([], foundKeys.slice(b0, b1), confs.slice(b0, b1))) {
        minor.push(`extra marks “${text}”`);
      } else {
        major.push(`extra text: “${text}”`);
        marks.add(`i${a0}`);
      }
    } else {
      diff.push({ op: "changed", expected: expected.join(" "), found: seen.join(" ") });
      const similar = ratio(joinedExpected, joinedSeen);
      const message = `“${expected.join(" ")}” read as “${seen.join(" ")}”`;
      const close = similar >= MINOR_WORD_SIMILARITY && Math.abs((a1 - a0) - (b1 - b0)) <= 1;
      if (close || misread(BODY_KEYS.slice(a0, a1), foundKeys.slice(b0, b1), confs.slice(b0, b1))) {
        minor.push(message);
      } else {
        major.push(message);
        for (let i = a0; i < a1; i++) marks.add(`w${i}`);
      }
    }
  }
  return [major, minor, marks, diff];
}

const tokensFromText = (text) => text.split(/\s+/).filter(Boolean).map((part) => ({ text: part, box: null, conf: 100.0 }));

// A reading never changes once made, and the rules and the warning re-read both ask about it
// every round: remember the answer for each reading.
const cache = new WeakMap();

export function readWarning(evidence) {
  if (!cache.has(evidence)) cache.set(evidence, locateWarning(evidence));
  return cache.get(evidence);
}

function locateWarning(evidence) {
  let tokens = evidence.tokens;
  if (evidence.warningText) tokens = tokensFromText(evidence.warningText);
  if (!tokens.length) return new WarningReading(evidence, false);

  // Find the body first, then the heading nearest to it.
  let [begin, end, score] = locate(tokens, BODY_KEYS);
  if (score < 60) return new WarningReading(evidence, false, { score });
  const isWord = (i, keys) => {
    const key = loose(tokens[i].text);
    return Boolean(key) && keys.some((k) => key === k || (cpLen(k) > 3 && ratio(key, k) >= 85));
  };
  while (begin > 0 && isWord(begin - 1, BODY_KEYS.slice(0, 8)) && !loose(tokens[begin - 1].text).startsWith("warn")) begin--;
  while (end < tokens.length && isWord(end, BODY_KEYS.slice(-8))) end++;
  let span = [];
  for (let i = begin; i < end; i++) span.push(i);
  for (let k = span.length - 1; k >= 0; k--) {
    if (loose(tokens[span[k]].text).startsWith("problem")) {
      span = span.slice(0, k + 1);
      break;
    }
  }
  const head = findHeader(tokens, begin);
  const headerIdx = head === null ? [] : [head, head + 1];
  if (head !== null && span.includes(head)) span = span.filter((i) => i > head + 1);
  const body = span.filter((i) => !headerIdx.includes(i));
  span = [...new Set([...headerIdx, ...body])].sort((a, b) => a - b);

  const words = body.map((i) => clean(tokens[i].text));
  const [major, minor, marks, diff] = compare(words, body.map((i) => tokens[i].conf));
  const header = headerIdx.map((i) => clean(tokens[i].text));
  return new WarningReading(evidence, true, {
    score,
    tokens: span.map((i) => tokens[i]),
    headerTokens: headerIdx.map((i) => tokens[i]),
    headerFound: header.join(" "),
    headerCaps: header.length > 0 && header.every((w) => isUpper(w.replace(/:+$/, ""))),
    headerColon: header.length > 0 && (header[header.length - 1].endsWith(":")
      || (head + 2 < tokens.length && tokens[head + 2].text.startsWith(":"))),
    major,
    minor,
    marks,
    diff: [{ op: header.length ? "equal" : "missing", text: "GOVERNMENT WARNING:" }, ...diff],
  });
}
