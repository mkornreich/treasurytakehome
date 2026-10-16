// String matching used by the rules, ported so that the browser gets the same numbers as the server:
// rapidfuzz's fuzz.ratio and fuzz.partial_ratio_alignment (from rapidfuzz-cpp, the C++ code that
// the server's rapidfuzz 3.14.6 runs) and Python's difflib.SequenceMatcher (autojunk=False).
// tests/test_local_parity.py compares these with the Python originals.

const toChars = (s) => (typeof s === "string" ? Array.from(s) : s);

/** Longest common subsequence length, bit-parallel (Hyyrö), for a pattern cached once. */
class CachedLcs {
  constructor(pattern) {
    this.chars = toChars(pattern);
    this.len = this.chars.length;
    this.words = Math.max(1, Math.ceil(this.len / 32));
    this.masks = new Map();
    this.chars.forEach((ch, i) => {
      let mask = this.masks.get(ch);
      if (!mask) this.masks.set(ch, (mask = new Uint32Array(this.words)));
      mask[i >>> 5] |= 1 << (i & 31);
    });
    this.zero = new Uint32Array(this.words);
    const tail = this.len & 31;
    this.lastMask = tail ? ((1 << tail) - 1) >>> 0 : 0xffffffff;
  }

  lcs(text, start = 0, end = text.length) {
    if (!this.len) return 0;
    const n = this.words;
    const s = new Uint32Array(n).fill(0xffffffff);
    for (let k = start; k < end; k++) {
      const m = this.masks.get(text[k]);
      if (!m) continue;
      let carry = 0;
      for (let w = 0; w < n; w++) {
        const sw = s[w];
        const u = (sw & m[w]) >>> 0;
        const sum = sw + u + carry; // S + U, with the carry between words
        carry = sum > 0xffffffff ? 1 : 0;
        s[w] = ((sum >>> 0) | (sw & ~m[w])) >>> 0; // (S + U) | (S - U); S - U == S & ~M
      }
    }
    let ones = 0;
    for (let w = 0; w < n; w++) {
      let v = w === n - 1 ? s[w] & this.lastMask : s[w];
      v >>>= 0;
      while (v) { v &= v - 1; ones++; }
    }
    return this.len - ones;
  }

  /** Indel distance between the pattern and text[start:end]. */
  distance(text, start = 0, end = text.length) {
    return this.len + (end - start) - 2 * this.lcs(text, start, end);
  }

  /** rapidfuzz normalized similarity * 100, or 0 when below `cutoff` (0-100). */
  similarity(text, start = 0, end = text.length, cutoff = 0) {
    const lensum = this.len + (end - start);
    const normDist = lensum ? this.distance(text, start, end) / lensum : 0;
    const sim = 1.0 - normDist;
    return sim >= cutoff / 100 ? sim * 100 : 0;
  }
}

/** fuzz.ratio: normalized Indel similarity, 0-100. */
export function ratio(a, b) {
  const x = toChars(a);
  const y = toChars(b);
  if (!x.length && !y.length) return 100;
  return new CachedLcs(x).similarity(y);
}

function partialRatioImpl(s1, s2, scoreCutoff) {
  const len1 = s1.length;
  const len2 = s2.length;
  const cached = new CachedLcs(s1);
  const charSet = new Set(s1);
  const res = { score: 0, srcStart: 0, srcEnd: len1, destStart: 0, destEnd: len1 };

  if (len2 > len1) {
    const maximum = len1 * 2;
    const normCutoffSim = Math.min(1.0, 1.0 - scoreCutoff / 100 + 0.00001);
    let cutoffDist = Math.ceil(maximum * normCutoffSim);
    let bestDist = Infinity;
    const scores = new Array(len2 - len1).fill(Infinity);
    let windows = [[0, len2 - len1 - 1]];
    const score = (i) => {
      if (scores[i] !== Infinity) return false;
      scores[i] = cached.distance(s2, i, i + len1);
      if (scores[i] < cutoffDist) {
        cutoffDist = bestDist = scores[i];
        res.destStart = i;
        res.destEnd = i + len1;
        if (bestDist === 0) return true;
      }
      return false;
    };
    while (windows.length) {
      const next = [];
      for (const [first, second] of windows) {
        if (score(first) || score(second)) {
          res.score = 100;
          return res;
        }
        const cellDiff = second - first;
        if (cellDiff === 1) continue;
        const knownEdits = Math.abs(scores[first] - scores[second]);
        const maxImprovement = Math.floor((cellDiff - Math.floor(knownEdits / 2)) / 2) * 2;
        const minScore = Math.min(scores[first], scores[second]) - maxImprovement;
        if (minScore < cutoffDist) {
          const center = Math.floor(cellDiff / 2);
          next.push([first, first + center], [first + center, second]);
        }
      }
      windows = next;
    }
    const best = (1.0 - bestDist / maximum) * 100;
    if (best >= scoreCutoff) scoreCutoff = res.score = best;
  }

  for (let i = 1; i < len1; i++) {
    if (!charSet.has(s2[i - 1])) continue;
    const r = cached.similarity(s2, 0, i, scoreCutoff);
    if (r > res.score) {
      scoreCutoff = res.score = r;
      res.destStart = 0;
      res.destEnd = i;
      if (res.score === 100) return res;
    }
  }
  for (let i = Math.max(0, len2 - len1); i < len2; i++) {
    if (!charSet.has(s2[i])) continue;
    const r = cached.similarity(s2, i, len2, scoreCutoff);
    if (r > res.score) {
      scoreCutoff = res.score = r;
      res.destStart = i;
      res.destEnd = len2;
      if (res.score === 100) return res;
    }
  }
  return res;
}

/** fuzz.partial_ratio_alignment(needle, haystack): {score, srcStart, srcEnd, destStart, destEnd}. */
export function partialRatioAlignment(a, b) {
  const s1 = toChars(a);
  const s2 = toChars(b);
  if (!s1.length && !s2.length) return { score: 100, srcStart: 0, srcEnd: 0, destStart: 0, destEnd: 0 };
  if (s1.length > s2.length) {
    const r = alignment(s2, s1);
    return { score: r.score, srcStart: r.destStart, srcEnd: r.destEnd, destStart: r.srcStart, destEnd: r.srcEnd };
  }
  return alignment(s1, s2);
}

function alignment(s1, s2) {
  if (!s1.length || !s2.length) {
    return { score: s1.length === s2.length ? 100 : 0, srcStart: 0, srcEnd: s1.length, destStart: 0, destEnd: s1.length };
  }
  const res = partialRatioImpl(s1, s2, 0);
  if (res.score !== 100 && s1.length === s2.length) {
    const res2 = partialRatioImpl(s2, s1, res.score);
    if (res2.score > res.score) {
      return { score: res2.score, srcStart: res2.destStart, srcEnd: res2.destEnd, destStart: res2.srcStart, destEnd: res2.srcEnd };
    }
  }
  return res;
}

/** difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() for lists of strings. */
export function opcodes(a, b) {
  const b2j = new Map();
  b.forEach((item, j) => {
    if (!b2j.has(item)) b2j.set(item, []);
    b2j.get(item).push(j);
  });

  const longest = (alo, ahi, blo, bhi) => {
    let besti = alo, bestj = blo, bestsize = 0;
    let j2len = new Map();
    for (let i = alo; i < ahi; i++) {
      const next = new Map();
      for (const j of b2j.get(a[i]) || []) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) || 0) + 1;
        next.set(j, k);
        if (k > bestsize) {
          besti = i - k + 1;
          bestj = j - k + 1;
          bestsize = k;
        }
      }
      j2len = next;
    }
    // With no junk, difflib's extension loops cannot grow a longest match any further.
    return [besti, bestj, bestsize];
  };

  const queue = [[0, a.length, 0, b.length]];
  const blocks = [];
  while (queue.length) {
    const [alo, ahi, blo, bhi] = queue.pop();
    const [i, j, k] = longest(alo, ahi, blo, bhi);
    if (k) {
      blocks.push([i, j, k]);
      if (alo < i && blo < j) queue.push([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
    }
  }
  blocks.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2]);
  const merged = [];
  let [i1, j1, k1] = [0, 0, 0];
  for (const [i2, j2, k2] of blocks) {
    if (i1 + k1 === i2 && j1 + k1 === j2) k1 += k2;
    else {
      if (k1) merged.push([i1, j1, k1]);
      [i1, j1, k1] = [i2, j2, k2];
    }
  }
  if (k1) merged.push([i1, j1, k1]);
  merged.push([a.length, b.length, 0]);

  const codes = [];
  let i = 0, j = 0;
  for (const [ai, bj, size] of merged) {
    const tag = i < ai && j < bj ? "replace" : i < ai ? "delete" : j < bj ? "insert" : "";
    if (tag) codes.push([tag, i, ai, j, bj]);
    i = ai + size;
    j = bj + size;
    if (size) codes.push(["equal", ai, i, bj, j]);
  }
  return codes;
}
