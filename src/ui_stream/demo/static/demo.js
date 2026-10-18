"use strict";

const byId = (id) => document.getElementById(id);
const descriptions = {
  normal: "逐条输出进度，每 3 条保存一次快照，最终结果提交后发送结束通知。",
  business_error: "第 6 步产生业务异常；快照、失败终态和错误结果在同一次提交中保存。",
  live_failure: "第 4–7 步模拟实时发布失败；计算与快照继续，最终结果仍可成功。",
  checkpoint_failure: "首次中间快照写入失败，缓冲保留；下一批恢复后继续保存，完整性标为降级。",
  commit_failure: "首次最终提交失败，不提前结束；约 1 秒后用同一执行重试提交。",
};
const statuses = {RUNNING: "运行中", DONE: "已完成", ERROR: "业务失败", INTERRUPTED: "已中断"};
const integrityLabels = {COMPLETE: "完整", DEGRADED: "降级", TRUNCATED: "截断"};
const pendingKey = "ui-stream-demo-pending";
let generation = 0;
let source = null;
let pollTimer = null;
let reconnectTimer = null;
let activeId = null;
let lastStep = 0;
let running = false;
let paused = false;
let creating = false;
let pending = null;

function notice(message = "") {
  byId("notice").textContent = message;
  byId("notice").hidden = !message;
}

function buttons() {
  byId("create").disabled = creating || running;
  byId("create").textContent = creating ? "正在创建…" : pending ? "重试创建" : "创建演示任务";
  byId("scenario").disabled = creating || running || Boolean(pending);
  byId("disconnect").disabled = !source;
  byId("reconnect").disabled = !activeId || !paused;
}

function clearConnection() {
  if (source) source.close();
  source = null;
  clearTimeout(pollTimer);
  clearTimeout(reconnectTimer);
  pollTimer = reconnectTimer = null;
}

async function request(path, options) {
  const response = await fetch(path, {cache: "no-store", ...options});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "请求未完成，请重试。");
    error.status = response.status;
    throw error;
  }
  return data;
}

function progress(event, restored = false, reset = false) {
  if (reset) {
    lastStep = 0;
    byId("events").replaceChildren();
    byId("progress").value = 0;
    byId("progress-label").textContent = "0 / 12";
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "尚无已保存进度，等待实时事件。";
    byId("events").append(empty);
  }
  if (!event || !Number.isInteger(event.step) || event.step <= lastStep) return;
  if (byId("events").querySelector(".empty")) byId("events").replaceChildren();
  lastStep = event.step;
  const row = document.createElement("li");
  row.className = restored ? "restored" : "";
  row.textContent = `${restored ? "快照恢复" : "实时到达"}  ${event.step} / ${event.total}  ${event.message}`;
  byId("events").append(row);
  while (byId("events").children.length > 20) byId("events").firstElementChild.remove();
  byId("progress").value = lastStep;
  byId("progress-label").textContent = `${lastStep} / ${event.total}`;
}

function renderSaved(job) {
  running = job.status === "RUNNING";
  byId("job-status").textContent = statuses[job.status] || job.status;
  byId("integrity").textContent = integrityLabels[job.integrity] || job.integrity;
  byId("job-id").textContent = `任务 ${job.id} / 执行 ${job.execution_id}`;
  byId("scenario").value = job.scenario;
  byId("scenario-description").textContent = descriptions[job.scenario];
  byId("snapshot").textContent = job.snapshot ? JSON.stringify(job.snapshot, null, 2) : "尚未到达保存阈值";
  byId("snapshot-label").textContent = job.snapshot ? `最新已保存：第 ${job.snapshot.step} 步；游标 ${job.cursor}` : "每秒读取一次，只保存最新进度。";
  byId("result").textContent = job.result ? JSON.stringify(job.result, null, 2) : job.status === "INTERRUPTED" ? "未提交最终结果。可以创建新任务。" : "任务结束后显示已提交的结果。";
  if (job.warning) notice(job.warning);
  if (!running) {
    clearConnection();
    byId("connection-status").textContent = "已结束";
    if (job.snapshot && job.snapshot.step > lastStep) progress(job.snapshot, true);
  }
  buttons();
}

function schedulePoll(gen) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => poll(gen), 1000);
}

async function recoverClosedProducer(gen, jobId) {
  if (gen !== generation) return;
  clearConnection();
  paused = true;
  byId("connection-status").textContent = "生产者已停止，终态尚未确认";
  buttons();
  try {
    const job = await request(`/api/jobs/${jobId}`);
    if (gen !== generation) return;
    renderSaved(job);
    if (running) notice("生产者已停止，SQLite 终态尚未确认。保留当前快照并定时回查；可手动恢复连接，或重启演示服务收敛状态。");
  } catch (error) {
    if (gen !== generation) return;
    notice(`生产者已停止，快照暂时无法读取：${error.message}`);
  }
  if (gen === generation && running) schedulePoll(gen);
}

async function poll(gen) {
  if (gen !== generation || !activeId) return;
  try {
    const job = await request(`/api/jobs/${activeId}`);
    if (gen !== generation) return;
    renderSaved(job);
  } catch (error) {
    if (gen !== generation) return;
    notice(`快照暂时无法读取：${error.message}`);
  }
  if (gen === generation && running) schedulePoll(gen);
}

async function finish(gen) {
  if (gen !== generation) return;
  if (source) source.close();
  source = null;
  await poll(gen);
}

function connect(gen, job) {
  const stream = new EventSource(`/api/jobs/${job.id}/events?after=${job.cursor}`);
  source = stream;
  byId("connection-status").textContent = "连接中";
  stream.onopen = () => {
    if (gen !== generation || source !== stream) return;
    byId("connection-status").textContent = "已连接";
  };
  stream.addEventListener("progress", (message) => {
    if (gen !== generation || source !== stream) return;
    progress(JSON.parse(message.data));
  });
  stream.addEventListener("finished", () => finish(gen));
  stream.addEventListener("state", (message) => {
    if (gen !== generation || source !== stream) return;
    renderSaved(JSON.parse(message.data));
  });
  stream.addEventListener("reset", () => {
    if (gen === generation && source === stream) restore(job.id);
  });
  stream.addEventListener("sync", () => {
    if (gen === generation && source === stream) recoverClosedProducer(gen, job.id);
  });
  stream.onerror = () => {
    if (gen !== generation || source !== stream) return;
    stream.close();
    source = null;
    paused = true;
    byId("connection-status").textContent = "连接中断，正在恢复快照";
    buttons();
    reconnectTimer = setTimeout(() => { if (gen === generation) restore(job.id); }, 1000);
  };
  buttons();
}

async function restore(jobId) {
  const gen = ++generation;
  clearConnection();
  activeId = jobId;
  paused = false;
  byId("connection-status").textContent = "读取已保存快照";
  try {
    const job = await request(`/api/jobs/${jobId}`);
    if (gen !== generation) return;
    progress(job.snapshot, true, true);
    renderSaved(job);
    if (running) {
      connect(gen, job);
      schedulePoll(gen);
    }
  } catch (error) {
    if (gen !== generation) return;
    running = false;
    paused = true;
    byId("connection-status").textContent = "未连接";
    notice(`无法恢复任务：${error.message} 点击“恢复连接”重试，或创建新任务。`);
    buttons();
  }
}

function remember(value) {
  pending = value;
  try {
    if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value));
    else sessionStorage.removeItem(pendingKey);
  } catch (_) { /* The in-memory token still makes repeated clicks idempotent. */ }
}

async function createJob() {
  if (creating || running) return;
  creating = true;
  notice();
  if (!pending) remember({request_token: crypto.randomUUID(), scenario: byId("scenario").value});
  buttons();
  try {
    const job = await request("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pending)});
    const url = new URL(location.href);
    url.searchParams.set("job", job.id);
    history.replaceState(null, "", url);
    remember(null);
    await restore(job.id);
  } catch (error) {
    if (error.status === 400 || error.status === 409) remember(null);
    notice(`创建未确认：${error.message} 再次点击会重试同一请求。`);
  } finally {
    creating = false;
    buttons();
  }
}

byId("create-form").addEventListener("submit", (event) => { event.preventDefault(); createJob(); });
byId("scenario").addEventListener("change", () => { byId("scenario-description").textContent = descriptions[byId("scenario").value]; });
byId("disconnect").addEventListener("click", () => {
  const gen = ++generation;
  clearConnection();
  paused = true;
  byId("connection-status").textContent = "已手动断开，任务继续运行";
  schedulePoll(gen);
  buttons();
});
byId("reconnect").addEventListener("click", () => { if (activeId) restore(activeId); });
window.addEventListener("pagehide", () => { ++generation; clearConnection(); });
const jobId = new URL(location.href).searchParams.get("job");
try { pending = JSON.parse(sessionStorage.getItem(pendingKey)); } catch (_) { pending = null; }
if (pending && typeof pending.request_token === "string" && descriptions[pending.scenario]) {
  byId("scenario").value = pending.scenario;
  createJob();
} else if (jobId) { remember(null); restore(jobId); }
else { remember(null); buttons(); }
