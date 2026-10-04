// Execute the actual nested package-card renderer with a minimal text-safe DOM.
// No browser/window initialization and no network or button clicks.
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../webapp/static/app.js"), "utf8");
const recordMode = process.argv[2] === "record";
const start = source.indexOf(recordMode ? "function runRecordRender(" : "  function renderVmpPkg(cd) {");
const end = recordMode ? source.indexOf("\n/* Живой блок записи", start)
  : source.indexOf('\n  api("/task-package/current").then(renderVmpPkg)', start);
if (start < 0 || end < 0) throw Error("Actual package card renderer not found");
class Element {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag; this.attrs = attrs; this.children = [];
    this._text = attrs.text == null ? "" : String(attrs.text);
    children.forEach(c => this.appendChild(c));
  }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent || "").join("\n"); }
  set innerHTML(_) { throw Error("Projection display must not inject HTML"); }
  addEventListener() {}
}
const el = (tag, attrs, children) => new Element(tag, attrs, children);
const host = el("div");
const ctx = {
  pkgBody: host, el, t: x => x, fmt: x => x == null ? "—" : String(x),
  fmtTime: x => String(x),
  pkgRow: (label, value) => el("div", {}, [el("span", {text:label}), el("span", {text:value})]),
  pkgVerdictChip: x => el("span", {text:x}), cosmosNoteWords: (_, e) => e,
  vmpRunBlock() {}, vmpNetBlock() {},
  _runRecDeclare() {}, _runRecStitch() {}, _runRecHead() {},
  _runRecJournal() {}, _runRecResults() {}, _runRecFoot() {},
  api() { throw Error("No network allowed"); }
};
const helperStart = source.indexOf("function vmpProjectionBlock(");
let helper = "";
if (helperStart >= 0) {
  const helperEnd = source.indexOf("\nfunction ", helperStart + 1);
  if (helperEnd < 0) throw Error("Projection helper boundary not found");
  helper = source.slice(helperStart, helperEnd);
}
vm.runInNewContext(helper + "\n" + source.slice(start, end) +
                   (recordMode ? "\nrunRecordRender(pkgBody," : "\nrenderVmpPkg(") +
                   JSON.stringify(JSON.parse(fs.readFileSync(0, "utf8"))) + ");", ctx,
                   {timeout: 3000});
process.stdout.write(host.textContent);
