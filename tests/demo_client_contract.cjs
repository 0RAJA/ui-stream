"use strict";
// Controlled DOM/transport fixtures exercise the real client script, not a second reducer.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const [scriptPath, scenario] = process.argv.slice(2);
const oldId = "00000000-0000-4000-8000-000000000001";
const newId = "00000000-0000-4000-8000-000000000002";
const token = "00000000-0000-4000-8000-000000000003";

class Element {
  constructor() {
    this.value = "normal";
    this.textContent = "";
    this.children = [];
    this.listeners = new Map();
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  replaceChildren(...children) { this.children = children; }
  append(child) { child.parent = this; this.children.push(child); }
  querySelector(selector) { return selector === ".empty" ? this.children.find(child => child.className === "empty") : null; }
  get firstElementChild() { return this.children[0]; }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}

async function main() {
  const elements = new Map();
  const requests = [];
  const streams = [];
  const timers = new Map();
  const storage = new Map();
  let timerId = 0;
  const pending = {request_token: token, scenario: "normal"};
  if (scenario === "pending_with_old_url") storage.set("ui-stream-demo-pending", JSON.stringify(pending));
  const currentId = scenario === "pending_with_old_url" ? oldId : newId;
  const location = {href: `http://127.0.0.1:8765/?job=${currentId}`};
  const job = (id) => ({
    id, execution_id: id, scenario: "normal", status: id === oldId ? "DONE" : "RUNNING",
    integrity: "COMPLETE", snapshot: null, cursor: 0, result: null, warning: "",
  });
  class Source {
    constructor(url) { this.url = url; this.listeners = new Map(); this.closed = false; streams.push(this); }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    close() { this.closed = true; }
    emit(name) { const listener = this.listeners.get(name); if (listener) listener({data: "{}"}); }
  }
  const context = vm.createContext({
    document: {
      getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
      createElement() { return new Element(); },
    },
    window: {addEventListener() {}},
    location,
    history: {replaceState(_state, _title, url) { location.href = String(url); }},
    sessionStorage: {
      getItem(key) { return storage.get(key) || null; },
      setItem(key, value) { storage.set(key, value); },
      removeItem(key) { storage.delete(key); },
    },
    crypto: {randomUUID() { throw new Error("must reuse the pending request token"); }},
    EventSource: Source,
    URL,
    console,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url, options = {}) {
      const method = options.method || "GET";
      requests.push({url, method, body: options.body});
      const data = method === "POST" ? job(newId) : job(url.endsWith(oldId) ? oldId : newId);
      return {ok: true, status: method === "POST" ? 200 : 200, json: async () => data};
    },
  });
  vm.runInContext(fs.readFileSync(scriptPath, "utf8"), context, {filename: "demo.js"});
  const flush = async () => { for (let index = 0; index < 4; ++index) await new Promise(setImmediate); };
  await flush();
  if (scenario === "pending_with_old_url") {
    const posts = requests.filter(item => item.method === "POST");
    assert.equal(posts.length, 1, "refresh must confirm the pending creation before restoring the old URL");
    assert.deepEqual(JSON.parse(posts[0].body), pending);
    assert.equal(new URL(location.href).searchParams.get("job"), newId);
    assert.equal(storage.has("ui-stream-demo-pending"), false);
    assert.equal(streams.length, 1);
    assert.match(streams[0].url, new RegExp(newId));
  } else if (scenario === "closed_producer") {
    assert.equal(streams.length, 1);
    streams[0].emit("sync");
    await flush();
    assert.equal(streams.length, 1, "a closed producer with RUNNING snapshot must not immediately reopen SSE");
    assert.equal(streams[0].closed, true);
    assert.match(elements.get("connection-status").textContent, /终态尚未确认/);
    assert.equal(elements.get("job-status").textContent, "运行中");
    assert.equal(timers.size, 1, "only one controlled snapshot poll may remain");
    assert.ok([...timers.values()].every(item => item.delay >= 1000));
  } else {
    throw new Error(`Unknown client contract: ${scenario}`);
  }
  console.log(`${scenario}: OK`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
