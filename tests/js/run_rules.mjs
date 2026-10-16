// Run the browser rules (app/static/local/rules.js) over cases from tests/parity_cases.py.
//   node tests/js/run_rules.mjs cases.json results.json
import { readFileSync, writeFileSync } from "node:fs";

const [input, output] = process.argv.slice(2);
const { normalizeApplication, overall, verify } = await import(new URL("../../app/static/local/rules.js", import.meta.url));

const toEvidence = (e) => ({
  source: e.source,
  name: e.name,
  imageIndex: e.image_index,
  fields: e.fields || {},
  warningText: e.warning_text,
  headerBold: e.header_bold,
  headerBoldNote: e.header_bold_note || "",
  tokens: e.tokens.map((t) => ({ text: t.text, conf: t.conf, box: t.box })),
});

const results = JSON.parse(readFileSync(input, "utf8")).map((c) => {
  const checks = verify(normalizeApplication(c.application), c.evidences.map(toEvidence));
  const [status, headline] = overall(checks);
  return { overall: status, headline, checks };
});
writeFileSync(output, JSON.stringify(results));
