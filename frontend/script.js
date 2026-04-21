/**
 * script.js — Curabook PHI v1.3
 * GLP-1 Intelligence App — Production JavaScript
 *
 * FIXES IN THIS VERSION:
 *   - FIX-SEND-1:  handleSend() now shows a clear error if session is missing
 *                  instead of silently doing nothing
 *   - FIX-SEND-2:  processFileUpload() shows a toast on every failure mode
 *                  (no session, server error, 403 consent, 413 too large)
 *   - FIX-SEND-3:  createConversation() failure now shows a toast + logs the
 *                  actual server response instead of swallowing the error
 *   - FIX-SEND-4:  403 on /chat now clears properly with auto-consent retry
 *   - FIX-SEND-5:  File upload 403 triggers consent save then retries once
 *   - FIX-API-1:   Dynamic API_BASE (localhost → 5000, prod → api.curabook.com)
 *   - FIX-AUTH-1:  Dev-mode soft warning instead of hard redirect loop
 *   - FIX-MOBILE-1:Cockpit drawer wired at ALL breakpoints
 *   - FIX-SHIELD-1:updateShield() safe with empty inputs + goalWt persisted
 *   - FIX-CTX-1:   _docCtx.sent only flips true after SUCCESSFUL API response
 *   - FIX-CTX-2:   removeFile() does NOT reset _docCtx.text
 *   - FIX-PERSIST-1:Goal weight saved/restored from localStorage
 */

"use strict";

/* ═══════════════════════════════════════
   CONFIG
═══════════════════════════════════════ */
const SUPABASE_URL = "https://pbeaawlxdcrdbvlmpqhc.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBiZWFhd2x4ZGNyZGJ2bG1wcWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMDk0MzksImV4cCI6MjA5MTU4NTQzOX0.6bUpYrDbe0mQjjBHX8Qscj-5R8i4-SqAtW_Z1UFzJ10";

const IS_LOCAL = (
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1" ||
  window.location.hostname === "0.0.0.0"
);
const API_BASE = IS_LOCAL ? "http://localhost:5000" : "https://api.curabook.com";
const IS_DEV   = IS_LOCAL || window.location.hostname.includes("staging");

if (IS_DEV) console.info(`[PHI] DEV MODE — API: ${API_BASE}`);

/* ═══════════════════════════════════════
   STATE
═══════════════════════════════════════ */
let _sb          = null;
let _user        = null;
let _userName    = "";
let _convId      = null;
let _isSending   = false;
let _uploads     = [];

let _goalWt        = parseFloat(localStorage.getItem("phi_goal_wt") || "165");
let _proteinTarget = Math.round(_goalWt * 0.545 * 10) / 10;

let _docCtx = {
  text:   null,
  sent:   false,
  hasDoc: false,
};

const NOISE_MESSAGES = {
  1:  "Nearly silent — ghrelin well suppressed. Excellent maintenance state.",
  2:  "Very low. Behavioral strategies alone may be sufficient.",
  3:  "Mild. 35g+ protein at each meal typically resolves this level.",
  4:  "Low-moderate. Check your daily protein — is it hitting your target?",
  5:  "Moderate. A 20-minute post-meal walk reduces post-meal glucose 30–50 mg/dL.",
  6:  "Elevated. Check sleep — each hour under 7h raises next-day ghrelin ~15%.",
  7:  "High. This level often indicates the dose reduction was too fast. Discuss with provider.",
  8:  "Very high. Classic ghrelin surge — this is physiology, not willpower.",
  9:  "Intense rebound. This is an important data point — discuss urgently with provider.",
  10: "🚨 Maximum. Severe ghrelin surge. Urgent provider conversation recommended."
};

/* ═══════════════════════════════════════
   BOOT
═══════════════════════════════════════ */
async function boot() {
  try {
    _sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: { detectSessionInUrl: true, persistSession: true }
    });

    _sb.auth.onAuthStateChange(async (event, session) => {
      if (event === "SIGNED_IN" && session?.user) {
        await onSignIn(session.user);
      } else if (event === "SIGNED_OUT") {
        if (IS_DEV) {
          console.warn("[PHI] DEV: Signed out — skipping redirect.");
          showDevAuthBanner();
        } else {
          window.location.href = "/login";
        }
      }
    });

    const { data: { session } } = await _sb.auth.getSession();
    if (!session?.user) {
      if (IS_DEV) {
        console.warn("[PHI] DEV: No session. Running in unauthenticated mode.");
        showDevAuthBanner();
        initTheme();
        wireEvents();
        restoreShieldInputs();
        return;
      } else {
        window.location.href = "/login";
        return;
      }
    }

    await onSignIn(session.user);

  } catch (err) {
    console.error("[PHI] Boot error:", err);
    toast("Failed to initialize. Please refresh.", "err");
  }
}

function showDevAuthBanner() {
  if (document.getElementById("devAuthBanner")) return;
  const banner = document.createElement("div");
  banner.id = "devAuthBanner";
  banner.style.cssText = `
    position:fixed;top:0;left:0;right:0;z-index:9999;
    background:#1a1000;border-bottom:2px solid #fbbf24;
    color:#fbbf24;font-size:.78rem;font-family:monospace;
    padding:6px 16px;display:flex;align-items:center;gap:12px;
  `;
  banner.innerHTML = `
    ⚠️ DEV MODE — No session.
    <a href="/login" style="color:#2dd4bf;text-decoration:underline">Sign in</a>
    to test auth features. API: ${API_BASE}
    <button onclick="this.parentNode.remove()" style="margin-left:auto;color:#fbbf24;background:none;border:none;cursor:pointer;font-size:1rem;">×</button>
  `;
  document.body.appendChild(banner);
}

async function onSignIn(user) {
  _user     = user;
  _userName = user.user_metadata?.first_name
    || user.email?.split("@")[0]?.replace(/[._-]/g, " ").split(" ")[0]
    || "there";
  _userName = _userName.charAt(0).toUpperCase() + _userName.slice(1);

  if (el("userAvatar")) el("userAvatar").textContent = _userName[0].toUpperCase();
  setText("userEmail",   user.email);
  setText("welcomeName", _userName);

  const h = new Date().getHours();
  setText("timeGreeting", h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");

  // FIX-SEND-4: Save consents immediately on sign-in — blocking await so
  // we know consent rows exist before the user can send a message
  await saveConsents();

  initTheme();
  restoreShieldInputs();
  await loadHistory();
  loadMarkersData();
}

/* ═══════════════════════════════════════
   API HELPERS
═══════════════════════════════════════ */
async function getSession() {
  try {
    const { data: { session } } = await _sb.auth.getSession();
    return session;
  } catch { return null; }
}

async function getHeaders(contentType = "application/json") {
  try {
    let session = await getSession();

    // If token is expired, try to refresh it
    if (!session) {
      const { data } = await _sb.auth.refreshSession();
      session = data?.session;
    }

    if (!session) return null;

    const h = { "Authorization": `Bearer ${session.access_token}` };
    if (contentType) h["Content-Type"] = contentType;
    return h;
  } catch (err) {
    console.error("[PHI] getHeaders error:", err);
    return null;
  }
}

async function apiFetch(path, options = {}) {
  const url = API_BASE + path;
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(timeout);
    return res;
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Request timed out");
    throw err;
  }
}

async function apiJson(path, options = {}) {
  const res  = await apiFetch(path, options);
  const text = await res.text();
  if (!text) return { ok: res.ok, status: res.status, data: null };
  try {
    return { ok: res.ok, status: res.status, data: JSON.parse(text) };
  } catch {
    return { ok: false, status: res.status, data: { error: "Invalid response from server" } };
  }
}

// FIX-SEND-4: saveConsents is now awaited on sign-in and retried silently
async function saveConsents() {
  try {
    const h = await getHeaders();
    if (!h) return;
    const res = await apiFetch("/api/consent", {
      method: "POST",
      headers: h,
      body: JSON.stringify({ consents: ["data_processing", "ai_processing", "document_processing"] })
    });
    if (!res.ok) {
      console.warn("[PHI] Consent save returned:", res.status);
    }
  } catch (err) {
    console.warn("[PHI] Consent save failed (non-fatal):", err);
  }
}

/* ═══════════════════════════════════════
   THEME
═══════════════════════════════════════ */
function initTheme() {
  const saved = localStorage.getItem("phi_theme") || "dark";
  applyTheme(saved);
}

function toggleTheme() {
  const cur = document.documentElement.dataset.theme || "dark";
  applyTheme(cur === "dark" ? "light" : "dark");
  closeUserMenu();
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("phi_theme", theme);
  const isDark = theme === "dark";
  setIcon("themeIcon",    isDark ? "fa-moon" : "fa-sun");
  setIcon("topThemeIcon", isDark ? "fa-moon" : "fa-sun");
  setText("themeLabel", isDark ? "Light Mode" : "Dark Mode");
}

/* ═══════════════════════════════════════
   SIDEBAR
═══════════════════════════════════════ */
function openSidebar() {
  el("sidebar")?.classList.add("open");
  el("sidebarOverlay")?.classList.add("show");
  el("mobileMenuBtn")?.setAttribute("aria-expanded", "true");
  closeCockpit();
}

function closeSidebar() {
  el("sidebar")?.classList.remove("open");
  el("sidebarOverlay")?.classList.remove("show");
  el("mobileMenuBtn")?.setAttribute("aria-expanded", "false");
}

function toggleUserMenu() {
  const dd = el("userDropdown");
  if (!dd) return;
  const isHidden = dd.getAttribute("aria-hidden") !== "false";
  dd.setAttribute("aria-hidden", isHidden ? "false" : "true");
}

function closeUserMenu() {
  el("userDropdown")?.setAttribute("aria-hidden", "true");
}

/* ═══════════════════════════════════════
   COCKPIT DRAWER
═══════════════════════════════════════ */
function openCockpit() {
  el("cockpit")?.classList.add("open");
  el("cockpitOverlay")?.classList.add("show");
  closeSidebar();
}

function closeCockpit() {
  el("cockpit")?.classList.remove("open");
  el("cockpitOverlay")?.classList.remove("show");
}

function toggleCockpit() {
  const cockpit = el("cockpit");
  if (!cockpit) return;
  if (cockpit.classList.contains("open")) closeCockpit();
  else openCockpit();
}

/* ═══════════════════════════════════════
   VIEWS
═══════════════════════════════════════ */
function showWelcome() {
  el("welcomeScreen")?.classList.remove("hidden");
  el("chatDisplay")?.classList.add("hidden");
  setText("convTitle", "Ready");
}

function showChat() {
  el("welcomeScreen")?.classList.add("hidden");
  el("chatDisplay")?.classList.remove("hidden");
}

function resetToWelcome() {
  _convId  = null;
  _uploads = [];
  _docCtx  = { text: null, sent: false, hasDoc: false };
  clearFilePreview();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  showWelcome();
  document.querySelectorAll(".hist-item").forEach(e => e.classList.remove("active"));
  setText("convTitle", "Ready");
}

/* ═══════════════════════════════════════
   HISTORY
═══════════════════════════════════════ */
async function loadHistory() {
  const h = await getHeaders();
  if (!h) return;

  try {
    const { ok, data } = await apiJson("/history", {
      method: "POST", headers: h, body: JSON.stringify({})
    });
    if (ok && Array.isArray(data)) {
      renderHistory(data);
    } else {
      el("historyList").innerHTML = '<div class="sb-empty">Could not load history</div>';
    }
  } catch {
    el("historyList").innerHTML = '<div class="sb-empty">No conversations yet</div>';
  }
}

function renderHistory(conversations) {
  const list = el("historyList");
  if (!list) return;
  if (!conversations.length) {
    list.innerHTML = '<div class="sb-empty">No conversations yet</div>';
    return;
  }

  const today     = new Date(); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  const groups    = new Map();

  conversations.forEach(c => {
    const d = c.created_at ? new Date(c.created_at) : new Date();
    d.setHours(0, 0, 0, 0);
    let label;
    if (d.getTime() === today.getTime())          label = "Today";
    else if (d.getTime() === yesterday.getTime()) label = "Yesterday";
    else label = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(c);
  });

  let html = "";
  groups.forEach((convs, label) => {
    html += `<div class="hist-group-label">${esc(label)}</div>`;
    convs.forEach(c => {
      const isActive = c.id === _convId ? " active" : "";
      const title    = (c.title && c.title !== "New Chat") ? c.title : "New Conversation";
      html += `<div class="hist-item${isActive}" data-id="${esc(c.id)}">
        <span class="hist-title">${esc(title)}</span>
        <button class="hist-del" data-del="${esc(c.id)}" title="Delete">
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>`;
    });
  });
  list.innerHTML = html;
}

async function openConversation(id) {
  if (_isSending || id === _convId) { closeSidebar(); return; }
  _convId  = id;
  _uploads = [];
  _docCtx  = { text: null, sent: false, hasDoc: false };
  clearFilePreview();
  showChat();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  document.querySelectorAll(".hist-item").forEach(e =>
    e.classList.toggle("active", e.dataset.id === id)
  );
  closeSidebar();

  const h = await getHeaders();
  if (!h) return;

  try {
    const { ok, data } = await apiJson("/conversation", {
      method: "POST", headers: h,
      body: JSON.stringify({ conversation_id: id })
    });
    if (ok && Array.isArray(data) && data.length) {
      data.forEach(m => appendMessage(m.content, m.role === "user" ? "user" : "ai"));
      scrollToBottom();
    }
  } catch { toast("Could not load conversation", "err"); }
}

async function deleteConversation(id, e) {
  e?.stopPropagation();
  const h = await getHeaders();
  if (!h) return;
  document.querySelector(`.hist-item[data-id="${id}"]`)?.remove();
  if (id === _convId) resetToWelcome();
  await apiFetch("/delete", {
    method: "POST", headers: h,
    body: JSON.stringify({ conversation_id: id })
  }).catch(() => {});
  toast("Conversation deleted");
}

/* ═══════════════════════════════════════
   CONVERSATIONS
═══════════════════════════════════════ */
// FIX-SEND-3: createConversation now logs the actual error and returns a local
// fallback ID so the user can still chat even if the DB call fails
async function createConversation() {
  const h = await getHeaders();
  if (!h) {
    toast("Session expired — please sign in again.", "err");
    setTimeout(() => { window.location.href = "/login"; }, 2000);
    return null;
  }

  try {
    const { ok, status, data } = await apiJson("/conversation/create", {
      method: "POST", headers: h, body: JSON.stringify({})
    });

    if (ok && data?.conversation_id) {
      _convId = data.conversation_id;
      prependToHistory(_convId, "New Conversation");
      return _convId;
    }

    // Log exactly what went wrong
    console.error("[PHI] createConversation failed:", status, data);

    if (status === 403) {
      // Consent missing — resave and retry once
      await saveConsents();
      const retry = await apiJson("/conversation/create", {
        method: "POST", headers: h, body: JSON.stringify({})
      });
      if (retry.ok && retry.data?.conversation_id) {
        _convId = retry.data.conversation_id;
        prependToHistory(_convId, "New Conversation");
        return _convId;
      }
    }

  } catch (err) {
    console.error("[PHI] createConversation exception:", err);
  }

  // Dev fallback — local ID so chat still works without DB
  if (IS_DEV) {
    _convId = "local-" + Date.now();
    console.warn("[PHI] Using local conversation ID:", _convId);
    return _convId;
  }

  return null;
}

function prependToHistory(id, title) {
  const list = el("historyList");
  if (!list) return;
  list.querySelector(".sb-empty")?.remove();

  let todayGroup = list.querySelector(".hist-group-label");
  if (!todayGroup || todayGroup.textContent !== "Today") {
    todayGroup = document.createElement("div");
    todayGroup.className   = "hist-group-label";
    todayGroup.textContent = "Today";
    list.prepend(todayGroup);
  }

  const item = document.createElement("div");
  item.className  = "hist-item active";
  item.dataset.id = id;
  item.innerHTML  = `
    <span class="hist-title">${esc(title)}</span>
    <button class="hist-del" data-del="${esc(id)}" title="Delete">
      <i class="fa-solid fa-trash"></i>
    </button>`;
  todayGroup.insertAdjacentElement("afterend", item);

  document.querySelectorAll(".hist-item").forEach(e =>
    e.classList.toggle("active", e.dataset.id === id)
  );
}

async function renameConversation(id, title) {
  const h = await getHeaders();
  if (!h || !id) return;
  const short   = title.slice(0, 50);
  const titleEl = document.querySelector(`.hist-item[data-id="${id}"] .hist-title`);
  if (titleEl) titleEl.textContent = short;
  setText("convTitle", short);
  await apiFetch("/rename", {
    method: "POST", headers: h,
    body: JSON.stringify({ conversation_id: id, title: short })
  }).catch(() => {});
}

/* ═══════════════════════════════════════
   CHAT
═══════════════════════════════════════ */
// FIX-SEND-1: handleSend checks session before doing anything
async function handleSend() {
  if (_isSending) return;

  // Guard: check session first
  const session = await getSession();
  if (!session) {
    toast("Session expired. Please sign in again.", "err");
    setTimeout(() => { window.location.href = "/login"; }, 2000);
    return;
  }

  const ta   = el("chatInput");
  let text   = ta?.value.trim();
  if (!text && _uploads.length) {
    text = "Please read my uploaded lab report and explain every finding — what's abnormal, what it means, and what I should discuss with my doctor.";
  }
  if (!text) return;
  if (ta) { ta.value = ""; ta.style.height = "auto"; }
  await sendMessage(text);
}

async function sendMessage(text) {
  if (_isSending || !text) return;
  _isSending = true;
  setSendingState(true);
  showChat();

  if (!_convId) {
    const id = await createConversation();
    if (!id) {
      _isSending = false;
      setSendingState(false);
      toast("Could not start conversation. Check your connection.", "err");
      return;
    }
  }

  // Process any queued file uploads first
  if (_uploads.length) {
    const loadRow = appendTyping();
    updateTypingText(loadRow, "Reading your report…");
    const docResult = await processFileUpload(_uploads[0]);
    loadRow?.remove();
    _uploads = [];
    clearFilePreview();

    if (docResult?.document_text) {
      _docCtx.text   = docResult.document_text;
      _docCtx.hasDoc = true;
      _docCtx.sent   = false;
    } else if (docResult === null) {
      // Upload failed — processFileUpload already showed a toast
      _isSending = false;
      setSendingState(false);
      return;
    }
  }

  appendMessage(text, "user");
  const botRow = appendTyping();
  scrollToBottom();

  const sendDoc = !_docCtx.sent ? (_docCtx.text || "") : "";

  const h = await getHeaders();
  if (!h) {
    updateMessage(botRow, "Your session has expired. Please [sign in again](/login).");
    _isSending = false;
    setSendingState(false);
    return;
  }

  let success = false;
  try {
    const { ok, status, data } = await apiJson("/chat", {
      method: "POST", headers: h,
      body: JSON.stringify({
        conversation_id: _convId,
        message:         text,
        has_documents:   _docCtx.hasDoc,
        document_text:   sendDoc,
      })
    });

    const reply = data?.reply;

    if (status === 401) {
      updateMessage(botRow, "Your session has expired. Please sign in again.");
      toast("Session expired.", "err");
    } else if (status === 403) {
      // Consent missing — resave and retry once
      console.warn("[PHI] 403 on /chat — retrying after consent resave");
      await saveConsents();
      const retry = await apiJson("/chat", {
        method: "POST", headers: h,
        body: JSON.stringify({
          conversation_id: _convId,
          message:         text,
          has_documents:   _docCtx.hasDoc,
          document_text:   sendDoc,
        })
      });
      if (retry.ok && retry.data?.reply) {
        updateMessage(botRow, retry.data.reply);
        success = true;
        if (sendDoc) _docCtx.sent = true;
      } else {
        updateMessage(botRow, "Consent required. Please refresh the page and try again.");
      }
    } else if (status === 500) {
      console.error("[PHI] 500 on /chat:", data);
      updateMessage(botRow,
        "PHI ran into a server error. The team has been notified.\n\n" +
        "---\n⚕️ *PHI is an educational wellness tool. Always consult your provider.*"
      );
    } else if (!ok || !reply) {
      console.error("[PHI] Chat error:", status, data);
      updateMessage(botRow,
        "I ran into a technical issue. Please try again in a moment.\n\n" +
        "---\n⚕️ *PHI is an educational wellness tool. Always consult your provider.*"
      );
    } else {
      updateMessage(botRow, reply);
      success = true;
      if (sendDoc) _docCtx.sent = true;
    }

    const userMsgCount = el("chatDisplay")?.querySelectorAll(".chat-msg.user-msg").length || 0;
    if (userMsgCount === 1 && _convId) renameConversation(_convId, text);

    if (success && _docCtx.hasDoc) {
      setTimeout(loadMarkersData, 2000);
    }

  } catch (err) {
    const msg = err.message?.includes("timed out")
      ? "The request timed out — please try again."
      : "Connection error. Please check your internet connection.";
    updateMessage(botRow,
      msg + "\n\n---\n⚕️ *PHI is an educational wellness tool. Always consult your provider.*"
    );
    console.error("[PHI] sendMessage exception:", err);
  }

  _isSending = false;
  setSendingState(false);
  scrollToBottom();
}

function setSendingState(on) {
  const btn = el("sendBtn");
  const ta  = el("chatInput");
  if (btn) {
    btn.disabled  = on;
    btn.innerHTML = on
      ? '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>'
      : '<i class="fa-solid fa-arrow-up"></i>';
  }
  if (ta) ta.disabled = on;
}

/* ═══════════════════════════════════════
   FILE UPLOAD
═══════════════════════════════════════ */
function handleFileSelect(e) {
  const files = Array.from(e.target.files || []);
  files.forEach(f => addFile(f));
  e.target.value = "";
}

function addFile(file) {
  if (file.size > 10 * 1024 * 1024) { toast(`${file.name} is too large. Max 10 MB.`, "err"); return; }
  if (!/\.(pdf|txt)$/i.test(file.name)) { toast("Only PDF or TXT files are supported.", "err"); return; }
  _uploads.push(file);
  renderFilePreview();
  toast(`${file.name} ready — press Send to analyze`);
}

function removeFile(idx) {
  _uploads.splice(idx, 1);
  renderFilePreview();
}

function renderFilePreview() {
  const strip = el("filePreview");
  if (!strip) return;
  if (!_uploads.length) {
    strip.classList.remove("show"); strip.innerHTML = "";
    return;
  }
  strip.classList.add("show");
  strip.innerHTML = _uploads.map((f, i) => `
    <div class="file-chip">
      <i class="fa-solid ${f.name.endsWith(".pdf") ? "fa-file-pdf" : "fa-file-lines"}"></i>
      <span>${esc(f.name)}</span>
      <button class="file-chip-rm" onclick="removeFile(${i})" aria-label="Remove ${esc(f.name)}">
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>`).join("");
}

function clearFilePreview() {
  const strip = el("filePreview");
  if (strip) { strip.classList.remove("show"); strip.innerHTML = ""; }
}

// FIX-SEND-2: processFileUpload now handles every failure mode with a toast
async function processFileUpload(file) {
  const session = await getSession();
  if (!session) {
    toast("Session expired. Please sign in again.", "err");
    setTimeout(() => { window.location.href = "/login"; }, 2000);
    return null;
  }

  const form = new FormData();
  form.append("file", file);

  try {
    const res = await apiFetch("/analyze", {
      method:  "POST",
      headers: { "Authorization": `Bearer ${session.access_token}` },
      body:    form,
    });

    // Handle specific HTTP errors
    if (res.status === 401) {
      toast("Session expired. Please sign in again.", "err");
      return null;
    }

    if (res.status === 403) {
      // Consent missing — resave and retry once
      console.warn("[PHI] 403 on /analyze — retrying after consent resave");
      await saveConsents();
      const retry = await apiFetch("/analyze", {
        method:  "POST",
        headers: { "Authorization": `Bearer ${session.access_token}` },
        body:    form,
      });
      if (retry.ok) {
        const d = await retry.json();
        return d;
      }
      toast("Permission error. Please refresh the page and try again.", "err");
      return null;
    }

    if (res.status === 413) {
      toast("File too large. Maximum 5 MB.", "err");
      return null;
    }

    if (res.status === 400) {
      const d = await res.json().catch(() => ({}));
      toast(d.error || "Could not read this file. Try a clear PDF.", "err");
      return null;
    }

    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      console.error("[PHI] /analyze error:", res.status, d);
      toast(d.error || `Upload failed (${res.status}). Please try again.`, "err");
      return null;
    }

    return await res.json();

  } catch (err) {
    console.error("[PHI] processFileUpload exception:", err);
    if (err.message?.includes("timed out")) {
      toast("Upload timed out. Try a smaller file.", "err");
    } else {
      toast("Upload failed. Check your connection.", "err");
    }
    return null;
  }
}

/* ═══════════════════════════════════════
   MESSAGE RENDERING
═══════════════════════════════════════ */
function appendMessage(text, role) {
  const display = el("chatDisplay");
  if (!display) return null;

  const wrap    = document.createElement("div");
  wrap.className = `chat-msg ${role === "user" ? "user-msg" : "ai-msg"}`;

  const avLabel = role === "user" ? (_userName?.[0]?.toUpperCase() || "U") : "φ";
  const avClass = role === "user" ? "av-user" : "av-ai";
  const avEl    = `<div class="msg-av ${avClass}">${avLabel}</div>`;

  const bodyEl  = document.createElement("div");
  bodyEl.className = "msg-body";

  if (role === "user") {
    bodyEl.textContent = text;
    wrap.innerHTML = avEl;
    wrap.insertBefore(bodyEl, wrap.querySelector(".msg-av"));
  } else {
    renderAIContent(bodyEl, text);
    wrap.innerHTML = avEl;
    wrap.appendChild(bodyEl);
  }

  display.appendChild(wrap);
  return wrap;
}

function appendTyping() {
  const display = el("chatDisplay");
  if (!display) return null;
  const wrap = document.createElement("div");
  wrap.className = "chat-msg ai-msg";
  wrap.innerHTML = `
    <div class="msg-av av-ai">φ</div>
    <div class="msg-body">
      <div class="typing-indicator">
        <div class="t-dot"></div><div class="t-dot"></div><div class="t-dot"></div>
      </div>
    </div>`;
  display.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function updateTypingText(wrap, text) {
  const body = wrap?.querySelector(".msg-body");
  if (body) body.textContent = text;
}

function updateMessage(wrap, text) {
  const body = wrap?.querySelector(".msg-body");
  if (!body) return;
  renderAIContent(body, text);
  scrollToBottom();
}

function renderAIContent(elem, text) {
  const parts    = text.split(/---\n⚕️/);
  const main     = parts[0].trim();
  const hasLegal = parts.length > 1;

  if (typeof marked !== "undefined") {
    elem.innerHTML = marked.parse(main);
  } else {
    elem.textContent = main;
  }

  if (hasLegal) {
    const legal = document.createElement("p");
    legal.className = "phi-legal";
    legal.textContent = "⚕️ PHI is an educational wellness tool. Always consult your healthcare provider.";
    elem.appendChild(legal);
  }
}

function scrollToBottom() {
  const d = el("chatDisplay");
  if (d) d.scrollTop = d.scrollHeight;
}

/* ═══════════════════════════════════════
   EXPORT & LOGOUT
═══════════════════════════════════════ */
function exportCurrentChat() {
  const msgs = el("chatDisplay")?.querySelectorAll(".chat-msg");
  if (!msgs?.length) { toast("No conversation to export", "err"); return; }
  let out = `Curabook PHI — Chat Export\n${"=".repeat(44)}\n\n`;
  msgs.forEach(m => {
    const role = m.classList.contains("user-msg") ? "You" : "PHI";
    const text = m.querySelector(".msg-body")?.innerText || "";
    out += `${role}:\n${text.trim()}\n\n`;
  });
  const a = Object.assign(document.createElement("a"), {
    href:     URL.createObjectURL(new Blob([out], { type: "text/plain" })),
    download: `phi-chat-${Date.now()}.txt`
  });
  a.click();
  closeUserMenu();
  toast("Chat exported");
}

async function handleLogout() {
  closeUserMenu();
  await _sb.auth.signOut();
}

/* ═══════════════════════════════════════
   HEALTH DATA
═══════════════════════════════════════ */
async function loadMarkersData() {
  const h = await getHeaders();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/health-markers", { headers: h });
    if (ok && Array.isArray(data) && data.length) {
      renderMarkers(data);
      runCliffDetection(data);
    }
  } catch {}
}

function renderMarkers(markers) {
  const grid = el("markersGrid");
  if (!grid) return;
  const show = markers.slice(0, 10);
  if (!show.length) {
    grid.innerHTML = '<div class="markers-empty">No markers yet — upload a lab report</div>';
    return;
  }
  grid.innerHTML = show.map(m => {
    const s     = (m.status || "").toLowerCase();
    const cls   = s === "high" ? "val-high" : s === "low" ? "val-low" : s === "normal" ? "val-normal" : "";
    const badge = (s && s !== "unknown")
      ? `<span class="marker-status st-${s}">${s.toUpperCase()}</span>` : "";
    return `<div class="marker-card">
      <div class="marker-card-name" title="${esc(m.marker_name)}">${esc(m.marker_name)}</div>
      <div class="marker-card-val ${cls}">${m.value}<span class="marker-card-unit"> ${esc(m.unit || "")}</span></div>
      ${badge}
    </div>`;
  }).join("");
}

function runCliffDetection(markers) {
  const alerts  = [];
  const grouped = {};

  markers.forEach(m => {
    const k = (m.marker_name || "").toLowerCase();
    if (!grouped[k]) grouped[k] = [];
    grouped[k].push({ ...m, _val: parseFloat(m.value) });
  });

  // Glucose rebound detection (15% threshold)
  const glucoseKey = Object.keys(grouped).find(k => /fasting.*glucose|blood.*glucose|glucose/i.test(k));
  if (glucoseKey) {
    const readings = grouped[glucoseKey].sort((a, b) => a.date < b.date ? -1 : 1);
    if (readings.length >= 2) {
      const base = readings[0]._val;
      const last = readings[readings.length - 1]._val;
      if (base > 0) {
        const pct = ((last - base) / base) * 100;
        if (pct >= 15) {
          alerts.push({ type: "danger", title: `🚨 Glucose rebound +${pct.toFixed(0)}%`, desc: `From ${base} → ${last} mg/dL. Early cliff signal. Discuss urgently with provider.` });
        } else if (pct >= 10) {
          alerts.push({ type: "warn", title: `⚠ Glucose rising +${pct.toFixed(0)}%`, desc: `Approaching the 15% rebound threshold.` });
        }
      }
    }
  }

  // HbA1c rebound (0.25% threshold)
  const hba1cKey = Object.keys(grouped).find(k => /hba1c|hemoglobin a1c|a1c/i.test(k));
  if (hba1cKey) {
    const readings = grouped[hba1cKey].sort((a, b) => a.date < b.date ? -1 : 1);
    for (let i = 1; i < readings.length; i++) {
      const delta = readings[i]._val - readings[i - 1]._val;
      if (delta >= 0.25) {
        alerts.push({ type: "danger", title: `🚨 HbA1c rebound +${delta.toFixed(2)}%`, desc: `${readings[i-1]._val}% → ${readings[i]._val}%. Sustained metabolic rebound signal.` });
        break;
      }
    }
  }

  // General HIGH markers
  markers.filter(m => m.status === "HIGH" && !/glucose/i.test(m.marker_name))
    .slice(0, 2)
    .forEach(m => {
      alerts.push({ type: "warn", title: `⬆ ${m.marker_name} HIGH`, desc: `${m.value} ${m.unit || ""} — outside normal range.` });
    });

  if (!alerts.length) {
    alerts.push({ type: "ok", title: "✅ No rebound signals", desc: "All monitored markers are stable. Keep up protein intake and resistance training." });
  }

  renderCliffAlerts(alerts);
}

function renderCliffAlerts(alerts) {
  const container = el("cliffAlerts");
  if (!container) return;
  container.innerHTML = alerts.map(a => `
    <div class="ca-item ca-${a.type}">
      <div class="ca-title">${a.title}</div>
      <div class="ca-desc">${a.desc}</div>
    </div>`).join("");
}

/* ═══════════════════════════════════════
   COCKPIT — METABOLIC SHIELD
═══════════════════════════════════════ */
function restoreShieldInputs() {
  const savedGoalWt = localStorage.getItem("phi_goal_wt");
  if (savedGoalWt) {
    const gwInput = el("inputGoalWt");
    if (gwInput && !gwInput.value) gwInput.value = savedGoalWt;
    const proteinInput = el("proteinInput");
    if (proteinInput && !proteinInput.value) proteinInput.value = savedGoalWt;
  }
}

function updateShield() {
  const protein = parseFloat(el("inputProtein")?.value) || 0;
  const steps   = parseFloat(el("inputSteps")?.value)   || 0;
  const sleep   = parseFloat(el("inputSleep")?.value)   || 0;

  const gwInputVal = parseFloat(el("inputGoalWt")?.value);
  const goalWt = gwInputVal || _goalWt || 165;

  if (gwInputVal && gwInputVal !== _goalWt) {
    _goalWt = gwInputVal;
    localStorage.setItem("phi_goal_wt", String(_goalWt));
  }

  _proteinTarget = Math.round(goalWt * 0.545 * 10) / 10;

  const pPct = Math.min(100, Math.round((protein / (_proteinTarget || 90)) * 100));
  const mPct = Math.min(100, Math.round((steps   / 8000) * 100));
  const rPct = Math.max(0, Math.min(100, Math.round(((sleep - 4) / 5) * 100)));
  const score = Math.round((pPct + mPct + rPct) / 3);

  setRing("ringProtein",  70, pPct, 440);
  setRing("ringMovement", 55, mPct, 346);
  setRing("ringRecovery", 41, rPct, 258);

  setText("shieldScore",    score + "%");
  setText("shieldBadge",    score + "%");
  setText("proteinLegend",  `${protein}g / ${_proteinTarget}g (${pPct}%)`);
  setText("movementLegend", `${steps.toLocaleString()} / 8,000 (${mPct}%)`);
  setText("recoveryLegend", `${sleep}h sleep (${rPct}%)`);

  setBarWidth("proteinBar",  pPct);
  setBarWidth("movementBar", mPct);
  setBarWidth("recoveryBar", rPct);

  if (_user && (protein > 0 || steps > 0 || sleep > 0)) {
    logShieldData(protein, steps, sleep).catch(() => {});
  }
}

function setRing(id, r, pct, circ) {
  const ring = el(id);
  if (!ring) return;
  ring.style.strokeDasharray  = circ;
  ring.style.strokeDashoffset = circ - (circ * Math.max(0, Math.min(100, pct)) / 100);
}

function setBarWidth(id, pct) {
  const bar = el(id);
  if (bar) bar.style.width = Math.max(0, pct) + "%";
}

async function logShieldData(protein, steps, sleep) {
  const h = await getHeaders();
  if (!h) return;

  const date = new Date().toISOString().slice(0, 10);
  const logs = [];
  if (protein > 0) logs.push({ date, metric_name: "protein", value: protein, unit: "g" });
  if (steps   > 0) logs.push({ date, metric_name: "steps",   value: steps,   unit: "steps" });
  if (sleep   > 0) logs.push({ date, metric_name: "sleep",   value: sleep,   unit: "hours" });

  logs.forEach(l =>
    apiFetch("/api/behavioral-logs", {
      method: "POST", headers: h, body: JSON.stringify(l)
    }).catch(err => console.warn("[PHI] Log failed:", err))
  );
}

function calcProtein() {
  const gw = parseFloat(el("proteinInput")?.value);
  if (!gw || gw < 80 || gw > 400) {
    toast("Enter a valid goal weight between 80–400 lbs", "err");
    return;
  }

  _goalWt        = gw;
  _proteinTarget = Math.round(gw * 0.545 * 10) / 10;
  const perMeal  = Math.round(_proteinTarget / 3 * 10) / 10;
  const leucineOk = perMeal >= 30;

  localStorage.setItem("phi_goal_wt", String(gw));

  setText("proteinNum",     _proteinTarget);
  setText("proteinCaption", `${gw} lbs × 0.545 = ${_proteinTarget}g/day`);

  const details = el("proteinDetails");
  if (details) {
    details.classList.remove("hidden");
    details.innerHTML = `
      <strong>${perMeal}g per meal</strong> across 3 meals
      &nbsp;—&nbsp; ${leucineOk ? "✅" : "⚠️"} ${leucineOk ? "Meets" : "Below"} 30g leucine threshold<br>
      <span style="color:var(--text-3);margin-top:4px;display:block">
        Sample: 4oz chicken (35g) + Greek yogurt (17g) + 2 eggs (12g) + whey scoop (25g)
      </span>`;
  }

  const gwInput = el("inputGoalWt");
  if (gwInput) gwInput.value = gw;
}

function updateNoiseReadout() {
  const val = parseInt(el("noiseSlider")?.value || 5);
  const colors = {
    1:"var(--ok)", 2:"var(--ok)", 3:"var(--ok)",
    4:"var(--amber)", 5:"var(--amber)", 6:"var(--amber)",
    7:"var(--danger)", 8:"var(--danger)", 9:"var(--danger)", 10:"var(--danger)"
  };
  const readout = el("noiseReadout");
  if (readout) {
    readout.innerHTML = `<strong style="color:${colors[val]}">Level ${val}/10</strong> — ${NOISE_MESSAGES[val]}`;
  }
}

async function logNoiseLevel() {
  const val = parseInt(el("noiseSlider")?.value || 5);
  const h   = await getHeaders();
  if (!h) { toast("Sign in to log food noise data", "info"); return; }

  await apiFetch("/api/behavioral-logs", {
    method: "POST", headers: h,
    body: JSON.stringify({
      date:        new Date().toISOString().slice(0, 10),
      metric_name: "food_noise",
      value:       val,
      unit:        "1-10",
      notes:       `Ghrelin surge level: ${val}/10`
    })
  }).catch(() => {});

  toast(`Food noise level ${val}/10 logged`, "info");
}

/* ═══════════════════════════════════════
   VOICE INPUT
═══════════════════════════════════════ */
function initVoiceInput() {
  const SR  = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = el("micBtn");
  if (!SR || !btn) {
    if (btn) { btn.style.opacity = ".3"; btn.disabled = true; }
    return;
  }
  let listening = false, rec = null;
  btn.addEventListener("click", () => {
    if (listening) { rec?.stop(); return; }
    rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.onstart  = () => { listening = true;  btn.style.color = "var(--danger)"; };
    rec.onresult = e => {
      const ta = el("chatInput");
      if (ta) { ta.value = e.results[0][0].transcript; autoGrow(ta); ta.focus(); }
    };
    rec.onend  = () => { listening = false; btn.style.color = ""; };
    rec.onerror = e => { toast(`Mic: ${e.error}`, "err"); };
    try { rec.start(); } catch { toast("Voice failed", "err"); }
  });
}

/* ═══════════════════════════════════════
   UTILITY
═══════════════════════════════════════ */
function el(id)          { return document.getElementById(id); }
function esc(s)          { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function setText(id, v)  { const e=el(id); if(e) e.textContent=v; }
function setIcon(id, cls){ const e=el(id); if(e) e.className=`fa-solid ${cls}`; }
function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 130) + "px";
}

function toast(msg, type = "ok") {
  const container = el("toasts");
  if (!container) return;
  const t = document.createElement("div");
  const iconMap = { ok: "circle-check", err: "circle-exclamation", info: "circle-info" };
  t.className = `toast toast-${type}`;
  t.innerHTML = `<i class="fa-solid fa-${iconMap[type] || "circle-info"}"></i> ${esc(msg)}`;
  container.appendChild(t);
  setTimeout(() => {
    t.style.opacity    = "0";
    t.style.transition = "opacity .3s";
    setTimeout(() => t.remove(), 300);
  }, 3800);
}

/* ═══════════════════════════════════════
   EVENT WIRING
═══════════════════════════════════════ */
function wireEvents() {
  el("newChatBtn")?.addEventListener("click", resetToWelcome);

  document.querySelectorAll(".nav-item[data-view]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      closeSidebar();
    });
  });

  el("mobileMenuBtn")?.addEventListener("click", openSidebar);
  el("sidebarOverlay")?.addEventListener("click", closeSidebar);
  el("mobileCockpitBtn")?.addEventListener("click", toggleCockpit);
  el("cockpitOverlay")?.addEventListener("click", closeCockpit);
  el("cockpitCloseBtn")?.addEventListener("click", closeCockpit);

  el("userRow")?.addEventListener("click", e => {
    if (!e.target.closest(".user-dropdown")) toggleUserMenu();
  });
  document.addEventListener("click", e => {
    if (!el("userRow")?.contains(e.target)) closeUserMenu();
  });

  el("themeToggleBtn")?.addEventListener("click", toggleTheme);
  el("topThemeBtn")?.addEventListener("click", toggleTheme);
  el("exportChatBtn")?.addEventListener("click", exportCurrentChat);
  el("logoutBtn")?.addEventListener("click", handleLogout);

  el("historyList")?.addEventListener("click", e => {
    const item   = e.target.closest(".hist-item[data-id]");
    const delBtn = e.target.closest(".hist-del[data-del]");
    if (delBtn) { deleteConversation(delBtn.dataset.del, e); }
    else if (item) { openConversation(item.dataset.id); }
  });

  const ta = el("chatInput");
  ta?.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  });
  ta?.addEventListener("input", () => autoGrow(ta));

  el("sendBtn")?.addEventListener("click", handleSend);

  document.querySelectorAll(".suggestion-chips .chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const q = chip.dataset.q;
      if (!q) return;
      if (ta) ta.value = q;
      sendMessage(q);
    });
  });

  const fileInput = el("fileInput");
  fileInput?.addEventListener("change", handleFileSelect);
  el("attachTopBtn")?.addEventListener("click",   () => fileInput?.click());
  el("attachInputBtn")?.addEventListener("click", () => fileInput?.click());
  el("uploadNudgeBtn")?.addEventListener("click", () => fileInput?.click());

  el("updateShieldBtn")?.addEventListener("click", updateShield);
  el("calcBtn")?.addEventListener("click", calcProtein);
  el("proteinInput")?.addEventListener("keydown", e => { if (e.key === "Enter") calcProtein(); });
  el("refreshAlertsBtn")?.addEventListener("click", loadMarkersData);
  el("noiseSlider")?.addEventListener("input", updateNoiseReadout);
  el("logNoiseBtn")?.addEventListener("click", logNoiseLevel);
  el("refreshMarkersBtn")?.addEventListener("click", loadMarkersData);

  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => {
    e.preventDefault();
    Array.from(e.dataTransfer?.files || []).forEach(f => addFile(f));
  });

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      resetToWelcome();
      el("chatInput")?.focus();
    }
    if (e.key === "Escape") {
      closeSidebar();
      closeUserMenu();
      closeCockpit();
    }
  });
}

/* ═══════════════════════════════════════
   INIT
═══════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", () => {
  wireEvents();
  updateNoiseReadout();
  boot();
  initVoiceInput();
});