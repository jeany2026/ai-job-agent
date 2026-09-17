const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED = [".pdf", ".doc", ".docx", ".txt", ".md"];
const KIND_LABELS = {
  resume: "简历",
  project_document: "项目资料",
  other_attachment: "其他材料",
};
const CONTINUE_FEEDBACK = "我已完成，继续";

const form = document.getElementById("entry-form");
const messageInput = document.getElementById("message-input");
const fileInput = document.getElementById("file-input");
const attachToggle = document.getElementById("attach-toggle");
const attachOptions = document.getElementById("attach-options");
const attachList = document.getElementById("attach-list");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const formNote = document.getElementById("form-note");
const runPanel = document.getElementById("run-panel");
const runTitle = document.getElementById("run-title");
const runMessage = document.getElementById("run-message");
const runActions = document.getElementById("run-actions");
const resultPanel = document.getElementById("result-panel");
const chatPanel = document.getElementById("chat-panel");
const chatLog = document.getElementById("chat-log");
const stageItems = [...document.querySelectorAll("#stage-list li")];
const profileEmpty = document.getElementById("profile-empty");
const profileBody = document.getElementById("profile-body");
const profileMeta = document.getElementById("profile-meta");

let pollTimer = null;
let pendingKind = "other_attachment";
let attachments = [];
let isRunning = false;
let conversationId = null;
let lastUserMessage = "";

attachToggle.addEventListener("click", () => {
  if (isRunning) return;
  attachOptions.hidden = !attachOptions.hidden;
});

document.addEventListener("click", (event) => {
  if (!attachToggle.contains(event.target) && !attachOptions.contains(event.target)) {
    attachOptions.hidden = true;
  }
});

attachOptions.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => {
    pendingKind = button.dataset.kind || "other_attachment";
    attachOptions.hidden = true;
    fileInput.click();
  });
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
  fileInput.value = "";
  hideError();
  if (!file) return;
  const error = validateFile(file);
  if (error) {
    showError(error);
    return;
  }
  attachments.push({ file, kind: pendingKind });
  pendingKind = "other_attachment";
  renderAttachments();
  syncSubmitState();
});

messageInput.addEventListener("input", syncSubmitState);

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!submitBtn.disabled) form.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendMessage(messageInput.value.trim());
});

loadProfile();

async function sendMessage(rawMessage, { fromShortcut = false } = {}) {
  hideError();
  hideNote();

  const message = String(rawMessage || "").trim();
  const localError = validateLocal(message);
  if (localError) {
    showError(localError);
    syncSubmitState();
    return;
  }

  appendChat("user", message || "（已附材料）");
  lastUserMessage = message;
  messageInput.value = "";
  const pendingAttachments = attachments.slice();
  attachments = [];
  renderAttachments();
  syncSubmitState();

  const body = new FormData();
  body.append("message", message);
  if (conversationId) body.append("conversation_id", conversationId);
  pendingAttachments.forEach((item) => {
    body.append("attachments", item.file);
    body.append("attachment_kinds", item.kind);
  });

  setRunning(true, "正在处理……", fromShortcut ? "已收到你的确认，正在继续。" : "请稍候，我正在理解你这一次的输入。");
  clearStages();
  resultPanel.hidden = true;
  resultPanel.innerHTML = "";

  try {
    const started = await request("/api/run-agent", { method: "POST", body });
    if (started.conversation_id) conversationId = started.conversation_id;
    if (!started.ok && !started.run_id) {
      throw Object.assign(new Error(started.message || "启动失败"), started);
    }
    await pollRun(started.run_id);
  } catch (err) {
    setRunning(false, "这次没有完成", err.message || "请求失败。");
    appendChat("agent", err.message || "请求失败。");
    showError(err.message || "请求失败。");
  }
}

function validateFile(file) {
  const suffix = extensionOf(file.name);
  if (!ALLOWED.includes(suffix)) return "附件仅支持 PDF / DOC / DOCX / TXT / MD。";
  if (file.size === 0) return "附件是空的。";
  if (file.size > MAX_BYTES) return "附件不能超过 10MB。";
  return "";
}

function validateLocal(message) {
  if (!String(message || "").trim() && !attachments.length) {
    return "请告诉我你想让我帮你做什么，也可以补充你的经历或上传材料。";
  }
  return "";
}

function renderAttachments() {
  if (!attachments.length) {
    attachList.hidden = true;
    attachList.innerHTML = "";
    return;
  }
  attachList.hidden = false;
  attachList.innerHTML = attachments
    .map(
      (item, index) => `
        <li class="attach-item">
          <span>${escapeHtml(item.file.name)}</span>
          <span class="attach-kind">${escapeHtml(KIND_LABELS[item.kind] || "材料")}</span>
          <button type="button" data-index="${index}">移除</button>
        </li>`
    )
    .join("");
  attachList.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      if (isRunning) return;
      attachments.splice(Number(button.dataset.index), 1);
      renderAttachments();
      syncSubmitState();
    });
  });
}

function syncSubmitState() {
  const ready = !isRunning && !validateLocal(messageInput.value);
  submitBtn.disabled = !ready;
  submitBtn.textContent = isRunning ? "正在处理…" : "发送";
  attachToggle.disabled = isRunning;
  messageInput.disabled = isRunning;
}

async function pollRun(runId) {
  stopPolling();
  await new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const data = await request(`/api/run-agent/${encodeURIComponent(runId)}`);
        if (data.conversation_id) conversationId = data.conversation_id;
        renderProgress(data);
        if (data.run_phase === "finished" || isTerminal(data.status)) {
          stopPolling();
          renderFinished(data);
          resolve(data);
          return;
        }
        pollTimer = window.setTimeout(tick, 1000);
      } catch (err) {
        stopPolling();
        reject(err);
      }
    };
    tick();
  });
}

function renderProgress(data) {
  runPanel.hidden = false;
  if (data.run_phase !== "finished") {
    runTitle.textContent = stageTitle(data.status) || "正在理解你的需求……";
    runMessage.textContent = data.message || "请稍候。";
  }
  paintStages(data.status, data.stats);
}

function renderFinished(data) {
  setRunning(false);
  clearRunActions();
  paintStages(data.status, data.stats);
  if (data.profile && data.profile.exists) renderProfile(data.profile, data.profile);
  else loadProfile();
  if (data.profile_updated) {
    showNote("已加入你的候选人画像");
  }
  if (data.status === "NEEDS_HUMAN") {
    const infra = data.error_code === "browser_unavailable" || data.error_code === "cdp_unavailable";
    const title = infra ? "招聘浏览环境暂不可用" : "需要你处理一下";
    const message =
      data.message || (infra ? "招聘浏览环境暂不可用，请重试。" : "需要人工处理后才能继续。");
    runTitle.textContent = title;
    runMessage.textContent = message;
    appendChat("gate", message, { continueButton: !infra });
    resultPanel.hidden = false;
    resultPanel.innerHTML = `
      <div class="panel banner warn">
        <p>${escapeHtml(message)}</p>
        ${
          infra
            ? ""
            : `<div class="gate-actions"><button type="button" class="continue-btn" data-continue="1">我已完成，继续</button></div>`
        }
      </div>`;
    bindContinueButtons(resultPanel);
    if (!infra) showRunContinue();
    return;
  }
  if (data.error_code === "GOAL_INCOMPLETE" || data.clarification_needed) {
    const message = data.message || "请再说明一下。";
    runTitle.textContent = data.error_code === "GOAL_INCOMPLETE" ? "还需要求职目标" : "请再说明一下";
    runMessage.textContent = message;
    appendChat("agent", message);
    resultPanel.hidden = false;
    resultPanel.innerHTML = `<div class="panel banner prompt">${escapeHtml(message)}</div>`;
    return;
  }
  if (data.error_code === "insufficient_candidate" || data.error_code === "INSUFFICIENT_INFORMATION") {
    runTitle.textContent = "匹配还缺少经历信息";
    runMessage.textContent = data.message;
    appendChat("agent", data.message || "匹配还缺少经历信息。");
    resultPanel.hidden = false;
    resultPanel.innerHTML = `<div class="panel banner prompt">${escapeHtml(data.message)}</div>`;
    return;
  }
  if (data.error_code === "PARSER_ERROR" || data.error_code === "ATTACHMENT_PROCESSING_FAILED") {
    const message = data.message || "附件没能正确读出文字，请换一份可读的简历或直接描述经历。";
    runTitle.textContent = "附件没能读出来";
    runMessage.textContent = message;
    appendChat("agent", message);
    resultPanel.hidden = false;
    resultPanel.innerHTML = `<div class="panel banner warn">${escapeHtml(message)}</div>`;
    return;
  }
  if (data.error_code === "SCHEMA_ERROR") {
    const message = data.message || "有一条候选人信息没能规范化，已跳过该字段。";
    runTitle.textContent = "有一条信息需要再确认";
    runMessage.textContent = message;
    appendChat("agent", message);
    resultPanel.hidden = false;
    resultPanel.innerHTML = `<div class="panel banner prompt">${escapeHtml(message)}</div>`;
    return;
  }
  if (isLlmServiceFailure(data)) {
    renderLlmRetry(data);
    return;
  }
  if (data.status === "FAILED" || data.ok === false) {
    const message = data.message || "这次没有完成。";
    // Message may still instruct 「重试」 even if error_code is missing on an older payload.
    if (looksLikeLlmFailureMessage(message)) {
      renderLlmRetry({ ...data, message });
      return;
    }
    runTitle.textContent = "这次没有完成";
    runMessage.textContent = message;
    appendChat("agent", message);
    resultPanel.hidden = false;
    resultPanel.innerHTML = `<div class="panel banner warn"><p>${escapeHtml(message)}</p></div>`;
    showError(message);
    return;
  }
  if (data.status === "WAITING_USER") {
    const message = data.message || "找到一个值得你决定的职位。";
    runTitle.textContent = "需要你的决定";
    runMessage.textContent = message;
    appendChat("agent", message);
  } else if (data.status === "DONE" && !(data.recommended || []).length) {
    runTitle.textContent = data.result_title || "暂无推荐职位";
    runMessage.textContent = data.message || "这一轮还没有可展示的推荐职位。";
    appendChat("agent", runMessage.textContent);
  } else {
    runTitle.textContent = data.result_title || "推荐结果";
    runMessage.textContent =
      data.message || "下面是这次匹配的结果，包括为什么匹配、哪里可以迁移、以及主要缺口。";
    appendChat("agent", runMessage.textContent);
  }
  renderJobs(data.recommended || [], data);
}

function isLlmServiceFailure(data) {
  const code = data && data.error_code;
  return code === "llm_error" || code === "llm_unavailable" || code === "llm_invalid_json";
}

function looksLikeLlmFailureMessage(message) {
  const text = String(message || "");
  return (
    text.includes("模型服务") ||
    text.includes("模型返回内容格式无效") ||
    (text.includes("「重试」") && text.includes("不是登录"))
  );
}

function renderLlmRetry(data) {
  const code = data && data.error_code;
  const message =
    (data && data.message) ||
    (code === "llm_unavailable"
      ? "模型服务暂时不可用，请稍后点「重试」。这不是登录或人工验证问题。"
      : code === "llm_invalid_json"
        ? "模型返回内容格式无效（不是可用的 JSON），请点「重试」。服务本身可达，这不是登录问题。"
        : "模型服务暂时出错，请稍后点「重试」。这不是登录或人工验证问题。");
  runTitle.textContent =
    code === "llm_invalid_json" ? "模型输出格式无效" : "模型服务暂时不可用";
  runMessage.textContent = message;
  appendChat("agent", message);
  resultPanel.hidden = false;
  resultPanel.innerHTML = `
    <div class="panel banner warn">
      <p>${escapeHtml(message)}</p>
      <div class="gate-actions">
        <button type="button" class="continue-btn" data-llm-retry="1">重试</button>
      </div>
    </div>`;
  bindLlmRetryButtons(resultPanel);
  showRunRetry();
  hideError();
}

function clearRunActions() {
  if (!runActions) return;
  runActions.hidden = true;
  runActions.innerHTML = "";
}

function showRunRetry() {
  if (!runActions) return;
  runActions.hidden = false;
  runActions.innerHTML = `<button type="button" class="continue-btn" data-llm-retry="1">重试</button>`;
  bindLlmRetryButtons(runActions);
}

function showRunContinue() {
  if (!runActions) return;
  runActions.hidden = false;
  runActions.innerHTML = `<button type="button" class="continue-btn" data-continue="1">我已完成，继续</button>`;
  bindContinueButtons(runActions);
}

function appendChat(role, text, { continueButton = false } = {}) {
  if (!chatLog || !text) return;
  if (chatPanel) chatPanel.hidden = false;
  const item = document.createElement("li");
  item.className = `chat-item ${role === "user" ? "user" : role === "gate" ? "gate" : "agent"}`;
  const roleLabel = role === "user" ? "你" : role === "gate" ? "需要人工处理" : "Agent";
  item.innerHTML = `<span class="chat-role">${roleLabel}</span><div>${escapeHtml(text)}</div>`;
  if (continueButton) {
    const actions = document.createElement("div");
    actions.className = "gate-actions";
    actions.innerHTML = `<button type="button" class="continue-btn" data-continue="1">我已完成，继续</button>`;
    item.appendChild(actions);
    bindContinueButtons(item);
  }
  chatLog.appendChild(item);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function bindContinueButtons(root) {
  root.querySelectorAll("[data-continue]").forEach((button) => {
    button.addEventListener("click", () => {
      if (isRunning) return;
      // Same continuation path as natural-language feedback — never calls search_jobs directly.
      sendMessage(CONTINUE_FEEDBACK, { fromShortcut: true });
    });
  });
}

function bindLlmRetryButtons(root) {
  root.querySelectorAll("[data-llm-retry]").forEach((button) => {
    button.addEventListener("click", () => {
      if (isRunning) return;
      const retryText = lastUserMessage || "请继续";
      sendMessage(retryText, { fromShortcut: true });
    });
  });
}

function renderJobs(jobs, data) {
  resultPanel.hidden = false;
  if (!jobs.length) {
    const message =
      (data && data.message) ||
      (data && data.outcome_kind === "no_search"
        ? "这一轮还没有开始搜索职位，所以没有推荐结果。"
        : "这一轮还没有可展示的推荐职位。");
    resultPanel.innerHTML = `<div class="panel banner prompt">${escapeHtml(message)}</div>`;
    return;
  }
  resultPanel.innerHTML = jobs
    .map((job) => {
      const title = job.job_title || job.title || "职位";
      const company = job.company_name || job.company || "";
      const url = job.job_url || "";
      const recommendation = job.recommendation || "";
      const fit = job.overall_fit || "";
      const rationale = job.rationale || job.summary || "";
      const matched = capabilityList(job.matched_capabilities);
      const transferable = capabilityList(job.transferable_capabilities, { transfer: true });
      const gaps = (job.knowledge_gaps || [])
        .map((item) => (typeof item === "string" ? item : item.gap || ""))
        .filter(Boolean)
        .map((item) => `<li>${escapeHtml(item)}</li>`)
        .join("");
      return `
      <article class="result-card">
        <h3>${escapeHtml(title)}</h3>
        <p class="muted">${escapeHtml(company)}${url ? ` · <a href="${escapeAttr(url)}" target="_blank" rel="noreferrer">打开职位</a>` : ""}</p>
        <p><strong>${escapeHtml(recommendation)}</strong>${fit ? ` · ${escapeHtml(String(fit))}` : ""}</p>
        <p>${escapeHtml(rationale)}</p>
        ${matched ? `<div class="profile-section"><h3>已具备</h3><ul>${matched}</ul></div>` : ""}
        ${transferable ? `<div class="profile-section"><h3>可迁移</h3><ul>${transferable}</ul></div>` : ""}
        ${gaps ? `<div class="profile-section"><h3>主要缺口</h3><ul>${gaps}</ul></div>` : ""}
      </article>`;
    })
    .join("");
}

function capabilityList(items, { transfer = false } = {}) {
  if (!items || !items.length) return "";
  return items
    .map((item) => {
      if (typeof item === "string") return `<li>${escapeHtml(item)}</li>`;
      const name = item.capability || item.name || "";
      const detail = transfer
        ? item.transfer_rationale || item.rationale || ""
        : item.rationale || "";
      return `<li>${escapeHtml(name)}${detail ? ` — ${escapeHtml(detail)}` : ""}</li>`;
    })
    .join("");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function setRunning(running, title, message) {
  isRunning = running;
  if (running) clearRunActions();
  if (title) runTitle.textContent = title;
  if (message) runMessage.textContent = message;
  runPanel.hidden = false;
  syncSubmitState();
}

function clearStages() {
  stageItems.forEach((item) => item.classList.remove("done", "active"));
}

function paintStages(status, stats) {
  const order = stageItems.map((item) => item.dataset.stages.split(","));
  let activeIndex = -1;
  order.forEach((stages, index) => {
    if (stages.includes(status)) activeIndex = index;
  });
  // Don't paint a full green pipeline when DONE/FAILED never actually searched.
  if ((status === "DONE" || status === "FAILED") && stats) {
    const searches = Number(stats.searches || 0);
    const listed = Number(stats.listed || 0);
    const opened = Number(stats.opened || 0);
    const recommended = Number(stats.recommended || 0);
    if (searches === 0 && listed === 0) {
      activeIndex = Number(stats.llm_calls || 0) > 0 ? 0 : -1;
    } else if (listed > 0 && opened === 0) {
      activeIndex = 2;
    } else if (opened > 0 && recommended === 0 && status === "DONE") {
      activeIndex = Math.max(activeIndex >= 0 ? Math.min(activeIndex, 4) : 4, 3);
    }
  }
  stageItems.forEach((item, index) => {
    item.classList.toggle("done", activeIndex > index);
    item.classList.toggle("active", activeIndex === index);
  });
}

function stageTitle(status) {
  const map = {
    PARSING_GOAL: "正在理解你的需求……",
    GATHERING_CANDIDATE: "正在整理候选人背景……",
    PLANNING: "正在规划搜索……",
    SEARCHING: "正在搜索职位……",
    ENRICHING_JOBS: "正在打开职位详情……",
    ANALYZING: "正在分析职位要求……",
    MATCHING: "正在分析匹配度……",
    FILTERING: "正在整理结果……",
    RANKING: "正在整理结果……",
    REPORTING: "正在整理结果……",
    DECIDING: "正在判断下一步……",
    EXECUTING: "正在执行操作……",
    WAITING_USER: "需要你的决定",
    NEEDS_HUMAN: "需要你处理一下",
    DONE: "完成",
    FAILED: "这次没有完成",
  };
  return map[status] || "";
}

async function loadProfile() {
  try {
    const data = await request("/api/profile");
    if (data && data.exists) renderProfile(data, data);
    else {
      profileEmpty.hidden = false;
      profileBody.hidden = true;
      profileMeta.textContent = "";
    }
  } catch (_err) {
    profileEmpty.hidden = false;
    profileBody.hidden = true;
  }
}

function renderProfile(profile, meta) {
  if (!profile || profile.exists === false) {
    profileEmpty.hidden = false;
    profileBody.hidden = true;
    profileMeta.textContent = "";
    return;
  }
  profileEmpty.hidden = true;
  profileBody.hidden = false;
  const summary = profile.summary || profile.candidate_summary || "";
  const skills =
    profile.core_capabilities || profile.skills || profile.core_skills || [];
  const experience =
    profile.core_experience || profile.experience || profile.experiences || [];
  profileMeta.textContent = meta && meta.updated_at ? `更新于 ${formatTime(meta.updated_at)}` : "";
  profileBody.innerHTML = [
    summary ? `<p class="profile-summary">${escapeHtml(summary)}</p>` : "",
    chipsBlock("技能", skills),
    experience.length
      ? `<div class="profile-section"><h3>经历</h3>${experience
          .map((item) => `<p>${escapeHtml(typeof item === "string" ? item : item.title || item.summary || "")}</p>`)
          .join("")}</div>`
      : "",
  ].join("");
}

function chipsBlock(title, items) {
  if (!items || !items.length) return "";
  return `<div class="profile-section"><h3>${title}</h3><div class="chips">${items
    .map((item) => `<span class="chip">${escapeHtml(typeof item === "string" ? item : item.name || item)}</span>`)
    .join("")}</div></div>`;
}

function formatTime(value) {
  const text = String(value || "");
  if (text.length >= 16 && text.includes("T")) return text.slice(0, 16).replace("T", " ");
  return text;
}

function showError(message) {
  formError.hidden = false;
  formError.textContent = message;
}

function hideError() {
  formError.hidden = true;
  formError.textContent = "";
}

function showNote(message) {
  formNote.hidden = false;
  formNote.textContent = message;
}

function hideNote() {
  formNote.hidden = true;
  formNote.textContent = "";
}

function stopPolling() {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function isTerminal(status) {
  return status === "DONE" || status === "FAILED" || status === "NEEDS_HUMAN" || status === "WAITING_USER";
}

function extensionOf(name) {
  const base = String(name || "").split("\\").pop().split("/").pop();
  const idx = base.lastIndexOf(".");
  return idx >= 0 ? base.slice(idx).toLowerCase() : "";
}

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.message || `请求失败（${response.status}）`);
    error.error_code = data.error_code;
    throw error;
  }
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

syncSubmitState();
