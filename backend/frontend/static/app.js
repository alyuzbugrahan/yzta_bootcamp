"use strict";

const API_BASE = "/api/v1";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const savedFigWeight = Number.parseFloat(localStorage.getItem("agrovision_fig_weight_g") || "");

const state = {
  authMode: "login",
  tokens: JSON.parse(localStorage.getItem("agrovision_tokens") || "null"),
  user: null,
  page: "dashboard",
  history: [],
  historyCursor: null,
  historyLoading: false,
  historyVersion: 0,
  recentSessions: [],
  editingSession: null,
  dashboard: { metrics: null, metricsAt: 0, metricsPromise: null, metricsVersion: 0, recentVersion: 0 },
  scan: {
    session: null,
    socket: null,
    stream: null,
    timer: null,
    waiting: false,
    paused: false,
    stopping: false,
    figWeightG: Number.isFinite(savedFigWeight) && savedFigWeight > 0 ? savedFigWeight : null,
    adviceCache: {},
    adviceRequestKey: null,
    counts: { recorded: 0, healthy: 0, aflatoxin: 0, processed: 0, dropped: 0 },
  },
};

class ApiClientError extends Error {
  constructor(message, code = "HTTP_ERROR", status = 0, detail = null) {
    super(message);
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

function saveTokens(tokens) {
  state.tokens = tokens;
  if (tokens) localStorage.setItem("agrovision_tokens", JSON.stringify(tokens));
  else localStorage.removeItem("agrovision_tokens");
}

async function api(path, options = {}, allowRefresh = true) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (state.tokens?.access_token) headers.set("Authorization", `Bearer ${state.tokens.access_token}`);

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401 && allowRefresh && state.tokens?.refresh_token) {
    const refreshed = await refreshAccessToken();
    if (refreshed) return api(path, options, false);
  }
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* no json body */ }
    const error = payload?.error;
    throw new ApiClientError(
      error?.message || `İstek başarısız (${response.status})`,
      error?.code || "HTTP_ERROR",
      response.status,
      error?.detail || null,
    );
  }
  if (response.status === 204) return null;
  return response.json();
}

async function refreshAccessToken() {
  try {
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.tokens.refresh_token }),
    });
    if (!response.ok) throw new Error("refresh failed");
    saveTokens(await response.json());
    return true;
  } catch (_) {
    saveTokens(null);
    showAuth();
    return false;
  }
}

function toast(title, message = "", type = "info") {
  const element = document.createElement("div");
  element.className = `toast ${type === "error" ? "toast--error" : ""}`;
  element.innerHTML = `<span>${type === "error" ? "!" : "✓"}</span><div><strong>${escapeHtml(title)}</strong>${message ? `<p>${escapeHtml(message)}</p>` : ""}</div>`;
  $("#toast-region").append(element);
  setTimeout(() => element.remove(), 4600);
}

function setButtonLoading(button, loading, text) {
  if (!button) return;
  if (!button.dataset.original) button.dataset.original = button.innerHTML;
  button.disabled = loading;
  button.innerHTML = loading ? text : button.dataset.original;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function formatDate(value, withTime = true) {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit", month: "short", year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function percent(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? `%${number.toFixed(digits)}` : "—";
}

function healthyCount(item) {
  return Math.max(Number(item.total_count || 0) - Number(item.defect_count || 0), 0);
}

function formatKg(value) {
  if (value === null || value === undefined || value === "") return "—";
  const kilograms = Number(value);
  return Number.isFinite(kilograms)
    ? `${kilograms.toLocaleString("tr-TR", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} kg`
    : "—";
}

function showAuth() {
  $("#auth-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
}

function showApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  const email = state.user?.email || "Kullanıcı";
  $("#user-email").textContent = email;
  $("#user-avatar").textContent = email[0].toUpperCase();
}

function updateAuthMode() {
  const register = state.authMode === "register";
  $("#auth-title").textContent = register ? "Yeni hesabınızı oluşturun" : "Hesabınıza giriş yapın";
  $("#auth-subtitle").textContent = register
    ? "Tarama sonuçlarınızı güvenli biçimde saklamaya başlayın."
    : "Tarama oturumlarınıza ve üretici asistanına erişin.";
  $("#auth-submit span").textContent = register ? "Kayıt ol" : "Giriş yap";
  $("#auth-switch-label").textContent = register ? "Zaten hesabınız var mı?" : "Hesabınız yok mu?";
  $("#auth-switch").textContent = register ? "Giriş yapın" : "Kayıt olun";
  $("#password").autocomplete = register ? "new-password" : "current-password";
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const button = $("#auth-submit");
  const email = $("#email").value.trim();
  const password = $("#password").value;
  if (!email || !password) return toast("Eksik bilgi", "E-posta ve şifre alanlarını doldurun.", "error");
  if (state.authMode === "register" && password.length < 8) return toast("Şifre çok kısa", "Şifre en az 8 karakter olmalıdır.", "error");

  setButtonLoading(button, true, state.authMode === "register" ? "Hesap oluşturuluyor…" : "Giriş yapılıyor…");
  try {
    const tokens = await api(`/auth/${state.authMode === "register" ? "register" : "login"}`, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }, false);
    saveTokens(tokens);
    state.user = await api("/me");
    showApp();
    await initializeApp();
    toast(state.authMode === "register" ? "Hesap oluşturuldu" : "Giriş başarılı");
  } catch (error) {
    toast("İşlem başarısız", error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

const pageMeta = {
  dashboard: ["Kontrol merkezi", "Genel Bakış"],
  scanner: ["Yapay zekâ analizi", "Canlı Tarama"],
  assistant: ["RAG destekli danışman", "Üretici Asistanı"],
  history: ["Parti ve rapor yönetimi", "Geçmiş Oturumlar"],
};

function navigate(page) {
  if (!pageMeta[page]) return;
  state.page = page;
  $$(".page").forEach((element) => element.classList.toggle("active", element.id === `page-${page}`));
  $$(".nav-item").forEach((element) => element.classList.toggle("active", element.dataset.page === page));
  $("#page-eyebrow").textContent = pageMeta[page][0];
  $("#page-title").textContent = pageMeta[page][1];
  $(".sidebar").classList.remove("open");
  history.replaceState(null, "", `#${page}`);
  if (page === "dashboard") loadDashboard();
  if (page === "history") loadHistory(true);
  if (page === "assistant") checkRagStatus();
}

async function initializeApp() {
  showApp();
  await Promise.allSettled([checkSystemStatus(), checkRagStatus()]);
  navigate(location.hash.slice(1) || "dashboard");
}

async function checkSystemStatus() {
  const dot = $("#system-status-dot");
  try {
    const [health, model] = await Promise.all([api("/health"), api("/model/info")]);
    dot.className = "status-dot good";
    $("#system-status-text").textContent = health.status === "ok" ? "Sistem çevrimiçi" : "Sistem uyarısı";
    $("#model-mode-text").textContent = model.demo_mode ? "Demo model etkin" : `${model.backend} modeli etkin`;
  } catch (error) {
    dot.className = "status-dot bad";
    $("#system-status-text").textContent = "Bağlantı sorunu";
    $("#model-mode-text").textContent = error.message;
  }
}

async function checkRagStatus() {
  const chip = $("#rag-status-chip");
  try {
    const status = await api("/rag/status");
    const ready = status.enabled && status.vector_store_found;
    chip.classList.toggle("good", ready);
    chip.textContent = ready
      ? `${status.semantic_dependencies ? "Vektör arama" : "Yedek arama"} hazır${status.llm_configured ? " · Gemini bağlı" : ""}`
      : "RAG veri deposu bulunamadı";
  } catch (error) {
    chip.textContent = "RAG durumuna ulaşılamadı";
  }
}

function loadDashboard(force = false) {
  // Recent sessions are cheap and must never wait for the 30-day aggregate.
  void loadRecentSessions();
  void loadDashboardMetrics(force);
}

async function loadRecentSessions() {
  const body = $("#recent-sessions-body");
  const requestVersion = ++state.dashboard.recentVersion;
  body.innerHTML = '<tr><td colspan="7" class="empty-cell">Son taramalar yükleniyor…</td></tr>';

  try {
    const sessions = await api("/sessions?limit=6");
    if (requestVersion !== state.dashboard.recentVersion) return;
    state.recentSessions = sessions.items;
    renderRecentSessions(state.recentSessions);
  } catch (error) {
    if (requestVersion !== state.dashboard.recentVersion) return;
    body.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

function applyDashboardMetrics(range) {
  $("#metric-total").textContent = range.total_figs.toLocaleString("tr-TR");
  $("#metric-healthy").textContent = range.healthy_count.toLocaleString("tr-TR");
  $("#metric-aflatoxin").textContent = range.aflatoxin_count.toLocaleString("tr-TR");
  $("#metric-confidence").textContent = percent(range.mean_confidence * 100);
  const healthyRate = range.total_figs ? (range.healthy_count / range.total_figs) * 100 : 0;
  $("#metric-healthy-rate").textContent = `${percent(healthyRate)} sağlıklı`;
  $("#metric-defect-rate").textContent = `${percent(range.defect_rate_pct)} risk oranı`;
}

async function loadDashboardMetrics(force = false) {
  const cacheAge = Date.now() - state.dashboard.metricsAt;
  if (!force && state.dashboard.metrics && cacheAge < 15000) {
    applyDashboardMetrics(state.dashboard.metrics);
    return;
  }
  if (!force && state.dashboard.metricsPromise) return state.dashboard.metricsPromise;

  const requestVersion = force
    ? ++state.dashboard.metricsVersion
    : state.dashboard.metricsVersion;
  const request = (async () => {
    try {
      const range = await api("/reports/range");
      if (requestVersion !== state.dashboard.metricsVersion) return;
      state.dashboard.metrics = range;
      state.dashboard.metricsAt = Date.now();
      applyDashboardMetrics(range);
    } catch (error) {
      if (requestVersion === state.dashboard.metricsVersion) {
        toast("Özet bilgiler yüklenemedi", error.message, "error");
      }
    } finally {
      if (state.dashboard.metricsPromise === request) {
        state.dashboard.metricsPromise = null;
      }
    }
  })();

  state.dashboard.metricsPromise = request;
  return request;
}

function editIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17.25V20h2.75L17.81 8.94l-2.75-2.75L4 17.25Zm15.71-10.04a1 1 0 0 0 0-1.42l-1.5-1.5a1 1 0 0 0-1.42 0l-1.17 1.17 2.75 2.75 1.34-1.34Z"/></svg>';
}

function deleteIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 21a2 2 0 0 1-2-2V7h14v12a2 2 0 0 1-2 2H7Zm3-4h2V10h-2v7Zm4 0h2V10h-2v7ZM4 6V4h5l1-1h4l1 1h5v2H4Z"/></svg>';
}

function renderRecentSessions(items) {
  const body = $("#recent-sessions-body");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">Henüz tarama oturumu yok.</td></tr>';
    return;
  }
  body.innerHTML = items.map((item) => `<tr>
      <td><strong>${escapeHtml(item.batch_id)}</strong></td>
      <td>${formatDate(item.start_time)}</td>
      <td>${item.total_count}${item.is_manually_corrected ? '<small class="correction-tag">Kullanıcı girişi</small>' : ""}</td>
      <td>${healthyCount(item)}</td>
      <td><span class="${item.defect_count > 0 ? "text-danger" : "text-good"}">${item.defect_count}</span></td>
      <td>${formatKg(item.total_kg)}</td>
      <td class="row-actions-cell">
        <div class="row-actions">
          <button class="table-icon-button" data-edit-session="${item.uuid}" aria-label="Tarama kaydını düzenle" title="Düzenle" ${item.is_open ? "disabled" : ""}>${editIcon()}</button>
          <button class="table-icon-button table-icon-button--danger" data-delete-session="${item.uuid}" aria-label="Tarama kaydını sil" title="Sil" ${item.is_open ? "disabled" : ""}>${deleteIcon()}</button>
        </div>
      </td>
    </tr>`).join("");

  $$('[data-edit-session]', body).forEach((button) => button.addEventListener("click", () => {
    const item = state.recentSessions.find((session) => session.uuid === button.dataset.editSession);
    if (item) openSessionEditor(item);
  }));
  $$('[data-delete-session]', body).forEach((button) => button.addEventListener("click", () => {
    const item = state.recentSessions.find((session) => session.uuid === button.dataset.deleteSession);
    if (item) void deleteSession(item, button);
  }));
}

function openSessionEditor(item) {
  if (item.is_open) return toast("Oturum açık", "Devam eden tarama önce tamamlanmalıdır.", "error");
  state.editingSession = item;
  $("#edit-batch-id").value = item.batch_id;
  $("#edit-device-label").value = item.device_label || "";
  $("#edit-total-count").value = String(item.total_count);
  $("#edit-defect-count").value = String(item.defect_count);
  $("#edit-fig-weight").value = item.fig_weight_g ?? "";
  const detectedNote = item.is_manually_corrected
    ? `Model ham kaydı: ${item.detected_total_count} ürün, ${item.detected_defect_count} aflatoksin. Düzeltme özetlerde kullanılır; ham kayıt korunur.`
    : `Model ham kaydı: ${item.detected_total_count} ürün, ${item.detected_defect_count} aflatoksin. Girdiğiniz düzeltme özetlerde kullanılır; ham kayıt korunur.`;
  $("#edit-count-note").textContent = detectedNote;
  $("#session-edit-modal").classList.remove("hidden");
  $("#edit-batch-id").focus();
}

function closeSessionEditor() {
  state.editingSession = null;
  $("#session-edit-modal").classList.add("hidden");
  $("#session-edit-form").reset();
}

async function submitSessionEdit(event) {
  event.preventDefault();
  const item = state.editingSession;
  if (!item) return;
  const button = $("#save-session-edit");
  const batchId = $("#edit-batch-id").value.trim();
  const deviceLabel = $("#edit-device-label").value.trim();
  const totalCount = Number.parseInt($("#edit-total-count").value, 10);
  const defectCount = Number.parseInt($("#edit-defect-count").value, 10);
  const figWeightRaw = $("#edit-fig-weight").value.trim();
  const figWeightG = figWeightRaw ? Number.parseFloat(figWeightRaw.replace(",", ".")) : null;
  if (!Number.isInteger(totalCount) || totalCount < 0) {
    return toast("Geçersiz toplam", "Toplam ürün sayısı sıfır veya pozitif bir tam sayı olmalıdır.", "error");
  }
  if (!Number.isInteger(defectCount) || defectCount < 0 || defectCount > totalCount) {
    return toast("Geçersiz aflatoksin sayısı", "Aflatoksin sayısı toplam ürün sayısını aşamaz.", "error");
  }
  if (figWeightG !== null && (!Number.isFinite(figWeightG) || figWeightG <= 0 || figWeightG > 1000)) {
    return toast("Geçersiz gramaj", "Bir ürün gramajı 0 ile 1000 gram arasında olmalıdır.", "error");
  }
  setButtonLoading(button, true, "Kaydediliyor…");
  try {
    const updated = await api(`/sessions/${item.uuid}`, {
      method: "PATCH",
      body: JSON.stringify({
        batch_id: batchId,
        device_label: deviceLabel || null,
        total_count: totalCount,
        defect_count: defectCount,
        fig_weight_g: figWeightG,
      }),
    });
    state.recentSessions = state.recentSessions.map((session) => session.uuid === updated.uuid ? updated : session);
    state.history = state.history.map((session) => session.uuid === updated.uuid ? updated : session);
    renderRecentSessions(state.recentSessions);
    if (state.page === "history") renderHistory();
    state.dashboard.metricsAt = 0;
    void loadDashboardMetrics(true);
    closeSessionEditor();
    toast("Kayıt güncellendi", updated.batch_id);
  } catch (error) {
    toast("Kayıt güncellenemedi", error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

async function deleteSession(item, button) {
  if (item.is_open) return toast("Oturum açık", "Devam eden tarama silinemez.", "error");
  if (!window.confirm(`${item.batch_id} kaydı ve bu kayda ait görüntüler kalıcı olarak silinsin mi?`)) return;
  setButtonLoading(button, true, "…");
  try {
    await api(`/sessions/${item.uuid}`, { method: "DELETE" });
    state.recentSessions = state.recentSessions.filter((session) => session.uuid !== item.uuid);
    state.history = state.history.filter((session) => session.uuid !== item.uuid);
    renderRecentSessions(state.recentSessions);
    if (state.page === "history") renderHistory();
    state.dashboard.metricsAt = 0;
    await loadDashboardMetrics(true);
    toast("Tarama kaydı silindi", item.batch_id);
  } catch (error) {
    toast("Kayıt silinemedi", error.message, "error");
    renderRecentSessions(state.recentSessions);
  }
}

/* Live scanning */
async function startScan() {
  const button = $("#start-scan");
  if (!navigator.mediaDevices?.getUserMedia) return toast("Kamera desteklenmiyor", "Tarayıcınız kamera erişimi sunmuyor.", "error");
  setButtonLoading(button, true, "Kamera açılıyor…");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    const video = $("#camera-video");
    video.srcObject = stream;
    await video.play();
    state.scan.stream = stream;

    let session;
    try {
      session = await api("/sessions", {
        method: "POST",
        body: JSON.stringify({
          conf_threshold: Number($("#confidence-range").value),
          device_label: `${navigator.platform || "Web"} tarayıcı`,
          fig_weight_g: state.scan.figWeightG,
        }),
      });
    } catch (error) {
      if (error.code !== "SESSION_ALREADY_OPEN" || !error.detail?.session_uuid) throw error;
      const existing = await api(`/sessions/${error.detail.session_uuid}`);
      session = { ...existing.session, ws_url: `/api/v1/ws/scan/${existing.session.uuid}` };
      toast("Açık oturum bulundu", `${existing.session.batch_id} oturumuna devam ediliyor.`);
    }

    state.scan.session = session;
    if (session.fig_weight_g) state.scan.figWeightG = session.fig_weight_g;
    state.scan.counts = { recorded: session.total_count || 0, healthy: 0, aflatoxin: session.defect_count || 0, processed: 0, dropped: 0 };
    state.scan.counts.healthy = Math.max(0, state.scan.counts.recorded - state.scan.counts.aflatoxin);
    updateLiveCounters();
    $("#hud-batch").textContent = `Parti: ${session.batch_id}`;
    $("#camera-placeholder").classList.add("hidden");

    const ticket = await api(`/sessions/${session.uuid}/ticket`, { method: "POST" });
    await connectScanSocket(ticket);
    $("#start-scan").classList.add("hidden");
    $("#pause-scan").classList.remove("hidden");
    $("#stop-scan").classList.remove("hidden");
    $("#scan-badge").classList.add("active");
    $("#scan-badge").innerHTML = "<span></span>Canlı";
    toast("Tarama başladı", `Parti ${session.batch_id}`);
  } catch (error) {
    stopMediaOnly();
    toast("Tarama başlatılamadı", error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function connectScanSocket(ticket) {
  return new Promise((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${location.host}${ticket.ws_url}?ticket=${encodeURIComponent(ticket.ticket)}`;
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    state.scan.socket = socket;
    socket.onopen = () => {
      state.scan.paused = false;
      state.scan.waiting = false;
      scheduleFrame(150);
      resolve();
    };
    socket.onerror = () => reject(new Error("Canlı tarama bağlantısı kurulamadı."));
    socket.onclose = (event) => {
      clearTimeout(state.scan.timer);
      if (!state.scan.stopping && state.scan.session) {
        $("#scan-badge").classList.remove("active");
        $("#scan-badge").innerHTML = "<span></span>Bağlantı kesildi";
        toast("Tarama bağlantısı kapandı", event.reason || `Kod: ${event.code}`, "error");
      }
    };
    socket.onmessage = handleScanMessage;
  });
}

function scheduleFrame(delay = 70) {
  clearTimeout(state.scan.timer);
  if (!state.scan.paused && !state.scan.stopping && state.scan.socket?.readyState === WebSocket.OPEN) {
    state.scan.timer = setTimeout(sendFrame, delay);
  }
}

async function sendFrame() {
  if (state.scan.waiting || state.scan.paused || state.scan.socket?.readyState !== WebSocket.OPEN) return;
  const video = $("#camera-video");
  if (!video.videoWidth) return scheduleFrame(100);
  const canvas = $("#capture-canvas");
  const maxWidth = 960;
  const scale = Math.min(1, maxWidth / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .72));
  if (!blob) return scheduleFrame(100);
  state.scan.waiting = true;
  state.scan.socket.send(await blob.arrayBuffer());
}

function handleScanMessage(event) {
  let payload;
  try { payload = JSON.parse(event.data); } catch (_) { return; }
  switch (payload.type) {
    case "frame":
      state.scan.waiting = false;
      state.scan.counts.processed = payload.stats?.processed ?? state.scan.counts.processed + 1;
      state.scan.counts.dropped = payload.stats?.dropped ?? state.scan.counts.dropped;
      drawDetections(payload.detections || []);
      $("#hud-fps").textContent = `${Number(payload.stats?.effective_fps || 0).toFixed(1)} FPS`;
      $("#hud-latency").textContent = `Gecikme: ${Number(payload.latency_ms || 0).toFixed(0)} ms`;
      $("#hud-detections").textContent = `Tespit: ${(payload.detections || []).length}`;
      updateLiveCounters();
      scheduleFrame();
      break;
    case "dropped":
      state.scan.waiting = false;
      state.scan.counts.dropped += 1;
      updateLiveCounters();
      scheduleFrame(90);
      break;
    case "inspection":
      addInspection(payload);
      break;
    case "stats":
      state.scan.counts = { ...state.scan.counts, ...payload };
      updateLiveCounters();
      break;
    case "error":
      state.scan.waiting = false;
      toast("Kare işlenemedi", payload.message || payload.code, "error");
      scheduleFrame(150);
      break;
  }
}

function drawDetections(detections) {
  const video = $("#camera-video");
  const canvas = $("#overlay-canvas");
  const rect = video.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const context = canvas.getContext("2d");
  context.scale(dpr, dpr);
  context.clearRect(0, 0, rect.width, rect.height);

  const sourceRatio = video.videoWidth / video.videoHeight;
  const targetRatio = rect.width / rect.height;
  let displayWidth, displayHeight, offsetX, offsetY;
  if (sourceRatio > targetRatio) {
    displayWidth = rect.width;
    displayHeight = rect.width / sourceRatio;
    offsetX = 0;
    offsetY = (rect.height - displayHeight) / 2;
  } else {
    displayHeight = rect.height;
    displayWidth = rect.height * sourceRatio;
    offsetY = 0;
    offsetX = (rect.width - displayWidth) / 2;
  }

  detections.forEach((detection) => {
    const [x1, y1, x2, y2] = detection.bbox;
    const x = offsetX + x1 * displayWidth;
    const y = offsetY + y1 * displayHeight;
    const width = (x2 - x1) * displayWidth;
    const height = (y2 - y1) * displayHeight;
    const danger = detection.class_name === "Aflatoxin";
    const color = danger ? "#ef6f61" : "#67d39a";
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.strokeRect(x, y, width, height);
    const label = `${danger ? "Aflatoksin" : "Sağlıklı"} ${(detection.confidence * 100).toFixed(0)}%`;
    context.font = "600 12px system-ui";
    const labelWidth = context.measureText(label).width + 14;
    context.fillStyle = color;
    context.fillRect(x, Math.max(0, y - 24), labelWidth, 22);
    context.fillStyle = "#10201b";
    context.fillText(label, x + 7, Math.max(15, y - 8));
  });
}

function addInspection(payload) {
  state.scan.counts.recorded += 1;
  if (payload.decision === "Aflatoxin") state.scan.counts.aflatoxin += 1;
  else state.scan.counts.healthy += 1;
  updateLiveCounters();
  void loadScanAdvice(payload.decision, payload.confidence);
}

function updateLiveCounters() {
  const counts = state.scan.counts;
  $("#live-recorded").textContent = counts.recorded || 0;
  $("#live-healthy").textContent = counts.healthy || 0;
  $("#live-aflatoxin").textContent = counts.aflatoxin || 0;
  const healthyRate = counts.recorded ? (counts.healthy / counts.recorded) * 100 : 0;
  $("#live-health-bar").style.width = `${healthyRate}%`;
  $("#live-dropped").textContent = `İşlenen: ${counts.processed || 0} · Atlanan: ${counts.dropped || 0}`;

  const weight = Number(state.scan.figWeightG);
  const totalKg = Number.isFinite(weight) && weight > 0
    ? ((counts.recorded || 0) * weight) / 1000
    : null;
  $("#live-total-kg").textContent = totalKg === null
    ? "—"
    : `${totalKg.toLocaleString("tr-TR", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} kg`;
}

function updateFigWeight(value) {
  const parsed = Number.parseFloat(String(value).replace(",", "."));
  state.scan.figWeightG = Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  if (state.scan.figWeightG) localStorage.setItem("agrovision_fig_weight_g", String(state.scan.figWeightG));
  else localStorage.removeItem("agrovision_fig_weight_g");
  updateLiveCounters();
}

async function loadScanAdvice(decision, confidence) {
  const container = $("#scan-advice");
  const cached = state.scan.adviceCache[decision];
  if (cached) {
    renderScanAdvice(cached);
    return;
  }

  // Advice depends primarily on the decision. At most one request per class is allowed for a
  // scan, so hundreds of detections cannot create hundreds of Gemini/vector-store calls.
  if (state.scan.adviceRequestKey === decision) return;
  const sessionUuid = state.scan.session?.uuid;
  state.scan.adviceRequestKey = decision;
  container.className = "advice-empty";
  container.innerHTML = "<span>✦</span><p>Kaynaklar taranıyor ve öneri hazırlanıyor…</p>";
  try {
    const result = await api("/rag/inspection-advice", {
      method: "POST",
      body: JSON.stringify({ decision, confidence }),
    });
    state.scan.adviceCache[decision] = result;
    if (state.scan.session?.uuid === sessionUuid) renderScanAdvice(result);
  } catch (error) {
    if (state.scan.session?.uuid === sessionUuid) {
      container.className = "advice-empty";
      container.innerHTML = `<span>!</span><p>${escapeHtml(error.message)}</p>`;
    }
  } finally {
    if (state.scan.adviceRequestKey === decision) state.scan.adviceRequestKey = null;
  }
}

function renderScanAdvice(result) {
  const container = $("#scan-advice");
  container.className = "advice-content";
  const seen = new Set();
  const sourceChips = result.sources
    .filter((source) => {
      if (seen.has(source.source)) return false;
      seen.add(source.source);
      return true;
    })
    .map((source) => source.url
      ? `<span class="advice-source-link" data-doc-url="${escapeHtml(source.url)}" role="button" tabindex="0">${escapeHtml(source.source)}</span>`
      : escapeHtml(source.source))
    .join(" · ");
  container.innerHTML = `<p>${escapeHtml(result.answer)}</p>${sourceChips ? `<div class="advice-sources">${sourceChips}</div>` : ""}`;
  $$('[data-doc-url]', container).forEach((el) => {
    const open = () => openRagSource(el.dataset.docUrl);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
  });
}

function togglePause() {
  const scan = state.scan;
  if (!scan.socket || scan.socket.readyState !== WebSocket.OPEN) return;
  scan.paused = !scan.paused;
  scan.socket.send(JSON.stringify({ type: scan.paused ? "pause" : "resume" }));
  $("#pause-scan").textContent = scan.paused ? "Devam et" : "Duraklat";
  $("#scan-badge").innerHTML = `<span></span>${scan.paused ? "Duraklatıldı" : "Canlı"}`;
  if (!scan.paused) scheduleFrame(50);
  else clearTimeout(scan.timer);
}

async function stopScan() {
  if (!state.scan.session || state.scan.stopping) return;
  const button = $("#stop-scan");
  setButtonLoading(button, true, "Oturum kapatılıyor…");
  state.scan.stopping = true;
  clearTimeout(state.scan.timer);
  state.scan.socket?.close(1000, "Session stopped");
  stopMediaOnly();
  try {
    const detail = await api(`/sessions/${state.scan.session.uuid}/stop`, { method: "POST" });
    const activeWeight = state.scan.figWeightG;
    if (activeWeight && Number(detail.session.fig_weight_g) !== Number(activeWeight)) {
      try {
        await api(`/sessions/${detail.session.uuid}`, {
          method: "PATCH",
          body: JSON.stringify({
            batch_id: detail.session.batch_id,
            device_label: detail.session.device_label,
            fig_weight_g: activeWeight,
          }),
        });
      } catch (weightError) {
        toast("Gramaj kaydedilemedi", weightError.message, "error");
      }
    }
    toast("Oturum tamamlandı", `${detail.summary.total} ürün kaydedildi.`);
  } catch (error) {
    if (error.code !== "SESSION_CLOSED") toast("Oturum kapatılamadı", error.message, "error");
  } finally {
    resetScannerUi();
    setButtonLoading(button, false);
    loadDashboard(true);
  }
}

function stopMediaOnly() {
  state.scan.stream?.getTracks().forEach((track) => track.stop());
  state.scan.stream = null;
  $("#camera-video").srcObject = null;
  const canvas = $("#overlay-canvas");
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

function resetScannerUi() {
  const figWeightG = state.scan.figWeightG;
  state.scan = {
    session: null, socket: null, stream: null, timer: null,
    waiting: false, paused: false, stopping: false,
    figWeightG,
    adviceCache: {},
    adviceRequestKey: null,
    counts: { recorded: 0, healthy: 0, aflatoxin: 0, processed: 0, dropped: 0 },
  };
  $("#camera-placeholder").classList.remove("hidden");
  $("#start-scan").classList.remove("hidden");
  $("#pause-scan").classList.add("hidden");
  $("#stop-scan").classList.add("hidden");
  $("#pause-scan").textContent = "Duraklat";
  $("#scan-badge").classList.remove("active");
  $("#scan-badge").innerHTML = "<span></span>Hazır";
  $("#hud-batch").textContent = "Parti: —";
  $("#hud-fps").textContent = "0.0 FPS";
  $("#hud-latency").textContent = "Gecikme: —";
  $("#hud-detections").textContent = "Tespit: 0";
  $("#scan-advice").className = "advice-empty";
  $("#scan-advice").innerHTML = '<span>✦</span><p>Bir ürün kaydedildiğinde sonuca özel kaynak-temelli öneri burada gösterilir.</p>';
  updateLiveCounters();
}

/* RAG assistant */
function appendMessage(role, text, sources = [], loading = false) {
  const wrapper = document.createElement("div");
  wrapper.className = `message message--${role}`;
  const sourcesHtml = sources.length
    ? `<div class="message-sources">${sources.map((source) => `<div class="message-source${source.url ? " message-source--linked" : ""}"${source.url ? ` data-doc-url="${escapeHtml(source.url)}" role="button" tabindex="0"` : ""}><b>${escapeHtml(source.source)}${source.page ? ` · s. ${source.page}` : ""}</b>${escapeHtml(source.snippet)}</div>`).join("")}</div>`
    : "";
  wrapper.innerHTML = `${role === "assistant" ? '<div class="message-avatar">✦</div>' : ""}<div class="message-bubble"><p>${loading ? "Kaynaklarda aranıyor…" : escapeHtml(text)}</p>${sourcesHtml}</div>`;
  $$('[data-doc-url]', wrapper).forEach((el) => {
    const open = () => openRagSource(el.dataset.docUrl);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
  });
  $("#chat-messages").append(wrapper);
  $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
  return wrapper;
}

async function askAssistant(question) {
  question = question.trim();
  if (!question) return;
  appendMessage("user", question);
  const pending = appendMessage("assistant", "", [], true);
  $("#chat-input").value = "";
  try {
    const result = await api("/rag/query", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    pending.remove();
    appendMessage("assistant", result.answer, result.sources);
  } catch (error) {
    pending.remove();
    appendMessage("assistant", `Yanıt oluşturulamadı: ${error.message}`);
  }
}


/* History */
async function loadHistory(reset = false) {
  if (state.historyLoading && !reset) return;
  const requestVersion = reset ? ++state.historyVersion : state.historyVersion;
  if (reset) {
    state.history = [];
    state.historyCursor = null;
    $("#history-body").innerHTML = '<tr><td colspan="7" class="empty-cell">Oturumlar yükleniyor…</td></tr>';
  }

  state.historyLoading = true;
  setButtonLoading($("#load-more-history"), true, "Yükleniyor…");
  try {
    const cursor = state.historyCursor ? `&cursor=${state.historyCursor}` : "";
    const page = await api(`/sessions?limit=25${cursor}`);
    if (requestVersion !== state.historyVersion) return;
    const known = new Set(state.history.map((item) => item.uuid));
    state.history.push(...page.items.filter((item) => !known.has(item.uuid)));
    state.historyCursor = page.next_cursor;
    renderHistory();
    $("#load-more-history").classList.toggle("hidden", !page.next_cursor);
  } catch (error) {
    if (requestVersion === state.historyVersion) {
      $("#history-body").innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  } finally {
    if (requestVersion === state.historyVersion) {
      state.historyLoading = false;
      setButtonLoading($("#load-more-history"), false);
    }
  }
}

function renderHistory() {
  const query = $("#history-search").value.trim().toLocaleLowerCase("tr-TR");
  const items = state.history.filter((item) => !query || item.batch_id.toLocaleLowerCase("tr-TR").includes(query));
  const body = $("#history-body");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">Eşleşen oturum bulunamadı.</td></tr>';
    return;
  }
  body.innerHTML = items.map((item) => `<tr data-session="${item.uuid}">
      <td><strong>${escapeHtml(item.batch_id)}</strong><br><small>${escapeHtml(item.device_label || "Web kamera")}</small></td>
      <td>${formatDate(item.start_time)}</td>
      <td>${item.total_count}${item.is_manually_corrected ? '<small class="correction-tag">Kullanıcı girişi</small>' : ""}</td>
      <td>${healthyCount(item)}</td>
      <td><span class="${item.defect_count > 0 ? "text-danger" : "text-good"}">${item.defect_count}</span></td>
      <td>${formatKg(item.total_kg)}</td>
      <td><button class="row-action" aria-label="Detay">→</button></td>
    </tr>`).join("");
  $$('tr[data-session]', body).forEach((row) => row.addEventListener("click", () => loadSessionDetail(row.dataset.session)));
}

function loadSessionDetail(uuid) {
  const container = $("#session-detail");
  const item = state.history.find((session) => session.uuid === uuid);
  if (!item) {
    container.innerHTML = '<div class="empty-state"><span>!</span><h3>Kayıt bulunamadı</h3><p>Listeyi yenileyip tekrar deneyin.</p></div>';
    return;
  }

  const rate = item.total_count ? (item.defect_count / item.total_count) * 100 : 0;
  container.innerHTML = `
      <div class="detail-head"><span class="eyebrow">Parti raporu</span><h3>${escapeHtml(item.batch_id)}</h3><p>${formatDate(item.start_time)} · ${item.is_open ? "Açık oturum" : "Tamamlandı"}</p></div>
      <div class="detail-metrics">
        <div class="detail-metric"><span>Toplam ürün</span><strong>${item.total_count}</strong></div>
        <div class="detail-metric"><span>Sağlıklı ürün</span><strong class="text-good">${healthyCount(item)}</strong></div>
        <div class="detail-metric"><span>Aflatoksinli ürün</span><strong class="${item.defect_count ? "text-danger" : "text-good"}">${item.defect_count}</strong></div>
        <div class="detail-metric"><span>Aflatoksin oranı</span><strong class="${rate ? "text-danger" : "text-good"}">${percent(rate)}</strong></div>
        <div class="detail-metric"><span>Toplam KG</span><strong>${formatKg(item.total_kg)}</strong></div>
        <div class="detail-metric"><span>Rapor verisi</span><strong>${item.is_manually_corrected ? "Kullanıcı + model" : "Model"}</strong></div>
      </div>
      <div class="detail-notes"><h4>PDF rapor seçenekleri</h4><ul><li>Model raporu ham tespit adetlerini, kullanıcı raporu ise düzenleme ekranında girilen adetleri kullanır.</li></ul></div>
      <div class="detail-actions detail-actions--reports">
        <button class="button button--secondary" data-download="csv">CSV indir</button>
        <button class="button button--secondary" data-download="pdf-model">PDF - Model</button>
        <button class="button button--primary" data-download="pdf-user" ${item.is_manually_corrected ? "" : 'disabled title="Önce kaydı düzenleyerek manuel adet girin"'}>PDF - Kullanıcı</button>
      </div>`;
  $$('[data-download]', container).forEach((button) => button.addEventListener("click", () => {
    const type = button.dataset.download;
    if (type === "csv") {
      downloadAuthenticated(`/sessions/${uuid}/export.csv`, `${item.batch_id}.csv`);
      return;
    }
    const source = type === "pdf-user" ? "user" : "model";
    downloadAuthenticated(`/sessions/${uuid}/report.pdf?source=${source}`, `${item.batch_id}_${source === "user" ? "kullanici" : "model"}_raporu.pdf`);
  }));
}

async function downloadAuthenticated(path, filename) {
  try {
    let response = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${state.tokens.access_token}` } });
    if (response.status === 401 && await refreshAccessToken()) {
      response = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${state.tokens.access_token}` } });
    }
    if (!response.ok) throw new Error(`Dosya indirilemedi (${response.status})`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast("İndirme başarısız", error.message, "error");
  }
}

// RAG source links point at an authenticated endpoint (e.g. "/rag/documents/foo.pdf#page=3"),
// so a plain <a href> can't be used without sending a token. This mirrors downloadAuthenticated
// but opens the file for viewing in a new tab instead of forcing a download.
async function openRagSource(path) {
  const [base, hash] = path.split("#");
  try {
    let response = await fetch(`${API_BASE}${base}`, { headers: { Authorization: `Bearer ${state.tokens.access_token}` } });
    if (response.status === 401 && await refreshAccessToken()) {
      response = await fetch(`${API_BASE}${base}`, { headers: { Authorization: `Bearer ${state.tokens.access_token}` } });
    }
    if (!response.ok) throw new Error(`Belge açılamadı (${response.status})`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    window.open(hash ? `${url}#${hash}` : url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    toast("Belge açılamadı", error.message, "error");
  }
}

async function logout() {
  try { await api("/auth/logout-all", { method: "POST" }); } catch (_) { /* local logout continues */ }
  if (state.scan.session) {
    state.scan.stopping = true;
    state.scan.socket?.close();
    stopMediaOnly();
  }
  saveTokens(null);
  state.user = null;
  showAuth();
}

function bindEvents() {
  $("#auth-form").addEventListener("submit", handleAuthSubmit);
  $("#auth-switch").addEventListener("click", () => { state.authMode = state.authMode === "login" ? "register" : "login"; updateAuthMode(); });
  $("#toggle-password").addEventListener("click", () => {
    const input = $("#password");
    input.type = input.type === "password" ? "text" : "password";
    $("#toggle-password").textContent = input.type === "password" ? "Göster" : "Gizle";
  });
  $$("[data-page]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
  $$("[data-go]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.go)));
  $("#mobile-menu").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
  $("#logout-button").addEventListener("click", logout);
  $("#start-scan").addEventListener("click", startScan);
  $("#pause-scan").addEventListener("click", togglePause);
  $("#stop-scan").addEventListener("click", stopScan);
  $("#fig-weight-input").addEventListener("input", (event) => updateFigWeight(event.target.value));
  $("#session-edit-form").addEventListener("submit", submitSessionEdit);
  $("#close-session-edit").addEventListener("click", closeSessionEditor);
  $("#cancel-session-edit").addEventListener("click", closeSessionEditor);
  $("#session-edit-modal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeSessionEditor();
  });
  $("#confidence-range").addEventListener("input", (event) => {
    const value = Number(event.target.value);
    $("#confidence-value").textContent = percent(value * 100, 0);
    if (state.scan.socket?.readyState === WebSocket.OPEN) state.scan.socket.send(JSON.stringify({ type: "set_conf", value }));
  });
  $("#chat-form").addEventListener("submit", (event) => { event.preventDefault(); askAssistant($("#chat-input").value); });
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); askAssistant(event.target.value); }
  });
  $$("[data-question]").forEach((button) => button.addEventListener("click", () => askAssistant(button.dataset.question)));
  $("#refresh-history").addEventListener("click", () => loadHistory(true));
  $("#load-more-history").addEventListener("click", () => loadHistory(false));
  $("#history-search").addEventListener("input", renderHistory);
  window.addEventListener("resize", () => { if (state.scan.socket) drawDetections([]); });
}

async function bootstrap() {
  bindEvents();
  updateAuthMode();
  setInterval(() => { $("#live-clock").textContent = new Intl.DateTimeFormat("tr-TR", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date()); }, 1000);
  $("#live-clock").textContent = formatDate(new Date());
  if (state.scan.figWeightG) $("#fig-weight-input").value = String(state.scan.figWeightG);
  updateLiveCounters();
  if (!state.tokens) return showAuth();
  try {
    state.user = await api("/me");
    await initializeApp();
  } catch (_) {
    saveTokens(null);
    showAuth();
  }
}

document.addEventListener("DOMContentLoaded", bootstrap);
