// Measure what app/static/local/imaging.js measures, on pictures prepared by tests/test_local_parity.py.
//   node tests/js/run_imaging.mjs dir   (reads dir/cases.json and dir/<name>.gray, dir/<name>.orig; writes dir/js.json)
import { readFileSync, writeFileSync } from "node:fs";

const [dir] = process.argv.slice(2);
const im = await import(new URL("../../app/static/local/imaging.js", import.meta.url));
const cases = JSON.parse(readFileSync(`${dir}/cases.json`, "utf8"));
const read = (file, width, height) => ({ data: new Uint8Array(readFileSync(`${dir}/${file}`)), width, height });

const results = cases.map((c) => {
  const view = new im.View(read(`${c.id}.gray`, c.width, c.height), [1, 0, 0, 0, 1, 0, 0, 0, 1], [c.width, c.height]);
  const heading = im.headingWeight(view, c.words);
  return { ratio: heading.ratio, bold: heading.bold, note: heading.note, notices: im.qualityNotices(read(`${c.id}.orig`, c.orig_width, c.orig_height)) };
});
writeFileSync(`${dir}/js.json`, JSON.stringify(results));
