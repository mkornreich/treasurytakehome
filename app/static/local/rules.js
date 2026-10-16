// Compare the application with what the readers saw, field by field: a port of
// app/verification/rules.py (and merge_boxes from evidence.py), for when the page
// must work without the server. tests/test_local_parity.py checks that this file
// gives the same results as the Python rules.
//
// Evidence: {source, name, tokens: [{text, box, conf}], imageIndex, fields, warningText,
//            headerBold, headerBoldNote}
// Application: {beverage_type, brand_name, class_type, alcohol_content, net_contents, bottler, country_of_origin}

import { ratio } from "./fuzz.js";
import {
  US_STATES, casefold, clean, compareKeys, expandStates, findPhrase, fmtG, isState, loose, median, pyRound, similarity, splitWords,
} from "./text.js";
import {
  findAbvMentions, findProofMentions, findVolumeMentions, formatMl, parseAlcohol, parseVolume, sameVolume,
} from "./units.js";
import { readWarning } from "./warning.js";

export const PASS = "pass";
export const REVIEW = "review";
export const FAIL = "fail";

const READER_NAMES = { ocr: "text scanner", vision: "AI reader" };
const BOLD_PREFERENCE = { "vision:claude": 4, "ocr:warning": 3, "ocr:standard": 2, "ocr:layout": 2, "ocr:rotate": 2 };
const CONFUSABLE_DIGITS = [["3", "8"], ["5", "6"], ["6", "8"], ["0", "8"], ["1", "7"], ["5", "9"]];
const NEAR_MATCH = 90;
const WEAK_MATCH = 70;

const evidenceText = (ev) => ev.tokens.map((t) => t.text).join(" ");
const wordCount = (ev) => ev.tokens.filter((t) => /[\p{L}\p{N}]/u.test(t.text)).length;
const tokensFromText = (text) => splitWords(text).map((part) => ({ text: part, box: null, conf: 100.0 }));
const sum = (lists) => lists.flat();

function finding(status, message, fields = {}) {
  const f = {
    status, message, found: null, evidence: null, boxes: [], score: 0.0, exact: false,
    prominence: 0.0, key: "", conflict: false, ...fields,
  };
  f.conflict = f.conflict || f.status === FAIL;
  return f;
}
const replace = (f, changes) => finding(changes.status ?? f.status, changes.message ?? f.message, { ...f, ...changes });

function confusableNumbers(a, b) {
  const x = fmtG(a);
  const y = fmtG(b);
  if (x.length !== y.length) return false;
  const pairs = [];
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) pairs.push([x[i], y[i]]);
  return pairs.length > 0 && pairs.every(([c, d]) => CONFUSABLE_DIGITS.some(([p, q]) => (c === p && d === q) || (c === q && d === p)));
}

/** Downgrade a numeric conflict to review when it could be a digit misread. */
function maybeMisread(f, wanted, seen, unit) {
  if (f.status === FAIL && seen.some((v) => confusableNumbers(v, wanted))) {
    return replace(f, {
      status: REVIEW, conflict: true,
      message: `${f.message} The scanner often reads these digits incorrectly. Examine the ${unit} on the label.`,
    });
  }
  return f;
}

/** max() by (score, exact, prominence, box count); the first wins a tie, as in Python. */
function best(group) {
  let top = group[0];
  for (const f of group.slice(1)) {
    if (compareKeys([f.score, f.exact, f.prominence, f.boxes.length], [top.score, top.exact, top.prominence, top.boxes.length]) > 0) top = f;
  }
  return top;
}

const keyParts = (key) => {
  const at = key.indexOf(":");
  const kind = at < 0 ? key : key.slice(0, at);
  return [kind, new Set(key.split(":").at(-1).split(","))];
};

function corroborated(f, fails) {
  if (f.evidence === null || f.evidence.source !== "ocr") return true;
  const [kind, values] = keyParts(f.key);
  return fails.some((g) => {
    if (g === f || g.evidence === f.evidence) return false;
    const [gKind, gValues] = keyParts(g.key);
    return gKind === kind && [...values].some((v) => gValues.has(v));
  });
}

/** PASS if any reader confirmed; else FAIL if a conflict was seen twice; else best REVIEW. */
function pick(findings) {
  const passes = findings.filter((f) => f.status === PASS);
  if (passes.length) return best(passes);
  const fails = findings.filter((f) => f.status === FAIL);
  if (fails.length) {
    const confirmed = fails.filter((f) => corroborated(f, fails));
    if (confirmed.length) return best(confirmed);
    const single = best(fails);
    return replace(single, {
      status: REVIEW, conflict: true,
      message: `${single.message} Only one reading of the label shows this. The scanner can make reading errors. Examine the label.`,
    });
  }
  const reviews = findings.filter((f) => f.status === REVIEW);
  if (reviews.length) return best(reviews);
  throw new Error("no findings");
}

/** Collapse word boxes on the same text line (of the same image) into one box per line. */
export function mergeBoxes(boxes) {
  const lines = [];
  for (const box of boxes) {
    const line = lines.find((l) => {
      const ref = l[l.length - 1];
      return ref.image === box.image && Math.abs((box.y + box.h / 2) - (ref.y + ref.h / 2)) < Math.max(box.h, ref.h) * 0.6;
    });
    if (line) line.push(box);
    else lines.push([box]);
  }
  return lines.map((line) => {
    const x0 = Math.min(...line.map((b) => b.x));
    const y0 = Math.min(...line.map((b) => b.y));
    const x1 = Math.max(...line.map((b) => b.x + b.w));
    const y1 = Math.max(...line.map((b) => b.y + b.h));
    return { image: line[0].image, x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  });
}

const result = (field, label, expected, f) => ({
  field, label, status: f.status, message: f.message, expected: expected ?? null, found: f.found ?? null,
  source: f.evidence ? f.evidence.source : null, boxes: mergeBoxes(f.boxes), diff: null,
});

const tokenBoxes = (ev, start, end) => ev.tokens.slice(start, end).filter((t) => t.box).map((t) => t.box);

/** Boxes of the tokens covering a character span of the evidence text. */
function spanBoxes(ev, mention, searched = null) {
  if (searched !== null && searched !== evidenceText(ev)) return [];
  const boxes = [];
  let pos = 0;
  for (const token of ev.tokens) {
    const end = pos + token.text.length;
    if (end > mention.start && pos < mention.end && token.box) boxes.push(token.box);
    pos = end + 1;
  }
  return boxes;
}

const stripAccents = (text) => text.normalize("NFKD").replace(/\p{M}/gu, "");

// --- free-text fields: brand name, class/type, bottler, country ---------------------------------

function describeMatch(expected, found) {
  if (clean(found) === clean(expected)) return "The label and the application are the same.";
  if (casefold(clean(found)) === casefold(clean(expected))) {
    return `The text is the same. Only the capital letters are different. The label shows “${found}”.`;
  }
  if (loose(found) !== loose(expected)) return `The text is the same. The label uses the full word for an abbreviation: “${found}”.`;
  return `The text is the same. Only the punctuation or the spaces are different. The label shows “${found}”.`;
}

function textFinding(expected, found, score, evidence, boxes, structured, prominence = 0.0) {
  if (score === 100) {
    return finding(PASS, describeMatch(expected, found), { found, evidence, boxes, score, exact: clean(found) === clean(expected), prominence });
  }
  const accented = stripAccents(found) !== found || stripAccents(expected) !== expected;
  if (accented && loose(stripAccents(found)) === loose(stripAccents(expected))) {
    return finding(REVIEW, `The letters are the same, but the accent marks are different. The label shows “${found}”. `
      + "Make sure that this difference is permitted.", { found, evidence, boxes, score });
  }
  if (score >= NEAR_MATCH) {
    return finding(REVIEW, `The text is almost the same. The label shows “${found}”. Examine the label for a typing error or a reading error.`,
      { found, evidence, boxes, score });
  }
  if (structured && found) {
    return finding(FAIL, `The label shows “${found}”. This is not the same as the application.`, { found, evidence, boxes, score });
  }
  if (score >= WEAK_MATCH) {
    return finding(REVIEW, `The tool did not find this text on the label. The nearest text is “${found}”.`, { found, evidence, boxes, score });
  }
  return finding(REVIEW, "The tool did not find this text on the label. The font can be decorative, or the text can be hard to read. "
    + "Examine the label.", { found: null, evidence, boxes: [], score });
}

const CORPORATE = new Set(["llc", "l l c", "inc", "co", "corp", "corporation", "ltd", "company", "lp", "llp", "spa", "s p a", "sa", "s a"]);

/** "Appalachian Spirits, LLC, Mechanicsburg, PA" -> ["Appalachian Spirits, LLC", "Mechanicsburg, PA"]. */
export function addressParts(text) {
  const parts = [];
  for (const part of text.split(",").map((p) => p.trim())) {
    if (!part) continue;
    const key = loose(part);
    const state = isState(key) || /^[a-z]{2} \d{5}(?: \d{4})?$/.test(key);
    if (parts.length && (CORPORATE.has(key) || state)) parts[parts.length - 1] = `${parts[parts.length - 1]}, ${part}`;
    else parts.push(part);
  }
  return parts;
}

function addressFinding(expected, ev, tokens, structured) {
  const parts = addressParts(expected);
  if (parts.length < 2) return null;
  const matches = parts.map((part) => findPhrase(part, tokens, expandStates));
  if (!matches.every(Boolean)) return null;
  const score = Math.min(...matches.map((m) => m.score));
  const found = matches.map((m) => m.text).join(" … ");
  const boxes = structured ? [] : matches.flatMap((m) => tokenBoxes(ev, m.start, m.end));
  if (score === 100) return finding(PASS, `The label shows the same name and address: “${found}”.`, { found, evidence: ev, boxes, score });
  return textFinding(expected, found, score, ev, boxes, structured);
}

export function checkText(fieldName, label, expected, evidences, address = false, contained = false) {
  const transform = address ? expandStates : null;
  const prepare = transform || ((text) => text);
  const findings = [];
  for (const ev of evidences) {
    const value = (ev.fields || {})[fieldName] || "";
    if (value) {
      let found = value;
      let score = ratio(loose(prepare(expected)), loose(prepare(value)));
      const inner = contained && score < 100 ? findPhrase(expected, tokensFromText(value), transform) : null;
      if (inner && inner.score > score) [found, score] = [inner.text, inner.score];
      findings.push(textFinding(expected, found, score, ev, [], true));
      const parts = address ? addressFinding(expected, ev, tokensFromText(value), true) : null;
      if (parts) findings.push(parts);
    }
    const match = findPhrase(expected, ev.tokens, transform);
    if (match) {
      findings.push(textFinding(expected, match.text, match.score, ev, tokenBoxes(ev, match.start, match.end), false, match.height));
    }
    if (address && match && match.score < 100) {
      const parts = addressFinding(expected, ev, ev.tokens, false);
      if (parts) findings.push(parts);
    }
  }
  if (!findings.length) findings.push(finding(REVIEW, "The tool cannot read text on the label."));
  return result(fieldName, label, expected, pick(findings));
}

// --- alcohol content -------------------------------------------------------------------------

const alcoholText = (ev) => (ev.fields || {}).alcohol_content || evidenceText(ev);

export function checkAlcohol(app, evidences) {
  const label = "Alcohol content";
  if (!app.alcohol_content) return alcoholPresence(app, evidences);
  const wanted = parseAlcohol(app.alcohol_content);
  if (wanted.abv === null) {
    return {
      field: "alcohol_content", label, status: REVIEW, expected: app.alcohol_content, found: null, source: null, boxes: [], diff: null,
      message: "The tool cannot read the alcohol content on the application. Type it as in this example: “45% Alc./Vol.”.",
    };
  }
  let findings = [];
  let noPercent = false;
  let withPercent = false;
  const proofOnly = new Set(); // findings from readings that saw a proof but no ABV statement
  for (const ev of evidences) {
    const text = alcoholText(ev);
    const abvs = findAbvMentions(text);
    const proofs = findProofMentions(text);
    const boxes = (m) => spanBoxes(ev, m, text);
    const matching = abvs.filter((m) => Math.abs(m.value - wanted.abv) < 0.05);
    matching.sort((x, y) => Number(!x.percent) - Number(!y.percent));
    if (matching.length) {
      const m = matching[0];
      noPercent ||= !m.percent;
      withPercent ||= m.percent;
      let message = `The label shows the same value: ${fmtG(m.value)}% alcohol by volume.`;
      let status = PASS;
      let key = "";
      if (!m.percent) {
        message = `The label shows ${fmtG(m.value)} next to “ALC/VOL”, but it has no % sign. The alcohol content must be a percentage.`;
        status = REVIEW;
      } else if (wanted.proof !== null && proofs.length) {
        if (!proofs.some((p) => Math.abs(p.value - wanted.proof) < 0.05)) {
          const shown = proofs.map((p) => fmtG(p.value)).join(", ");
          message = `The ABV is the same, but the label shows ${shown} proof. The application shows ${fmtG(wanted.proof)} proof.`;
          status = FAIL;
          key = "proof:" + proofs.map((p) => fmtG(p.value)).join(",");
        }
      } else if (proofs.length && !proofs.some((p) => Math.abs(p.value - 2 * m.value) < 0.05)) {
        message = `The ABV is the same, but the proof on the label (${fmtG(proofs[0].value)}) is not two times the ABV.`;
        status = REVIEW;
      }
      findings.push(finding(status, message, { found: m.text, evidence: ev, boxes: [...boxes(m), ...sum(proofs.map(boxes))], score: 100, key }));
    } else if (abvs.length) {
      const shown = abvs.map((m) => `${fmtG(m.value)}%`).join(", ");
      findings.push(maybeMisread(finding(FAIL, `The label shows ${shown}. The application shows ${fmtG(wanted.abv)}%.`, {
        found: abvs[0].text, evidence: ev, boxes: sum(abvs.map(boxes)), score: 50,
        key: "abv:" + abvs.map((m) => fmtG(m.value)).sort().join(","),
      }), wanted.abv, abvs.map((m) => m.value), "alcohol content"));
    } else if (proofs.length) {
      const p = proofs[0];
      if ((wanted.proof !== null && Math.abs(p.value - wanted.proof) < 0.05) || Math.abs(p.value / 2 - wanted.abv) < 0.05) {
        findings.push(finding(PASS, `The label shows the same value: ${fmtG(p.value)} proof (${fmtG(p.value / 2)}% ABV).`,
          { found: p.text, evidence: ev, boxes: boxes(p), score: 90 }));
        proofOnly.add(findings[findings.length - 1]);
      } else {
        findings.push(finding(FAIL, `The label shows ${fmtG(p.value)} proof. The application shows ${wanted.describe()}.`,
          { found: p.text, evidence: ev, boxes: boxes(p), score: 50, key: `proof:${fmtG(p.value)}` }));
      }
    }
  }
  // A reading that saw only the proof cannot show the % sign that another reading found missing.
  if (noPercent && !withPercent) findings = findings.filter((f) => !proofOnly.has(f));
  if (!findings.length) findings.push(finding(REVIEW, "The tool did not find an alcohol content statement on the label."));
  return result("alcohol_content", label, app.alcohol_content, pick(findings));
}

function alcoholPresence(app, evidences) {
  for (const ev of evidences) {
    const mentions = findAbvMentions(alcoholText(ev));
    if (mentions.length) {
      const m = mentions[0];
      return result("alcohol_content", "Alcohol content", null, finding(PASS,
        `The label shows ${fmtG(m.value)}% alcohol by volume. The application does not give a value, so the tool did not compare it.`,
        { found: m.text, evidence: ev, boxes: spanBoxes(ev, m, alcoholText(ev)) }));
    }
  }
  if ((app.beverage_type || "spirits") === "spirits") {
    return result("alcohol_content", "Alcohol content", null, finding(REVIEW,
      "The tool did not find the alcohol content on the label. Distilled spirits must show it."));
  }
  return null; // optional for most beer and wine
}

// --- net contents ----------------------------------------------------------------------------

export function checkNetContents(app, evidences) {
  const label = "Net contents";
  const wanted = app.net_contents ? parseVolume(app.net_contents) : null;
  if (app.net_contents && wanted === null) {
    return {
      field: "net_contents", label, status: REVIEW, expected: app.net_contents, found: null, source: null, boxes: [], diff: null,
      message: "The tool cannot read the net contents on the application. Type it as in this example: “750 mL”.",
    };
  }
  const findings = [];
  for (const ev of evidences) {
    const text = (ev.fields || {}).net_contents || evidenceText(ev);
    const mentions = findVolumeMentions(text);
    if (!mentions.length) continue;
    const boxes = (m) => spanBoxes(ev, m, text);
    if (wanted === null) {
      const m = mentions[0];
      findings.push(finding(PASS, `The label shows ${m.text}. The application does not give a value, so the tool did not compare it.`,
        { found: m.text, evidence: ev, boxes: boxes(m), score: 100 }));
      continue;
    }
    const matching = mentions.filter((m) => sameVolume(m.value, wanted));
    if (matching.length) {
      const m = matching[0];
      const sameUnits = Math.abs(m.value - wanted) < 0.01;
      const note = sameUnits ? "" : ` ${m.text} is the same as ${formatMl(wanted)}.`;
      findings.push(finding(PASS, `The label shows the same value: ${m.text}.${note}`, { found: m.text, evidence: ev, boxes: boxes(m), score: 100 }));
    } else {
      const shown = mentions.map((m) => m.text).join(", ");
      findings.push(maybeMisread(finding(FAIL, `The label shows ${shown}. The application shows ${app.net_contents}.`, {
        found: mentions[0].text, evidence: ev, boxes: sum(mentions.map(boxes)), score: 50,
        key: "ml:" + mentions.map((m) => pyRound(m.value).toFixed(0)).sort().join(","),
      }), wanted, mentions.map((m) => pyRound(m.value, 2)), "net contents"));
    }
  }
  if (!findings.length) findings.push(finding(REVIEW, "The tool did not find the net contents on the label, for example “750 mL”."));
  return result("net_contents", label, app.net_contents || null, pick(findings));
}

// --- government warning ----------------------------------------------------------------------

function legible(ev) {
  const confidences = ev.tokens.map((t) => t.conf);
  return ev.source === "vision" || (wordCount(ev) >= 20 && confidences.length > 0 && median(confidences) >= 75);
}

/** No reading located the statement: missing, or just unreadable? */
function absentWarning(readings) {
  const evidences = readings.map((r) => r.evidence);
  const words = new Set(["government", "surgeon", "pregnancy", "machinery"]);
  const partial = evidences.some((ev) => ev.tokens.some((t) => words.has(loose(t.text))));
  const perImage = new Map();
  for (const ev of evidences) perImage.set(ev.imageIndex, (perImage.get(ev.imageIndex) || 0) + Number(legible(ev)));
  const vision = evidences.some((ev) => ev.source === "vision");
  // Missing only if every picture was read clearly, twice (or by the AI reader).
  if (!partial && (vision || (perImage.size && Math.min(...perImage.values()) >= 2))) {
    return finding(FAIL, "The tool did not find the government warning on the label.", { evidence: evidences[0] });
  }
  return finding(REVIEW, "The tool cannot read the government warning. The warning is not on the label, or it is too small or not clear.",
    { evidence: evidences[0] });
}

const sentences = (items) => items.map((item) => `${item[0].toUpperCase()}${item.slice(1)}` + (item.endsWith(".") || item.endsWith(".”") ? "" : ".")).join(" ");

function bestReading(readings) {
  let top = readings[0];
  for (const r of readings.slice(1)) {
    if (compareKeys([r.located, r.score, -r.major.length, -r.minor.length], [top.located, top.score, -top.major.length, -top.minor.length]) > 0) top = r;
  }
  return top;
}

const maxBy = (items, key) => items.reduce((top, x) => (compareKeys(key(x), key(top)) > 0 ? x : top));

/** One reader family's verdict on the wording, from all of its readings. */
function sourceWording(readings) {
  const located = readings.filter((r) => r.located);
  if (!located.length) return [absentWarning(readings), maxBy(readings, (r) => [r.score])];
  const exact = located.filter((r) => r.exact);
  if (exact.length) {
    const r = maxBy(exact, (x) => [x.score]);
    return [finding(PASS, "The warning is word-for-word the same as the required statement.",
      { found: r.text, evidence: r.evidence, boxes: r.boxes, score: r.score }), r];
  }
  let top = bestReading(located);
  const cleanReads = located.filter((r) => !r.major.length && r.score >= 85);
  if (top.major.length && cleanReads.length) top = maxBy(cleanReads, (r) => [r.score]);
  if (top.major.length) {
    const message = `The text is different from the required statement. ${sentences(top.major.slice(0, 3))}`;
    const confirmed = top.evidence.source !== "ocr" || located.some(
      (other) => other.evidence !== top.evidence && [...other.marks].some((m) => top.marks.has(m)),
    );
    if (confirmed) return [finding(FAIL, message, { found: top.text, evidence: top.evidence, boxes: top.boxes, score: top.score }), top];
    return [finding(REVIEW, `${message} Only one reading shows this. The scanner can make reading errors. Compare the text on the label.`,
      { found: top.text, evidence: top.evidence, boxes: top.boxes, score: top.score, conflict: true }), top];
  }
  return [finding(REVIEW, `The text is almost the same as the required statement. Compare these items. ${sentences(top.minor.slice(0, 3))} `
    + "The difference can be a reading error.", { found: top.text, evidence: top.evidence, boxes: top.boxes, score: top.score }), top];
}

function sourceCapitals(readings) {
  const located = readings.filter((r) => r.located);
  const withHeader = located.filter((r) => r.headerFound);
  const capitals = withHeader.filter((r) => r.headerCaps);
  if (capitals.length) {
    const r = maxBy(capitals, (x) => [x.headerColon, x.score]);
    if (r.headerColon) {
      return finding(PASS, "“GOVERNMENT WARNING:” is in capital letters.", { found: r.headerFound, evidence: r.evidence, boxes: r.headerBoxes, score: r.score });
    }
    return finding(REVIEW, "The heading is in capital letters, but the tool did not find the colon after “GOVERNMENT WARNING”.",
      { found: r.headerFound, evidence: r.evidence, boxes: r.headerBoxes, score: r.score });
  }
  if (withHeader.length) {
    const r = maxBy(withHeader, (x) => [x.score]);
    const message = `The label shows “${r.headerFound}”. The heading must be in capital letters: “GOVERNMENT WARNING:”.`;
    const seenBy = new Set(withHeader.map((x) => x.evidence));
    if (r.evidence.source !== "ocr" || seenBy.size >= 2) {
      return finding(FAIL, message, { found: r.headerFound, evidence: r.evidence, boxes: r.headerBoxes, score: r.score });
    }
    return finding(REVIEW, `${message} Only one reading shows this. Examine the label.`,
      { found: r.headerFound, evidence: r.evidence, boxes: r.headerBoxes, score: r.score, conflict: true });
  }
  const r = maxBy(located, (x) => [x.score]);
  if (r.evidence.source !== "ocr") {
    return finding(FAIL, "The statement must start with “GOVERNMENT WARNING:”.", { evidence: r.evidence, score: r.score });
  }
  return finding(REVIEW, "The tool did not find the heading “GOVERNMENT WARNING:”. Make sure that the label has this heading.",
    { evidence: r.evidence, boxes: r.boxes.slice(0, 2), score: r.score });
}

function precedence(findings) {
  for (const status of [PASS, FAIL, REVIEW]) {
    const group = findings.filter((f) => f.status === status);
    if (group.length) return best(group);
  }
  throw new Error("no findings");
}

/** Two engines that disagree (one pass, one fail) -> a person decides, unless the failing one could not read the statement. */
function combineReaders(perReader) {
  const findings = [...perReader.values()];
  const passing = findings.find((f) => f.status === PASS);
  const failing = findings.find((f) => f.status !== PASS && f.conflict);
  if (passing && failing) {
    if (failing.score >= 90) {
      const failName = READER_NAMES[failing.evidence.source];
      const passName = READER_NAMES[passing.evidence.source];
      return finding(REVIEW, `The ${failName} and the ${passName} do not agree. The ${failName} found this: ${failing.message} `
        + "Compare the text on the label.", {
        found: failing.found, evidence: failing.evidence, boxes: failing.boxes.length ? failing.boxes : passing.boxes, score: failing.score,
      });
    }
    return passing;
  }
  return precedence(findings);
}

export function checkWarning(evidences) {
  if (!evidences.length) {
    return [{ field: "warning_text", label: "Government warning", status: REVIEW, message: "The tool cannot read text on the label.",
      expected: null, found: null, source: null, boxes: [], diff: null }];
  }
  const bySource = new Map();
  for (const ev of evidences) {
    if (!bySource.has(ev.source)) bySource.set(ev.source, []);
    bySource.get(ev.source).push(readWarning(ev));
  }
  const verdicts = new Map([...bySource].map(([source, readings]) => [source, sourceWording(readings)]));
  const wording = combineReaders(new Map([...verdicts].map(([s, v]) => [s, v[0]])));
  const results = [result("warning_text", "Government warning: text", null, wording)];
  const withDiff = [...verdicts.values()].find(([, r]) => r.evidence === wording.evidence && r.located);
  results[0].diff = withDiff ? withDiff[1].diff : null;
  const located = new Map();
  for (const [source, readings] of bySource) {
    const found = readings.filter((r) => r.located);
    if (found.length) located.set(source, found);
  }
  if (!located.size) {
    results[0].label = "Government warning";
    return results;
  }
  const capitals = combineReaders(new Map([...located].map(([s, rs]) => [s, sourceCapitals(rs)])));
  results.push(result("warning_caps", "Government warning: capital letters", "GOVERNMENT WARNING:", capitals));
  results.push(bold(located));
  return results;
}

function bold(located) {
  const label = "Government warning: bold type";
  const votes = new Map();
  let boxes = [];
  for (const [source, readings] of located) {
    // Stroke widths are measured best on unfiltered images: the enlarged warning re-read, then the plain passes.
    const measured = readings.filter((r) => r.evidence.headerBold !== null && r.evidence.headerBold !== undefined);
    if (measured.length) {
      const r = maxBy(measured, (x) => [BOLD_PREFERENCE[x.evidence.name.split("-")[0]] || 0, x.score]);
      votes.set(source, [r.evidence.headerBold, r.evidence.headerBoldNote || ""]);
    }
    if (!boxes.length) boxes = mergeBoxes(bestReading(readings).headerBoxes);
  }
  const base = { field: "warning_bold", label, expected: null, found: null, source: null, boxes, diff: null };
  if (!votes.size) {
    return { ...base, status: REVIEW, message: "The tool did not measure the type weight of “GOVERNMENT WARNING:”. "
      + "Examine the label to make sure that the heading is in bold type." };
  }
  const saysBold = [...votes.values()].map(([v]) => v);
  const notes = [...votes.values()].map(([, note]) => note).filter(Boolean).join(" ");
  if (saysBold.every(Boolean)) {
    return { ...base, status: PASS, message: `“GOVERNMENT WARNING:” is in bold type. ${notes}`.trim() };
  }
  const status = votes.size > 1 && !saysBold.some(Boolean) ? FAIL : REVIEW;
  return { ...base, status, message: `The tool measured “GOVERNMENT WARNING:” as not bold. Bold type is required. ${notes} Examine the label.`.trim() };
}

// --- country of origin ------------------------------------------------------------------------

const ORIGIN_RE = /\b(product of|produce of|produced (?:and bottled )?in|bottled in|made in|hecho en|wine of|imported from|produit de|prodotto in|producto de)\s+((?:the\s+)?[a-z][a-z]+(?:\s[a-z]+)?)/gi;

export function checkCountry(expected, evidences) {
  const res = checkText("country_of_origin", "Country of origin", expected, evidences, false, true);
  if (res.status === PASS) return res;
  const findings = [];
  for (const ev of evidences) {
    for (const m of evidenceText(ev).matchAll(ORIGIN_RE)) {
      const stated = m[2];
      const statement = `${m[1]} ${stated}`;
      const match = findPhrase(expected, tokensFromText(stated));
      if (match && match.score >= 90) {
        findings.push(finding(PASS, `The label shows the same country: “${statement}”.`, { found: statement, evidence: ev, score: 100 }));
      } else {
        findings.push(finding(FAIL, `The label shows “${statement}”. The application shows ${expected}.`,
          { found: statement, evidence: ev, score: 80, key: `origin:${loose(stated)}` }));
      }
    }
  }
  if (!findings.length) return res;
  return result("country_of_origin", "Country of origin", expected, pick(findings));
}

// --- all checks ------------------------------------------------------------------------------

export function verify(app, evidences) {
  const checks = [];
  if (app.brand_name) checks.push(checkText("brand_name", "Brand name", app.brand_name, evidences));
  if (app.class_type) checks.push(checkText("class_type", "Class or type", app.class_type, evidences));
  const alcohol = checkAlcohol(app, evidences);
  if (alcohol) checks.push(alcohol);
  checks.push(checkNetContents(app, evidences));
  if (app.bottler) checks.push(checkText("bottler", "Name and address of the bottler", app.bottler, evidences, true, true));
  if (app.country_of_origin) checks.push(checkCountry(app.country_of_origin, evidences));
  checks.push(...checkWarning(evidences));
  return checks;
}

export function overall(checks) {
  const failed = checks.filter((c) => c.status === FAIL).length;
  const review = checks.filter((c) => c.status === REVIEW).length;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (failed) {
    const tail = review ? `, ${plural(review, "item")} to examine` : "";
    return [FAIL, `${plural(failed, "problem")} found${tail}`];
  }
  if (review) return [REVIEW, `${plural(review, "item")} to examine`];
  return [PASS, `All ${checks.length} checks are OK`];
}

/** Application values as the server's model cleans them (strip; beverage type aliases). */
export function normalizeApplication(raw = {}) {
  const app = {};
  for (const key of ["brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"]) {
    app[key] = typeof raw[key] === "string" ? raw[key].trim() : "";
  }
  const aliases = { "": "spirits", spirit: "spirits", "distilled spirits": "spirits", liquor: "spirits", "malt beverage": "beer", malt: "beer", ale: "beer" };
  const type = String(raw.beverage_type ?? "").trim().toLowerCase();
  app.beverage_type = aliases[type] ?? type;
  return app;
}

export { US_STATES };
