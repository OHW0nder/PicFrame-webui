const state = {
  jobId: null,
  job: null,
  templates: [],
  selectedTemplateId: "info-portrait",
  selectedIndex: 0,
  view: "original",
  scope: "all",
  filter: "all",
  historyJobs: [],
  pollTimer: null,
  reveal: 50,
  dragging: false,
};

const icons = {
  history: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/></svg>',
  plus: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>',
  upload: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>',
  chevronLeft: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>',
  chevronRight: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>',
  download: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>',
  bookmark: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
  close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>',
};

const el = {
  emptyState: document.getElementById("emptyState"),
  workspace: document.getElementById("workspace"),
  dropArea: document.getElementById("dropArea"),
  dropIcon: document.getElementById("dropIcon"),
  browseButton: document.getElementById("browseButton"),
  fileInput: document.getElementById("fileInput"),
  historyButton: document.getElementById("historyButton"),
  newJobButton: document.getElementById("newJobButton"),
  historyOverlay: document.getElementById("historyOverlay"),
  closeHistoryButton: document.getElementById("closeHistoryButton"),
  historyCount: document.getElementById("historyCount"),
  historyGrid: document.getElementById("historyGrid"),
  filterTabs: document.getElementById("filterTabs"),
  jobTemplateLabel: document.getElementById("jobTemplateLabel"),
  jobTitle: document.getElementById("jobTitle"),
  startButton: document.getElementById("startButton"),
  saveButton: document.getElementById("saveButton"),
  downloadSingleButton: document.getElementById("downloadSingleButton"),
  downloadAllButton: document.getElementById("downloadAllButton"),
  resultViewButton: document.getElementById("resultViewButton"),
  progressPanel: document.getElementById("progressPanel"),
  progressTitle: document.getElementById("progressTitle"),
  progressMessage: document.getElementById("progressMessage"),
  progressBar: document.getElementById("progressBar"),
  originalView: document.getElementById("originalView"),
  compareStage: document.getElementById("compareStage"),
  compareBefore: document.getElementById("compareBefore"),
  compareAfter: document.getElementById("compareAfter"),
  compareClip: document.getElementById("compareClip"),
  compareHandle: document.getElementById("compareHandle"),
  viewer: document.getElementById("viewer"),
  thumbnailStrip: document.getElementById("thumbnailStrip"),
  templateList: document.getElementById("templateList"),
  templateCount: document.getElementById("templateCount"),
  compression: document.getElementById("compression"),
  prevButton: document.getElementById("prevButton"),
  nextButton: document.getElementById("nextButton"),
  imageCounter: document.getElementById("imageCounter"),
  toast: document.getElementById("toast"),
};

let toastTimer = null;

function renderIcons() {
  document.getElementById("historyIcon").innerHTML = icons.history;
  document.getElementById("newJobIcon").innerHTML = icons.plus;
  el.dropIcon.innerHTML = icons.upload;
  document.getElementById("browseIcon").innerHTML = icons.upload;
  document.getElementById("startIcon").innerHTML = icons.plus;
  document.getElementById("saveIcon").innerHTML = icons.bookmark;
  document.getElementById("downloadSingleIcon").innerHTML = icons.download;
  document.getElementById("downloadAllIcon").innerHTML = icons.download;
  el.prevButton.innerHTML = icons.chevronLeft;
  el.nextButton.innerHTML = icons.chevronRight;
  el.closeHistoryButton.innerHTML = icons.close;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return data;
}

function toast(message) {
  el.toast.textContent = message;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.toast.hidden = true;
  }, 2800);
}

function selectedTemplate() {
  return state.templates.find((item) => item.id === state.selectedTemplateId) || state.templates[0];
}

function setState(name) {
  el.emptyState.hidden = name !== "empty";
  el.workspace.hidden = name !== "workspace";
  el.newJobButton.hidden = name === "empty";
}

function setReveal(value) {
  state.reveal = Math.max(2, Math.min(98, value));
  el.compareStage.style.setProperty("--reveal", `${state.reveal}%`);
  el.compareHandle.style.setProperty("--reveal", `${state.reveal}%`);
  el.compareHandle.setAttribute("aria-valuenow", String(Math.round(state.reveal)));
}

function statusBadge(status) {
  if (status === "completed") return "✓";
  if (status === "processing") return "…";
  if (status === "error" || status === "interrupted") return "!";
  if (status === "queued") return "·";
  return "";
}

function hasResults() {
  return Boolean(state.job && state.job.results && state.job.results.length);
}

function resultForIndex(index) {
  if (!state.job) return null;
  return state.job.results.find((item) => item.index === index)
    || state.job.results[0]
    || null;
}

function renderTemplates() {
  const activeId = state.selectedTemplateId;
  el.templateList.innerHTML = state.templates.map((template) => {
    const active = template.id === activeId ? " active" : "";
    return `<button type="button" class="template-card${active}" data-template-id="${template.id}" aria-pressed="${template.id === activeId}">
      <img src="${template.after}" alt="" loading="lazy">
      <span class="template-meta">
        <strong>${escapeHtml(template.name)}</strong>
        <span>${escapeHtml(template.category)}</span>
      </span>
    </button>`;
  }).join("");
  el.templateCount.textContent = `${state.templates.length} 款`;
}

function selectTemplate(templateId) {
  state.selectedTemplateId = templateId;
  renderTemplates();
  if (state.job) {
    el.jobTemplateLabel.textContent = selectedTemplate()?.name || "未选择模板";
  }
  if (state.view === "template") {
    renderViewer();
  }
}

function renderThumbnails() {
  if (!state.job) return;
  const images = state.job.images || [];
  el.thumbnailStrip.innerHTML = images.map((image, index) => {
    const active = index === state.selectedIndex ? " active" : "";
    const badge = statusBadge(image.status);
    return `<button type="button" class="thumb${active}" data-image-index="${index}" aria-label="${escapeHtml(image.name)}">
      <img src="${image.thumb_url}" alt="" loading="lazy">
      ${badge ? `<span class="thumb-state ${image.status}">${badge}</span>` : ""}
    </button>`;
  }).join("");
}

function renderOriginal() {
  if (!state.job || !state.job.images.length) return;
  const image = state.job.images[state.selectedIndex];
  el.originalView.innerHTML = `<img src="${image.preview_url}" alt="">`;
}

function renderTemplateCompare() {
  const template = selectedTemplate();
  if (!template) return;
  el.compareBefore.src = template.before;
  el.compareAfter.src = template.after;
  el.compareStage.hidden = false;
  el.originalView.innerHTML = "";
  setReveal(state.reveal);
}

function renderResultCompare() {
  const result = resultForIndex(state.selectedIndex);
  if (!result) {
    showView("original");
    return;
  }
  const image = state.job.images[state.selectedIndex] || state.job.images[0];
  el.compareBefore.src = image.preview_url;
  el.compareAfter.src = `/api/jobs/${state.job.id}/result/${result.index}`;
  el.compareStage.hidden = false;
  el.originalView.innerHTML = "";
  setReveal(state.reveal);
}

function renderViewer() {
  if (!state.job) return;
  el.resultViewButton.hidden = !hasResults();
  if (state.view === "template") {
    renderTemplateCompare();
  } else if (state.view === "result") {
    renderResultCompare();
  } else {
    el.compareStage.hidden = true;
    renderOriginal();
  }
}

function renderProgress() {
  if (!state.job || !["processing", "queued"].includes(state.job.status)) {
    el.progressPanel.hidden = true;
    return;
  }
  const progress = state.job.progress || {};
  const current = Number(progress.current || 0);
  const total = Number(progress.total || state.job.images.length || 1);
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  el.progressPanel.hidden = false;
  el.progressTitle.textContent = progress.message || "处理中";
  el.progressMessage.textContent = `${current} / ${total}`;
  el.progressBar.style.width = `${percent}%`;
}

function renderJob() {
  if (!state.job) return;
  const template = state.templates.find((item) => item.id === state.job.template_id)
    || state.templates.find((item) => item.id === state.selectedTemplateId)
    || state.templates[0];
  el.jobTemplateLabel.textContent = template?.name || "未选择模板";
  el.jobTitle.textContent = `${state.job.images.length} 张照片`;
  el.imageCounter.textContent = `${state.selectedIndex + 1} / ${state.job.images.length}`;

  const running = ["processing", "queued"].includes(state.job.status);
  const completed = state.job.status === "completed";
  el.startButton.disabled = running;
  el.startButton.innerHTML = `${icons.plus}${running ? "处理中" : completed ? "再次处理" : "开始处理"}`;
  el.saveButton.hidden = !completed;
  el.downloadSingleButton.hidden = !hasResults();
  el.downloadAllButton.hidden = !hasResults();
  el.saveButton.innerHTML = `${icons.bookmark}${state.job.saved ? "已收藏" : "收藏"}`;
  el.resultViewButton.hidden = !hasResults();

  renderThumbnails();
  renderProgress();
  renderViewer();
}

function selectImage(index) {
  if (!state.job || !state.job.images.length) return;
  state.selectedIndex = Math.max(0, Math.min(state.job.images.length - 1, index));
  el.imageCounter.textContent = `${state.selectedIndex + 1} / ${state.job.images.length}`;
  renderThumbnails();
  renderViewer();
}

function showView(view) {
  state.view = view;
  document.querySelectorAll(".viewer-toolbar .seg").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  if (view === "result" && !hasResults()) {
    toast("还没有可查看的结果");
    return;
  }
  renderViewer();
}

function stopPolling() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling() {
  stopPolling();
  const poll = async () => {
    if (!state.jobId) return;
    try {
      const job = await api(`/api/jobs/${state.jobId}`);
      state.job = job;
      renderJob();
      if (["processing", "queued"].includes(job.status)) {
        state.pollTimer = setTimeout(poll, 1000);
      } else {
        state.pollTimer = null;
        if (job.status === "completed") {
          showView("result");
        } else if (job.error) {
          toast(job.error || "处理失败");
        }
      }
    } catch (error) {
      state.pollTimer = null;
      toast(error.message);
    }
  };
  state.pollTimer = setTimeout(poll, 300);
}

async function createJob(files) {
  if (!files || !files.length) return;
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file, file.name);
  }
  try {
    const job = await api("/api/jobs", { method: "POST", body: formData });
    state.jobId = job.id;
    state.job = job;
    state.selectedIndex = 0;
    state.view = "original";
    setState("workspace");
    renderJob();
    toast(`已上传 ${job.images.length} 张`);
  } catch (error) {
    toast(error.message);
  }
}

async function startJob() {
  if (!state.jobId || !state.job) return;
  const template = selectedTemplate();
  if (!template) {
    toast("请先选择模板");
    return;
  }
  try {
    const job = await api(`/api/jobs/${state.jobId}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_id: state.selectedTemplateId,
        compression: el.compression.value,
        scope: state.scope,
        index: state.scope === "selected" ? state.selectedIndex : null,
      }),
    });
    state.job = job;
    renderJob();
    startPolling();
  } catch (error) {
    toast(error.message);
  }
}

function renderHistory() {
  const filter = state.filter;
  let jobs = state.historyJobs;
  if (filter === "saved") {
    jobs = jobs.filter((job) => job.saved);
  } else if (filter !== "all") {
    jobs = jobs.filter((job) => job.template && job.template.category === filter);
  }
  el.historyCount.textContent = `${jobs.length} 个任务`;
  if (!jobs.length) {
    el.historyGrid.innerHTML = '<div class="history-empty">暂无任务</div>';
    return;
  }
  el.historyGrid.innerHTML = jobs.map((job) => {
    const image = (job.images || [])[0];
    const thumb = image ? image.thumb_url : "";
    const templateName = job.template?.name || "未选择模板";
    const count = job.images?.length || 0;
    return `<button type="button" class="history-card" data-history-job-id="${job.id}">
      ${thumb ? `<img src="${thumb}" alt="" loading="lazy">` : ""}
      <span class="history-meta">
        <strong>${escapeHtml(templateName)}</strong>
        <span>${count} 张 · ${escapeHtml(job.status)}</span>
      </span>
    </button>`;
  }).join("");
}

async function loadHistory() {
  try {
    const data = await api("/api/jobs");
    state.historyJobs = data.jobs || [];
    renderHistory();
  } catch (error) {
    toast(error.message);
  }
}

async function openHistory() {
  state.filter = "all";
  document.querySelectorAll("#filterTabs .seg").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === "all");
  });
  el.historyOverlay.hidden = false;
  await loadHistory();
}

function closeHistory() {
  el.historyOverlay.hidden = true;
}

function openJob(job) {
  stopPolling();
  state.jobId = job.id;
  state.job = job;
  state.selectedIndex = 0;
  state.view = job.status === "completed" ? "result" : "original";
  closeHistory();
  setState("workspace");
  renderJob();
  if (["processing", "queued"].includes(job.status)) {
    startPolling();
  }
}

function resetWorkspace() {
  stopPolling();
  state.jobId = null;
  state.job = null;
  state.selectedIndex = 0;
  state.view = "original";
  setState("empty");
}

async function toggleSave() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}/save`, { method: "POST" });
    state.job = job;
    renderJob();
    toast(job.saved ? "已收藏" : "已取消收藏");
  } catch (error) {
    toast(error.message);
  }
}

function downloadSingle() {
  if (!state.jobId) return;
  const result = resultForIndex(state.selectedIndex);
  if (!result) {
    toast("当前图片没有结果");
    return;
  }
  window.location.href = `/api/jobs/${state.jobId}/download?index=${result.index}`;
}

function downloadAll() {
  if (!state.jobId) return;
  window.location.href = `/api/jobs/${state.jobId}/download`;
}

async function loadTemplates() {
  try {
    const data = await api("/api/templates");
    state.templates = data.templates || [];
    if (!state.templates.some((item) => item.id === state.selectedTemplateId)) {
      state.selectedTemplateId = state.templates[0]?.id || "";
    }
    renderTemplates();
  } catch (error) {
    toast(error.message);
  }
}

function bindEvents() {
  el.dropArea.addEventListener("click", () => el.fileInput.click());
  el.dropArea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      el.fileInput.click();
    }
  });
  el.browseButton.addEventListener("click", (event) => {
    event.stopPropagation();
    el.fileInput.click();
  });
  el.fileInput.addEventListener("change", () => {
    createJob(el.fileInput.files);
    el.fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((name) => {
    el.dropArea.addEventListener(name, (event) => {
      event.preventDefault();
      el.dropArea.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    el.dropArea.addEventListener(name, (event) => {
      event.preventDefault();
      el.dropArea.classList.remove("dragover");
    });
  });
  el.dropArea.addEventListener("drop", (event) => {
    createJob(event.dataTransfer.files);
  });

  document.querySelectorAll(".viewer-toolbar .seg").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  el.prevButton.addEventListener("click", () => selectImage(state.selectedIndex - 1));
  el.nextButton.addEventListener("click", () => selectImage(state.selectedIndex + 1));
  el.viewer.addEventListener("keydown", (event) => {
    if (event.target === el.compareHandle) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      selectImage(state.selectedIndex - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      selectImage(state.selectedIndex + 1);
    }
  });

  el.thumbnailStrip.addEventListener("click", (event) => {
    const button = event.target.closest(".thumb");
    if (button) selectImage(Number(button.dataset.imageIndex));
  });

  el.templateList.addEventListener("click", (event) => {
    const card = event.target.closest(".template-card");
    if (card) selectTemplate(card.dataset.templateId);
  });

  document.querySelectorAll("[data-scope]").forEach((button) => {
    button.addEventListener("click", () => {
      state.scope = button.dataset.scope;
      document.querySelectorAll("[data-scope]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
    });
  });

  el.startButton.addEventListener("click", startJob);
  el.saveButton.addEventListener("click", toggleSave);
  el.downloadSingleButton.addEventListener("click", downloadSingle);
  el.downloadAllButton.addEventListener("click", downloadAll);
  el.newJobButton.addEventListener("click", resetWorkspace);
  el.historyButton.addEventListener("click", openHistory);
  el.closeHistoryButton.addEventListener("click", closeHistory);
  el.historyOverlay.addEventListener("click", (event) => {
    if (event.target === el.historyOverlay) closeHistory();
  });
  el.historyGrid.addEventListener("click", (event) => {
    const card = event.target.closest(".history-card");
    if (!card) return;
    const job = state.historyJobs.find((item) => item.id === card.dataset.historyJobId);
    if (job) openJob(job);
  });
  el.filterTabs.addEventListener("click", (event) => {
    const button = event.target.closest(".seg");
    if (!button) return;
    state.filter = button.dataset.filter;
    document.querySelectorAll("#filterTabs .seg").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderHistory();
  });

  el.compareHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    state.dragging = true;
    el.compareHandle.setPointerCapture(event.pointerId);
  });
  el.compareHandle.addEventListener("pointermove", (event) => {
    if (!state.dragging) return;
    const rect = el.compareStage.getBoundingClientRect();
    const percent = ((event.clientX - rect.left) / rect.width) * 100;
    setReveal(percent);
  });
  el.compareHandle.addEventListener("pointerup", () => {
    state.dragging = false;
  });
  el.compareHandle.addEventListener("pointercancel", () => {
    state.dragging = false;
  });
  el.compareHandle.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setReveal(state.reveal - 5);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setReveal(state.reveal + 5);
    }
  });
  el.compareStage.addEventListener("click", (event) => {
    if (event.target.closest("#compareHandle")) return;
    setReveal(state.reveal > 80 ? 50 : 92);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el.historyOverlay.hidden) {
      closeHistory();
    }
  });
}

function init() {
  renderIcons();
  bindEvents();
  loadTemplates();
}

init();
