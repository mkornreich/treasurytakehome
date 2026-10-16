// Label Check front end. Plain JavaScript, no dependencies, nothing loaded from outside this server.

const FIELDS = ["brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"];
const STATUS = {
  pass: { icon: "✓", word: "OK" },
  review: { icon: "!", word: "Examine" },
  fail: { icon: "✕", word: "Problem" },
  queued: { icon: "…", word: "Not started" },
  running: { icon: "…", word: "In progress" },
  error: { icon: "?", word: "No result" },
};
const MAX_SIDE = 3000; // large phone photos are shrunk before upload: faster, and plenty for OCR
const RESIZE_OVER_BYTES = 2.5 * 1024 * 1024;
const BATCH_CONCURRENCY = 4;
const IMAGE_EXT = /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i;
const NO_CONNECTION = "The connection to the server failed.";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Build DOM safely (text is never parsed as HTML). */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const badge = (status) => el("span", { class: `badge ${status}` }, `${STATUS[status].icon} ${STATUS[status].word}`);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const isImage = (file) => file.type.startsWith("image/") || IMAGE_EXT.test(file.name);

// ---------------------------------------------------------------- tabs

function setupTabs() {
  const tabs = $$('[role="tab"]');
  const select = (tab, focus = true) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", on);
      t.tabIndex = on ? 0 : -1;
      $("#" + t.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
    history.replaceState(null, "", "#" + tab.id.replace("tab-", ""));
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (step) select(tabs[(i + step + tabs.length) % tabs.length]);
    });
  });
  const initial = tabs.find((t) => "#" + t.id.replace("tab-", "") === location.hash);
  if (initial) select(initial, false);
}

// ---------------------------------------------------------------- file pickers & drop zones

function setupPickers() {
  document.addEventListener("click", (e) => {
    const button = e.target.closest("[data-pick]");
    if (button) $("#" + button.dataset.pick).click();
  });
}

function setupDropzone(zone, onFiles) {
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { stop(e); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { stop(e); zone.classList.remove("dragging"); }));
  zone.addEventListener("drop", (e) => onFiles([...e.dataTransfer.files]));
}

/** Shrink big photos in the browser so uploads stay quick. Falls back to the original (e.g. TIFF). */
async function prepareImage(file) {
  const simple = /^image\/(jpeg|png|webp)$/.test(file.type);
  if (simple && file.size <= RESIZE_OVER_BYTES) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && simple && file.size <= 3 * RESIZE_OVER_BYTES) return file;
    const canvas = el("canvas", { width: Math.round(bitmap.width * scale), height: Math.round(bitmap.height * scale) });
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    return blob ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file; // the server can decode formats the browser cannot
  }
}

async function postVerify(files, application) {
  const form = new FormData();
  for (const f of files) form.append("images", f, f.name);
  form.append("application", JSON.stringify(application));
  let response;
  try {
    response = await fetch("/api/verify", { method: "POST", body: form });
  } catch {
    throw new Error(`${NO_CONNECTION} Make sure that your network connection operates. Then try again.`);
  }
  if (!response.ok) {
    let detail = `The server did not complete the check of this label (error ${response.status}). Try again.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(detail);
  }
  return response.json();
}

// ---------------------------------------------------------------- result view (shared)

function engineText(result) {
  return result.engines.some((e) => e.startsWith("vision")) ? "text scanner and AI reader" : "text scanner";
}

function renderDiff(diff) {
  const parts = [];
  for (const d of diff) {
    if (d.op === "equal") parts.push(d.text, " ");
    else if (d.op === "missing") parts.push(el("del", { title: "Required, but not on the label" }, d.text), " ");
    else if (d.op === "extra") parts.push(el("ins", { title: "On the label, but not in the required text" }, d.text), " ");
    else parts.push(el("del", { title: "Required text" }, d.expected), " ", el("ins", { title: "What the label says" }, d.found), " ");
  }
  return el("details", { class: "diff", open: true },
    el("summary", {}, "Compare with the required text"),
    el("p", { class: "diff-text" }, parts),
    el("p", { class: "diff-key" }, "Red text with a line through it is required, but it is not on the label. Yellow text is on the label, but it is not in the required statement."),
  );
}

function renderResult(container, result, files) {
  const checks = result.checks;
  const urls = files.map((f) => URL.createObjectURL(f));
  const views = urls.map((url, i) =>
    el("div", { class: "img-wrap", "data-image": i },
      el("img", { src: url, alt: `Label picture ${i + 1}`, onerror: (e) => { e.target.alt = "The browser cannot show this type of file."; } }),
      files.length > 1 ? el("span", { class: "img-tag" }, `Picture ${i + 1}`) : null,
    ));
  const caption = el("figcaption", {}, "Click a result to see where it is on the label.");

  const show = (check) => {
    $$(".hl", container).forEach((n) => n.remove());
    if (!check) { caption.textContent = "Click a result to see where it is on the label."; return; }
    for (const box of check.boxes) {
      const pad = 0.004;
      views[box.image]?.append(el("div", {
        class: `hl ${check.status}`,
        style: {
          left: `${(box.x - pad) * 100}%`, top: `${(box.y - pad) * 100}%`,
          width: `${(box.w + 2 * pad) * 100}%`, height: `${(box.h + 2 * pad) * 100}%`,
        },
      }));
    }
    caption.textContent = check.boxes.length
      ? `Highlighted: ${check.label}.`
      : `${check.label}: the tool cannot show a location. The text is not on the picture, or the AI reader read it.`;
  };

  let selected = null;
  const items = checks.map((check, i) => {
    const values = [];
    if (check.expected) values.push(el("dt", {}, check.field.startsWith("warning") ? "Required" : "Application"), el("dd", {}, check.expected));
    if (check.found && check.field !== "warning_text") values.push(el("dt", {}, "Label"), el("dd", {}, check.found));
    const item = el("li", { class: `check ${check.status}`, tabindex: 0, role: "button", "aria-pressed": "false" },
      el("div", { class: "check-head" }, badge(check.status), el("span", { class: "check-label" }, check.label)),
      el("p", { class: "check-msg" }, check.message),
      values.length ? el("dl", { class: "check-values" }, values) : null,
      check.diff && check.status !== "pass" ? renderDiff(check.diff) : null,
    );
    const choose = () => {
      selected = selected === i ? null : i;
      $$(".check", container).forEach((n, j) => {
        n.classList.toggle("selected", j === selected);
        n.setAttribute("aria-pressed", j === selected);
      });
      show(selected === null ? null : checks[selected]);
    };
    item.addEventListener("click", (e) => { if (!e.target.closest("summary")) choose(); });
    item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
    item.addEventListener("mouseenter", () => show(check));
    item.addEventListener("mouseleave", () => show(selected === null ? null : checks[selected]));
    return item;
  });

  const seconds = (result.elapsed_ms / 1000).toFixed(1);
  const heading = el("h2", { tabindex: -1 }, result.headline);
  const flagged = checks.map((c, i) => [c, i]).filter(([c]) => c.status !== "pass");
  const jumps = flagged.length
    ? el("p", { class: "jumps" }, "Examine: ", flagged.map(([c, i]) =>
        el("button", { type: "button", class: `jump ${c.status}`, onclick: () => {
          if (selected !== i) items[i].click();
          items[i].scrollIntoView({ block: "center" });
          items[i].focus({ preventScroll: true });
        } }, c.label)))
    : null;
  container.replaceChildren(...[
    el("div", { class: `verdict ${result.overall}` },
      el("div", { class: "verdict-icon", "aria-hidden": "true" }, STATUS[result.overall].icon),
      el("div", {}, heading, el("p", {}, `The ${engineText(result)} examined the label in ${seconds} seconds. The agent makes the final decision.`), jumps),
    ),
    result.notices.length ? el("ul", { class: "notices" }, result.notices.map((n) => el("li", {}, n))) : null,
    el("div", { class: "result-grid" },
      el("ol", { class: "checks", "aria-label": "Results" }, items),
      el("figure", { class: "label-view" }, views, caption),
    ),
  ].filter(Boolean));

  const first = checks.findIndex((c) => c.status !== "pass" && c.boxes.length);
  if (first >= 0) items[first].click();
  return heading;
}

// ---------------------------------------------------------------- one label

const single = { files: [], urls: [] };

function readApplication(form) {
  const data = Object.fromEntries(FIELDS.map((f) => [f, form.elements[f].value.trim()]));
  data.beverage_type = form.elements.beverage_type.value;
  return data;
}

function fillApplication(form, app) {
  for (const f of FIELDS) form.elements[f].value = app[f] || "";
  form.elements.beverage_type.value = app.beverage_type || "spirits";
}

function renderThumbs() {
  single.urls.forEach(URL.revokeObjectURL);
  single.urls = single.files.map((f) => URL.createObjectURL(f));
  const list = $("#single-thumbs");
  const tpl = $("#tpl-thumb");
  list.replaceChildren(...single.files.map((file, i) => {
    const node = tpl.content.firstElementChild.cloneNode(true);
    $("img", node).src = single.urls[i];
    $("img", node).alt = `Label picture ${i + 1}`;
    $(".thumb-name", node).textContent = file.name;
    $(".thumb-remove", node).setAttribute("aria-label", `Remove ${file.name}`);
    $(".thumb-remove", node).addEventListener("click", () => { single.files.splice(i, 1); renderThumbs(); });
    return node;
  }));
  if (single.files.length && single.files.length < 6) {
    list.append(el("li", {}, el("button", { type: "button", class: "thumb-add", "data-pick": "single-files" }, "+ Add a picture of the back label")));
  }
  $("#single-drop").classList.toggle("has-files", single.files.length > 0);
}

function addSingleFiles(files) {
  const images = files.filter(isImage);
  if (files.length && !images.length) return showSingleError("That file is not a picture. Choose a JPG or PNG picture of the label.");
  single.files.push(...images);
  single.files = single.files.slice(0, 6);
  hideSingleError();
  renderThumbs();
}

function showSingleError(message) {
  const box = $("#single-error");
  box.textContent = message;
  box.hidden = false;
}
function hideSingleError() { $("#single-error").hidden = true; }

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.replaceChildren(...(busy ? [el("span", { class: "spinner", "aria-hidden": "true" }), "Wait…"] : [label]));
}

async function loadSamples() {
  try {
    const response = await fetch("/api/samples");
    return response.ok ? await response.json() : [];
  } catch {
    return [];
  }
}

async function fetchFile(url, name) {
  const blob = await (await fetch(url)).blob();
  return new File([blob], name, { type: blob.type });
}

function setupSingle(samples) {
  const form = $("#single-form");
  const button = $("#check-btn");
  const resultBox = $("#single-result");

  $("#single-files").addEventListener("change", (e) => { addSingleFiles([...e.target.files]); e.target.value = ""; });
  setupDropzone($("#single-drop"), addSingleFiles);

  const select = $("#sample-select");
  const display = $("#sample-display");
  const showChoice = () => {
    const option = select.selectedOptions[0];
    display.textContent = option ? option.textContent : "";
    display.classList.toggle("placeholder", !select.value);
  };
  for (const s of samples) select.append(el("option", { value: s.id }, s.note));
  select.addEventListener("change", async () => {
    showChoice();
    const sample = samples.find((s) => s.id === select.value);
    if (!sample) return;
    single.files = await Promise.all(sample.files.map((f) => fetchFile(f.url, f.name)));
    renderThumbs();
    fillApplication(form, sample.application);
    resultBox.hidden = true;
    hideSingleError();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideSingleError();
    if (!single.files.length) {
      showSingleError("Add a picture of the label first.");
      $('[data-pick="single-files"]').focus();
      return;
    }
    setBusy(button, true);
    try {
      const prepared = await Promise.all(single.files.map(prepareImage));
      const result = await postVerify(prepared, readApplication(form));
      resultBox.hidden = false;
      const heading = renderResult(resultBox, result, prepared);
      resultBox.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      heading.focus({ preventScroll: true });
    } catch (err) {
      showSingleError(err.message);
    } finally {
      setBusy(button, false, "Check label");
    }
  });

  $("#clear-btn").addEventListener("click", () => {
    const type = form.elements.beverage_type.value;
    form.reset();
    form.elements.beverage_type.value = type;
    single.files = [];
    renderThumbs();
    select.value = "";
    showChoice();
    resultBox.hidden = true;
    hideSingleError();
    window.scrollTo({ top: 0 });
  });
}

// ---------------------------------------------------------------- CSV

const HEADER_ALIASES = {
  label_file: ["label_file", "label_files", "file", "filename", "file_name", "image", "images", "picture", "label", "label_image"],
  beverage_type: ["beverage_type", "type", "product_type", "beverage", "type_of_product"],
  brand_name: ["brand_name", "brand"],
  class_type: ["class_type", "class", "class_type_designation", "designation"],
  alcohol_content: ["alcohol_content", "alcohol", "abv", "alc"],
  net_contents: ["net_contents", "net", "volume", "contents", "size"],
  bottler: ["bottler", "bottler_name_address", "bottler_name_and_address", "producer", "name_and_address", "address"],
  country_of_origin: ["country_of_origin", "country", "origin"],
  application_id: ["application_id", "id", "ttb_id", "cola_id", "serial_number"],
};
const HEADER_LOOKUP = Object.fromEntries(Object.entries(HEADER_ALIASES).flatMap(([key, names]) => names.map((n) => [n, key])));

function parseCSV(text) {
  text = text.replace(/^﻿/, "");
  const firstLine = text.split(/\r?\n/, 1)[0];
  const delimiter = (firstLine.match(/;/g) || []).length > (firstLine.match(/,/g) || []).length ? ";" : ",";
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === delimiter) { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const [header, ...body] = rows.filter((r) => r.some((v) => v.trim()));
  if (!header) throw new Error("The spreadsheet is empty.");
  const keys = header.map((h) => HEADER_LOOKUP[h.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "")] || null);
  if (!keys.includes("label_file")) throw new Error("The spreadsheet must have a “label_file” column with the file name of each picture.");
  return {
    delimiter,
    rows: body.map((r) => Object.fromEntries(keys.map((k, i) => [k, (r[i] || "").trim()]).filter(([k]) => k))),
  };
}

const csvCell = (v) => (/[",\n\r]/.test(String(v)) ? `"${String(v).replaceAll('"', '""')}"` : String(v));

// ---------------------------------------------------------------- many labels

const batch = { files: new Map(), csv: null, csvName: "", items: [], running: false, stop: false, filter: "all", started: 0 };
const baseName = (path) => path.split(/[\\/]/).pop().trim().toLowerCase();

function addBatchFiles(files) {
  for (const file of files) {
    if (/\.csv$/i.test(file.name)) loadCSV(file);
    else if (isImage(file)) batch.files.set(baseName(file.name), file);
  }
  const count = $("#batch-file-count");
  count.hidden = batch.files.size === 0;
  count.textContent = `${plural(batch.files.size, "picture")} added.`;
  planBatch();
}

async function loadCSV(file) {
  const label = $("#batch-csv-name");
  label.hidden = false;
  try {
    batch.csv = parseCSV(await file.text());
    batch.csvName = file.name;
    label.textContent = `${file.name}: ${plural(batch.csv.rows.length, "application")}.`;
  } catch (err) {
    batch.csv = null;
    label.textContent = `${file.name}: ${err.message}`;
  }
  planBatch();
}

function planBatch() {
  const items = [];
  const used = new Set();
  const missing = [];
  if (batch.csv) {
    const separator = batch.csv.delimiter === ";" ? /[|]/ : /[;|]/;
    batch.csv.rows.forEach((row, n) => {
      const names = (row.label_file || "").split(separator).map((s) => s.trim()).filter(Boolean);
      const files = names.map((name) => batch.files.get(baseName(name))).filter(Boolean);
      names.forEach((name) => used.add(baseName(name)));
      const notFound = names.filter((name) => !batch.files.has(baseName(name)));
      if (notFound.length) missing.push(`Row ${n + 2}: ${notFound.join(", ")}`);
      const application = Object.fromEntries(FIELDS.map((f) => [f, row[f] || ""]));
      application.beverage_type = (row.beverage_type || "spirits").toLowerCase();
      items.push({
        name: names.join(" + ") || `Row ${n + 2}`, id: row.application_id || "", files, application, notFound,
        status: files.length ? "queued" : "error",
        error: files.length ? "" : (notFound.length ? `These pictures were not added: ${notFound.join(", ")}.` : "The label_file box is empty."),
      });
    });
  }
  const extras = [...batch.files.entries()].filter(([key]) => !used.has(key));
  for (const [, file] of extras) {
    items.push({ name: file.name, id: "", files: [file], application: {}, notFound: [], status: "queued", noRow: true });
  }
  batch.items = items;

  const plan = $("#batch-plan");
  const runnable = items.filter((i) => i.status === "queued").length;
  plan.hidden = items.length === 0;
  const parts = [`${plural(runnable, "label")} ${runnable === 1 ? "is" : "are"} ready.`];
  if (batch.csv && extras.length) parts.push(`${plural(extras.length, "picture")} ${extras.length === 1 ? "is" : "are"} not in the spreadsheet. For these, the tool examines only the warning, the alcohol content, and the net contents.`);
  if (!batch.csv && runnable) parts.push("If there is no spreadsheet, the tool examines only the government warning, the alcohol content, and the net contents.");
  $("#batch-summary").textContent = parts.join(" ");
  const details = $("#batch-unmatched");
  details.hidden = missing.length === 0;
  $("summary", details).textContent = `${plural(missing.length, "row")} ${missing.length === 1 ? "has" : "have"} the names of pictures that you did not add`;
  $("ul", details).replaceChildren(...missing.map((m) => el("li", {}, m)));

  const start = $("#batch-start");
  start.disabled = runnable === 0 || batch.running;
  start.textContent = runnable ? `Check ${plural(runnable, "label")}` : "Check the labels";
}

function itemSummary(item) {
  if (item.status === "error") return item.error;
  if (item.status === "queued") return batch.running ? "Not started." : (item.noRow ? "No row in the spreadsheet. The tool examines only the warning, alcohol content, and net contents." : "Ready.");
  if (item.status === "running") return "In progress.";
  const problems = item.result.checks.filter((c) => c.status !== "pass").map((c) => c.label);
  return problems.length ? `${item.result.headline}: ${problems.join(", ")}` : item.result.headline;
}

function renderBatchItem(item) {
  const brand = item.application.brand_name;
  const row = el("button", { type: "button", class: "batch-row", disabled: !item.result, "aria-expanded": "false" },
    badge(item.status),
    el("span", {}, el("span", { class: "name" }, brand || item.name), el("br"),
      el("span", { class: "sub" }, (brand ? `${item.name} · ` : "") + itemSummary(item))),
    item.result ? el("span", { class: "chev", "aria-hidden": "true" }, "›") : el("span"),
  );
  const node = el("li", { class: `batch-item ${item.status}` }, row);
  row.addEventListener("click", async () => {
    const open = node.toggleAttribute("open");
    row.setAttribute("aria-expanded", open);
    $(".batch-detail", node)?.remove();
    if (!open) return;
    const detail = el("div", { class: "batch-detail" }, el("p", {}, "Wait…"));
    node.append(detail);
    renderResult(detail, item.result, await Promise.all(item.files.map(prepareImage)));
  });
  node.hidden = batch.filter !== "all" && batch.filter !== item.status;
  item.node?.replaceWith(node);
  item.node = node;
  return node;
}

function updateBatchCounts() {
  const done = batch.items.filter((i) => i.result || i.status === "error");
  const count = (s) => batch.items.filter((i) => i.status === s).length;
  for (const chip of $$(".chip")) {
    const f = chip.dataset.filter;
    $("span", chip).textContent = `(${f === "all" ? batch.items.length : count(f)})`;
  }
  const total = batch.items.length;
  const pct = total ? Math.round((done.length / total) * 100) : 0;
  $("#batch-bar").style.width = `${pct}%`;
  $(".progress").setAttribute("aria-valuenow", pct);
  const checked = batch.items.filter((i) => i.result).length;
  let text = `${checked} of ${total} labels examined`;
  const tally = [`${count("fail")} with problems`, `${count("review")} to examine`, `${count("pass")} OK`];
  text += ` · ${tally.join(" · ")}`;
  if (batch.running && checked) {
    const perLabel = (performance.now() - batch.started) / checked;
    const left = Math.round((perLabel * (total - done.length)) / 1000);
    if (left > 0) text += ` · approximately ${left < 60 ? plural(left, "second") : plural(Math.ceil(left / 60), "minute")} to go`;
  } else if (!batch.running) {
    text = (batch.stop ? "The check stopped. " : "The check is completed. ") + text;
  }
  $("#batch-progress-text").textContent = text;
  $("#batch-export").disabled = checked === 0;
}

async function runItem(item) {
  item.status = "running";
  renderBatchItem(item);
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const prepared = await Promise.all(item.files.map(prepareImage));
      item.result = await postVerify(prepared, item.application);
      item.status = item.result.overall;
      if (item.notFound.length) item.result.notices.unshift(`These pictures were not added: ${item.notFound.join(", ")}.`);
      break;
    } catch (err) {
      item.status = "error";
      item.error = err.message;
      if (err.message.startsWith(NO_CONNECTION)) await new Promise((r) => setTimeout(r, 1000));
      else break;
    }
  }
  renderBatchItem(item);
  updateBatchCounts();
}

async function startBatch() {
  batch.running = true;
  batch.stop = false;
  batch.started = performance.now();
  $("#batch-start").disabled = true;
  $("#batch-sample").hidden = true;
  $("#batch-reset").hidden = false;
  $("#batch-stop").hidden = false;
  $("#batch-progress").hidden = false;
  $("#batch-results").hidden = false;
  $("#batch-list").replaceChildren(...batch.items.map(renderBatchItem));
  updateBatchCounts();

  const queue = batch.items.filter((i) => i.status === "queued");
  const worker = async () => {
    while (!batch.stop && queue.length) await runItem(queue.shift());
  };
  await Promise.all(Array.from({ length: BATCH_CONCURRENCY }, worker));
  batch.running = false;
  $("#batch-stop").hidden = true;
  batch.items.filter((i) => i.status === "queued").forEach((i) => { i.status = "error"; i.error = "No result. You stopped the check."; renderBatchItem(i); });
  updateBatchCounts();
}

function exportBatch() {
  const header = ["application_id", "label_file", "brand_name", "result", "summary", "problems", "examine", "seconds"];
  const lines = [header.join(",")];
  for (const item of batch.items) {
    const checks = item.result?.checks || [];
    const describe = (status) => checks.filter((c) => c.status === status).map((c) => `${c.label}: ${c.message}`).join(" | ");
    lines.push([
      item.id, item.name, item.application.brand_name || "",
      item.result ? { pass: "OK", review: "Examine", fail: "Problem" }[item.result.overall] : "No result",
      item.result ? item.result.headline : item.error || "",
      describe("fail"), describe("review"),
      item.result ? (item.result.elapsed_ms / 1000).toFixed(1) : "",
    ].map(csvCell).join(","));
  }
  const blob = new Blob([lines.join("\r\n")], { type: "text/csv" });
  const link = el("a", { href: URL.createObjectURL(blob), download: `label-check-results-${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.append(link);
  link.click();
  link.remove();
}

function setupBatch(samples) {
  $("#batch-files").addEventListener("change", (e) => { addBatchFiles([...e.target.files]); e.target.value = ""; });
  $("#batch-folder").addEventListener("change", (e) => { addBatchFiles([...e.target.files]); e.target.value = ""; });
  $("#batch-csv").addEventListener("change", (e) => { if (e.target.files[0]) loadCSV(e.target.files[0]); e.target.value = ""; });
  setupDropzone($("#batch-drop"), addBatchFiles);
  $("#batch-start").addEventListener("click", startBatch);
  $("#batch-stop").addEventListener("click", () => { batch.stop = true; $("#batch-stop").disabled = true; });
  $("#batch-export").addEventListener("click", exportBatch);
  $("#batch-reset").addEventListener("click", () => location.reload());

  $("#batch-sample").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    button.textContent = "Wait…";
    const files = await Promise.all(samples.flatMap((s) => s.files).map((f) => fetchFile(f.url, f.name)));
    const csv = await fetchFile("/samples/batch_applications.csv", "batch_applications.csv");
    addBatchFiles(files);
    await loadCSV(csv);
    button.textContent = "Sample labels added";
  });

  for (const chip of $$(".chip")) {
    chip.addEventListener("click", () => {
      batch.filter = chip.dataset.filter;
      $$(".chip").forEach((c) => c.setAttribute("aria-pressed", c === chip));
      for (const item of batch.items) if (item.node) item.node.hidden = batch.filter !== "all" && batch.filter !== item.status;
    });
  }
}

// ---------------------------------------------------------------- start

async function main() {
  setupTabs();
  setupPickers();
  const samples = await loadSamples();
  setupSingle(samples);
  setupBatch(samples);
  try {
    const config = await (await fetch("/api/config")).json();
    $("#engine-note").textContent = config.vision !== "off"
      ? ` The text scanner on this server reads the labels. An AI reader (${config.vision_model}) helps with labels that are hard to read.`
      : " The text scanner on this server reads the labels. It does not use outside services.";
  } catch { /* footer note is optional */ }
}

main();
