// Alcohol content and net contents parsing: a port of app/verification/units.py.
// "45% Alc./Vol. (90 Proof)" == "45.0% ABV"; "750 mL" == "75 cl" == "0.75 L".

import { fmtG } from "./text.js";

const NUMBER = String.raw`(\d{1,4}(?:[.,]\d{1,3})?)`;
const PERCENT_RE = new RegExp(NUMBER + String.raw`\s*%`, "g");
const PROOF_RE = new RegExp(NUMBER + String.raw`\s*°?\s*proof\b`, "gi");
const ALCOHOL_CONTEXT_RE = /alc|vol|abv|alcohol|proof/i;
// "53.5 ALC/VOL": an alcohol statement missing its % sign.
const BARE_ABV_RE = new RegExp(NUMBER + String.raw`\s*(?=alc\b|alc[./]|abv\b)`, "gi");
const SPACED_RE = /(?<!\S)(?:\S ){2,}\S(?!\S)/g;

// Longest spellings first so "fl oz" wins over "oz" and "liters" over "l".
const UNITS = [
  [String.raw`millilit(?:er|re)s?`, 1.0],
  [String.raw`m\s?l`, 1.0],
  [String.raw`centilit(?:er|re)s?`, 10.0],
  [String.raw`c\s?l`, 10.0],
  [String.raw`lit(?:er|re)s?`, 1000.0],
  [String.raw`l`, 1000.0],
  [String.raw`fl\.?\s*oz\.?`, 29.5735],
  [String.raw`fluid\s+ounces?`, 29.5735],
  [String.raw`oz\.?`, 29.5735],
  [String.raw`pints?`, 473.176],
  [String.raw`pt\.?`, 473.176],
  [String.raw`quarts?`, 946.353],
  [String.raw`qt\.?`, 946.353],
  [String.raw`gallons?`, 3785.41],
  [String.raw`gal\.?`, 3785.41],
];
const VOLUME_SOURCE = NUMBER + String.raw`(?:\s+[^\d\s]{1,2}(?=\s)){0,2}\s*(` + UNITS.map(([u]) => u).join("|") + ")(?![a-z])";
// "43% ALC/VOL IL": a 1-litre size right after the alcohol statement, its "1" read as "I".
const ONE_LITRE_RE = /((?:vol|proof|%)\.?[^\p{L}\p{N}_\s]{0,2}\s+)[Il](\s?l(?:iters?|itres?)?\b)/giu;
const UNIT_PATTERNS = UNITS.map(([u, ml]) => [new RegExp(`^(?:${u})$`, "i"), ml]);

/** '0,75' -> 0.75 (decimal comma); '1,000' -> 1000 (thousands separator). */
export function toFloat(raw) {
  if (raw.includes(",")) {
    const at = raw.indexOf(",");
    const whole = raw.slice(0, at);
    const frac = raw.slice(at + 1);
    raw = frac.length === 3 ? whole + frac : `${whole}.${frac}`;
  }
  return parseFloat(raw);
}

/** Repair common OCR confusions inside numbers: 75O mL -> 750 mL. */
export function fixOcrDigits(text) {
  return text.replace(/(?<![A-Za-z])[\dOolI][\dOolI.,]*(?=\s*(?:%|[a-zA-Z]{1,6}\b|$|\s))/g, (token) => {
    if (!/\d/.test(token)) return token;
    return token.replace(/[OolI]/g, (c) => ({ O: "0", o: "0", l: "1", I: "1" })[c]);
  });
}

export class Alcohol {
  constructor() {
    this.abv = null;
    this.proof = null;
  }

  describe() {
    const parts = [];
    if (this.abv !== null) parts.push(`${fmtG(this.abv)}% alcohol by volume`);
    if (this.proof !== null) parts.push(`${fmtG(this.proof)} proof`);
    return parts.join(", ");
  }
}

const first = (re, text) => {
  re.lastIndex = 0;
  const m = re.exec(text);
  re.lastIndex = 0;
  return m;
};

/** Parse an application value such as '45% Alc./Vol. (90 Proof)' or just '45'. */
export function parseAlcohol(text) {
  const result = new Alcohol();
  let m;
  if ((m = first(PERCENT_RE, text))) result.abv = toFloat(m[1]);
  if ((m = first(PROOF_RE, text))) result.proof = toFloat(m[1]);
  if (result.abv === null && result.proof === null) {
    if ((m = new RegExp(String.raw`^\s*` + NUMBER + String.raw`\s*$`).exec(text))) result.abv = toFloat(m[1]);
  }
  if (result.abv === null && result.proof !== null) result.abv = result.proof / 2;
  return result;
}

/** Repair OCR digit slips and close up letter-spaced text ("7 5 0 m L" -> "750mL").
 *  Returns the cleaned text and, for each of its characters, its index in the original. */
function prepare(text) {
  text = fixOcrDigits(text).replace(ONE_LITRE_RE, (_, a, b) => `${a}1${b}`);
  const out = [];
  const index = [];
  let pos = 0;
  for (const m of text.matchAll(SPACED_RE)) {
    const end = m.index + m[0].length;
    for (let i = pos; i < end; i++) {
      if (i < m.index || text[i] !== " ") {
        out.push(text[i]);
        index.push(i);
      }
    }
    pos = end;
  }
  for (let i = pos; i < text.length; i++) {
    out.push(text[i]);
    index.push(i);
  }
  index.push(text.length);
  return [out.join(""), index];
}

const mention = (value, m, index, percent = true) => ({
  value,
  text: m[0].trim(),
  start: index[m.index],
  end: index[m.index + m[0].length - 1] + 1,
  percent,
});

/** Percentages on the label that look like an alcohol statement ("100% agave" is excluded). */
export function findAbvMentions(source) {
  const [text, index] = prepare(source);
  const mentions = [];
  const contextual = [];
  for (const m of text.matchAll(PERCENT_RE)) {
    const value = toFloat(m[1]);
    if (!(value > 0 && value < 100)) continue;
    const found = mention(value, m, index);
    mentions.push(found);
    const start = m.index;
    const end = m.index + m[0].length;
    const before = text.slice(Math.max(0, start - 15), start);
    const after = text.slice(end, end + 12);
    if (ALCOHOL_CONTEXT_RE.test(before) || ALCOHOL_CONTEXT_RE.test(after)) contextual.push(found);
  }
  for (const m of text.matchAll(BARE_ABV_RE)) {
    const value = toFloat(m[1]);
    if (value > 0 && value < 100) contextual.push(mention(value, m, index, false));
  }
  return contextual.length ? contextual : mentions;
}

export function findProofMentions(source) {
  const [text, index] = prepare(source);
  return [...text.matchAll(PROOF_RE)].map((m) => mention(toFloat(m[1]), m, index));
}

function unitToMl(unit) {
  unit = unit.trim().replace(/\s+/g, " ");
  for (const [pattern, ml] of UNIT_PATTERNS) if (pattern.test(unit)) return ml;
  return null;
}

/** Parse '750 mL', '1.75L', '12 FL. OZ.' into millilitres. */
export function parseVolume(text) {
  const m = new RegExp(VOLUME_SOURCE, "i").exec(fixOcrDigits(text));
  if (!m) return null;
  const factor = unitToMl(m[2]);
  return factor ? toFloat(m[1]) * factor : null;
}

export function findVolumeMentions(source) {
  const [text, index] = prepare(source);
  const found = [];
  for (const m of text.matchAll(new RegExp(VOLUME_SOURCE, "gi"))) {
    const factor = unitToMl(m[2]);
    if (factor) found.push(mention(toFloat(m[1]) * factor, m, index));
  }
  return found;
}

/** Equal within 1% - covers '12 fl oz' printed as '355 mL'. */
export const sameVolume = (a, b) => Math.abs(a - b) <= Math.max(a, b) * 0.01;

export const formatMl = (ml) => (ml >= 1000 ? `${fmtG(ml / 1000)} L` : `${fmtG(ml)} mL`);
