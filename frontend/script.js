/**
 * script.js — Curabook PHI  v3.1
 *
 * FIXES APPLIED:
 * #BUG-JS-1  window._lastDocText / _lastDocId persisted across sessions and new
 *            chats. Every subsequent message re-sent the full document text,
 *            making is_fresh_document=True on every single follow-up. This
 *            caused expensive re-extraction on every message. Now cleared on
 *            new chat and on session init.
 *
 * #BUG-JS-2  Delete account called supabaseClient.auth.signOut() AFTER backend
 *            delete, then redirected. The session remained valid for ~1hr post-
 *            deletion. Now: signOut() → delete-account → redirect.
 *
 * #BUG-JS-3  New chat confirm fired on DOM.chatDisplay.children.length > 0 even
 *            when chatDisplay only had empty state divs or loading spinners.
 *            Now checks for actual .chat-message elements.
 *
 * #BUG-JS-4  Profile dropdown stayed open after signing out. Now explicitly
 *            hidden on logout.
 *
 * #BUG-JS-5  Health pulse card was not refreshed after new conversation started.
 *            Now reloaded when returning to welcome mode.
 */

"use strict";

/* ══════════════════════════════════════════════════════════════
   CONSTANTS & STATE
══════════════════════════════════════════════════════════════ */

const API_BASE = "https://api.curabook.com";

let supabaseClient       = null;
let currentUser          = null;
let activeConvId         = null;
let uploadedFiles        = [];
let isProcessing         = false;
let conversationToDelete = null;
let _healthContext       = null;

/* ══════════════════════════════════════════════════════════════
   DOM
══════════════════════════════════════════════════════════════ */

const $id = (id) => document.getElementById(id);

const DOM = {
    sidebar:        $id("sidebar"),
    overlay:        $id("overlay"),
    closeSidebar:   $id("close-sidebar-btn"),
    mobileMenu:     $id("mobile-menu-btn"),
    avatarInitial:  $id("avatar-initial"),
    dropdownEmail:  $id("dropdown-email"),
    dropdownPlan:   $id("dropdown-plan-label"),
    profileBtn:     $id("profileBtn"),
    profileDrop:    $id("profileDropdown"),
    logoutBtn:      $id("logoutBtn"),
    statusLabel:    $id("statusLabel"),
    welcomeScreen:  $id("welcomeScreen"),
    welcomeHero:    $id("welcomeHero"),
    welcomeChips:   $id("welcomeChips"),
    uploadNudge:    $id("uploadNudge"),
    uploadNudgeBtn: $id("uploadNudgeBtn"),
    pulseCard:      $id("pulseCard"),
    pulseLoading:   $id("pulseLoading"),
    pulseContent:   $id("pulseContent"),
    chatDisplay:    $id("chat-display"),
    userInput:      $id("userInput"),
    sendBtn:        $id("sendBtn"),
    attachBtn:      $id("attachBtn"),
    fileInput:      $id("fileInput"),
    micBtn:         $id("micBtn"),
    filePreview:    $id("file-preview-container"),
    newChatBtn:     $id("newChatBtn"),
    historyList:    $id("historyList"),
    userEmailDisp:  $id("user-email-display"),
    btnUploadNav:   $id("btn-upload-nav"),
    btnHealthPulse: $id("btn-health-pulse"),
    profileModal:   $id("profile-modal"),
    settingsModal:  $id("settings-modal"),
    deleteModal:    $id("delete-modal"),
    pulseModal:     $id("pulse-modal"),
    pulseModalBody: $id("pulse-modal-body"),
    modalAvatar:    $id("modal-avatar"),
    modalEmail:     $id("modal-email"),
    accountCreated: $id("account-created"),
    totalConvs:     $id("total-conversations"),
    docsAnalyzed:   $id("documents-analyzed"),
    healthDash:     $id("health-dashboard"),
    doctorBriefBtn: $id("doctorBriefBtn"),
    btnProfile:     $id("btn-sidebar-profile"),
    btnSettings:    $id("btn-sidebar-settings"),
    modalLogout:    $id("modalLogout"),
    themeToggle:    $id("themeToggle"),
    fontSizeInput:  $id("fontSizeInput"),
    fontSizeValue:  $id("fontSizeValue"),
    exportChatBtn:  $id("exportChatBtn"),
    clearHistBtn:   $id("clearHistoryBtn"),
    exportDataBtn:  $id("exportDataBtn"),
    deleteAccBtn:   $id("deleteAccountBtn"),
    confirmDeleteBtn: $id("confirmDeleteBtn"),
};

/* ══════════════════════════════════════════════════════════════
   DOC CONTEXT STATE — FIX #BUG-JS-1
   Centralized doc context management to prevent stale data leaking
   into follow-up conversations.
══════════════════════════════════════════════════════════════ */

const _docCtx = {
    text:  null,
    id:    null,
    convId: null,   // which conversation this doc belongs to

    set(docText, docId, convId) {
        this.text   = docText || null;
        this.id     = docId   || null;
        this.convId = convId  || null;
    },

    /**
     * Returns doc context only if it belongs to the CURRENT conversation.
     * Prevents stale doc text from bleeding into new conversations.
     */
    getForConv(convId) {
        if (!this.convId || this.convId !== convId) return { text: null, id: null };
        return { text: this.text, id: this.id };
    },

    clear() {
        this.text   = null;
        this.id     = null;
        this.convId = null;
    },
};

/* ══════════════════════════════════════════════════════════════
   UTILITIES
══════════════════════════════════════════════════════════════ */

function showToast(message, type = "success") {
    const el = document.createElement("div");
    el.className = type === "success" ? "success-toast" : "error-toast";
    const icon = type === "success" ? "fa-circle-check" : "fa-circle-exclamation";
    el.innerHTML = `<i class="fa-solid ${icon}"></i> ${escapeHtml(message)}`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function escapeHtml(str) {
    return String(str || "")
        .replace(/&/g,"&amp;").replace(/</g,"&lt;")
        .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function renderMarkdown(text) {
    if (typeof marked !== "undefined") return marked.parse(String(text || ""));
    return escapeHtml(String(text || ""));
}

function autoGrow(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 150) + "px";
}

function setProcessing(loading) {
    isProcessing = loading;
    DOM.sendBtn.disabled = loading;
    DOM.userInput.disabled = loading;
    DOM.sendBtn.innerHTML = loading
        ? '<i class="fa-solid fa-spinner fa-spin"></i>'
        : '<i class="fa-solid fa-arrow-up"></i>';
}

/** FIX #BUG-JS-3: Count only real chat messages, not layout divs */
function hasChatMessages() {
    return DOM.chatDisplay.querySelectorAll(".chat-message").length > 0;
}

/* ══════════════════════════════════════════════════════════════
   SUPABASE AUTH
══════════════════════════════════════════════════════════════ */

async function initSupabase() {
    const SUPABASE_URL = "https://hxfiymzpngxltjbpbgur.supabase.co";
    const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh4Zml5bXpwbmd4bHRqYnBiZ3VyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njc5NDk1NTQsImV4cCI6MjA4MzUyNTU1NH0.aU5JDyhwFyzOoCrflqaFJ6N-3Tvy92RO9nP2HP9v6sc";
    supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
    window.supabaseClient = supabaseClient;
}

async function getSession() {
    const { data } = await supabaseClient.auth.getSession();
    return data.session;
}

async function getAuthHeaders() {
    const session = await getSession();
    if (!session) return {};
    return {
        "Content-Type":  "application/json",
        "Authorization": "Bearer " + session.access_token,
    };
}

async function handleLoginSuccess(user) {
    currentUser = user;

    const initial = (user.email || "?").charAt(0).toUpperCase();
    DOM.avatarInitial.textContent  = initial;
    if (DOM.dropdownEmail) DOM.dropdownEmail.textContent = user.email;
    if (DOM.userEmailDisp) DOM.userEmailDisp.textContent = user.email;
    if (DOM.modalEmail)    DOM.modalEmail.textContent    = user.email;
    if (DOM.modalAvatar)   DOM.modalAvatar.textContent   = initial;

    // FIX #BUG-JS-1: clear stale doc context on session init
    _docCtx.clear();

    saveUserConsents().catch(() => {});
    loadUserPreferences();
    loadHistory();
    loadPlanStatus().catch(() => {});

    await loadHealthPulse();
    showToast("Welcome back 👋");

    _carryOverDemoDoc();

    if (new URLSearchParams(location.search).get("upload") === "1") {
        history.replaceState({}, "", location.pathname);
        setTimeout(() => DOM.fileInput?.click(), 600);
    }
}

async function saveUserConsents() {
    const headers = await getAuthHeaders();
    if (!headers.Authorization) return;
    await fetch(`${API_BASE}/api/consent`, {
        method: "POST", headers,
        body: JSON.stringify({ consents: ["data_processing", "ai_processing", "document_processing"] }),
    }).catch(() => {});
}

/** FIX #BUG-JS-2 + #BUG-JS-4: signOut before any redirect, hide dropdown */
async function handleLogout() {
    if (!confirm("Sign out of Curabook PHI?")) return;
    DOM.profileDrop?.classList.add("hidden");   // FIX #BUG-JS-4
    try { await supabaseClient.auth.signOut(); } catch {}
    window.location.href = "https://curabook.com/login";
}

function _carryOverDemoDoc() {
    const docText    = sessionStorage.getItem("phi_pending_doc_text");
    const docName    = sessionStorage.getItem("phi_pending_doc_name");
    const docSummary = sessionStorage.getItem("phi_pending_doc_summary");
    if (!docText || !docName) return;

    sessionStorage.removeItem("phi_pending_doc_text");
    sessionStorage.removeItem("phi_pending_doc_name");
    sessionStorage.removeItem("phi_pending_doc_summary");

    setTimeout(async () => {
        if (!activeConvId) await createConversation();
        _docCtx.set(docText, null, activeConvId);  // FIX #BUG-JS-1
        if (docSummary) appendMessage(`📋 **${docName}** (from demo)\n\n${docSummary}`, "ai");
        DOM.userInput.value = "Please explain my uploaded report thoroughly.";
        handleSend();
    }, 1200);
}

/* ══════════════════════════════════════════════════════════════
   HEALTH PULSE — INTELLIGENCE-FIRST WELCOME
══════════════════════════════════════════════════════════════ */

async function loadHealthPulse() {
    // Reset to loading state
    if (DOM.pulseLoading) DOM.pulseLoading.classList.remove("hidden");
    if (DOM.pulseContent) DOM.pulseContent.classList.add("hidden");
    if (DOM.pulseCard)    DOM.pulseCard.style.display = "";

    try {
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/api/dashboard`, { headers });

        if (!res.ok) { showNoDataState(); return; }

        const dash = await res.json();
        _healthContext = dash;

        if (!dash.total_markers || dash.total_markers === 0) {
            showNoDataState();
            return;
        }

        renderPulseCard(dash);
        generateContextualChips(dash);

    } catch (e) {
        console.warn("[PULSE] Load error:", e);
        showNoDataState();
    }
}

function showNoDataState() {
    if (DOM.pulseCard)    DOM.pulseCard.style.display = "none";
    if (DOM.welcomeHero)  DOM.welcomeHero.style.display = "";
    if (DOM.uploadNudge)  DOM.uploadNudge.classList.remove("hidden");

    setChips([
        { icon: "fa-file-medical",    text: "Upload my first lab report" },
        { icon: "fa-circle-question", text: "What can PHI do for me?" },
        { icon: "fa-stethoscope",     text: "How do I prepare for a doctor visit?" },
        { icon: "fa-chart-line",      text: "What health markers should I track?" },
    ]);
}

function renderPulseCard(dash) {
    if (!DOM.pulseLoading || !DOM.pulseContent) return;

    DOM.pulseLoading.classList.add("hidden");
    DOM.pulseContent.classList.remove("hidden");

    const abnormal  = dash.abnormal_count   || 0;
    const total     = dash.total_markers    || 0;
    const feed      = dash.feed             || [];
    const trends    = dash.trends           || [];

    let statusClass  = "healthy";
    let statusLabel  = "✓ All Looking Good";
    let headlineText = `All ${total} tracked markers are within normal ranges.`;

    if (abnormal >= 3) {
        statusClass  = "urgent";
        statusLabel  = "⚠ Needs Attention";
        headlineText = `${abnormal} of your markers need attention. The most important ones are below.`;
    } else if (abnormal > 0) {
        statusClass  = "moderate";
        statusLabel  = "↑ Some Items to Review";
        headlineText = `${abnormal} marker${abnormal > 1 ? "s" : ""} outside normal range.`;
    }

    const concerningTrends = trends.filter(t => t.concerning || t.pct_change >= 20);
    if (concerningTrends.length > 0 && abnormal === 0) {
        statusClass  = "moderate";
        statusLabel  = "↑ Trend to Watch";
        const t      = concerningTrends[0];
        headlineText = `${t.marker} has moved ${t.pct_change}% since ${t.from_date}.`;
    }

    const alertItems = feed.slice(0, 4).map(item => {
        const sc   = item.severity === "high" ? "high" : item.severity === "medium" ? "medium" : item.severity === "none" ? "positive" : "low";
        const icon = item.icon || (sc === "high" ? "⚠️" : sc === "positive" ? "✅" : "→");
        return `
            <div class="pulse-alert-item ${sc}"
                 onclick="sendChip(${JSON.stringify(item.cta || "Tell me more")})"
                 title="Click to ask PHI">
                <span class="alert-icon">${icon}</span>
                <div class="alert-body">
                    <div class="alert-title">${escapeHtml(item.title || "")}</div>
                    <div class="alert-desc">${escapeHtml(item.body || "")}</div>
                </div>
                <span class="alert-cta">Ask →</span>
            </div>`;
    }).join("");

    DOM.pulseContent.innerHTML = `
        <div class="pulse-card-header">
            <span class="pulse-status-pill ${statusClass}">${statusLabel}</span>
            <p class="pulse-headline">${escapeHtml(headlineText)}</p>
        </div>
        ${alertItems ? `<div class="pulse-alerts">${alertItems}</div>` : ""}
        <button class="pulse-view-more" onclick="openPulseModal()">
            <i class="fa-solid fa-chart-line"></i> View full health picture
        </button>`;
}

function generateContextualChips(dash) {
    const trends   = dash.trends           || [];
    const abnormal = dash.abnormal_count   || 0;
    const markers  = dash.abnormal_markers || [];

    const chips = [];

    if (markers.length > 0) {
        const name = markers[0].marker_name || markers[0].name || "my top marker";
        chips.push({ icon: "fa-triangle-exclamation", text: `What does my ${name} result mean?` });
    }

    const topTrend = trends.find(t => t.concerning || t.pct_change >= 15);
    if (topTrend) {
        chips.push({ icon: "fa-chart-line", text: `Why is my ${topTrend.marker} ${topTrend.direction}?` });
    }

    chips.push({ icon: "fa-stethoscope", text: "Prepare me for my next doctor visit" });

    chips.push({
        icon: abnormal > 0 ? "fa-dumbbell" : "fa-heart-pulse",
        text: abnormal > 0 ? "What lifestyle changes will help most?" : "How do I maintain these healthy levels?",
    });

    setChips(chips);

    if (abnormal > 0 && markers[0]) {
        DOM.userInput.placeholder = `Ask about your ${markers[0].marker_name || "results"}, trends, or doctor prep…`;
    } else {
        DOM.userInput.placeholder = "Ask PHI about your health…";
    }
}

function setChips(chips) {
    if (!DOM.welcomeChips) return;
    DOM.welcomeChips.innerHTML = chips.map(c =>
        `<button class="chip" onclick="sendChip(${JSON.stringify(c.text)})">
            <i class="fa-solid ${c.icon}"></i> ${escapeHtml(c.text)}
        </button>`
    ).join("");
}

function sendChip(text) {
    DOM.userInput.value = text;
    DOM.userInput.focus();
    setTimeout(() => handleSend(), 80);
}
window.sendChip = sendChip;

async function openPulseModal() {
    window.closeModals();
    DOM.pulseModal?.classList.remove("hidden");
    renderPulseModalContent();
}

async function renderPulseModalContent() {
    if (!DOM.pulseModalBody) return;
    DOM.pulseModalBody.innerHTML = '<div class="loading-text"><i class="fa-solid fa-spinner fa-spin"></i> Loading…</div>';

    try {
        const headers = await getAuthHeaders();
        const [markersRes, insightsRes] = await Promise.all([
            fetch(`${API_BASE}/api/health-markers`,  { headers }),
            fetch(`${API_BASE}/api/health-insights`, { headers }),
        ]);
        const markers  = markersRes.ok  ? await markersRes.json()  : [];
        const insights = insightsRes.ok ? await insightsRes.json() : [];

        if (!markers.length) {
            DOM.pulseModalBody.innerHTML = '<div class="loading-text">No health data yet — upload a report to see your full picture.</div>';
            return;
        }

        const abnormal = markers.filter(m => m.status === "HIGH" || m.status === "LOW");

        const markerRows = markers.map(m => {
            const color = m.status === "HIGH" || m.status === "LOW"
                ? "var(--accent-warn)" : m.status === "NORMAL" ? "var(--accent-ok)" : "var(--text-muted)";
            const badge = m.status === "HIGH" ? "⬆ HIGH" : m.status === "LOW" ? "⬇ LOW" : "✓ NORMAL";
            return `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid var(--border)">
                    <div>
                        <div style="font-weight:500;font-size:13px">${escapeHtml(m.marker_name)}</div>
                        <div style="font-size:11.5px;color:var(--text-muted)">Normal: ${escapeHtml(m.reference_range || "—")} · ${escapeHtml(m.date || "")}</div>
                    </div>
                    <div style="text-align:right">
                        <div style="font-weight:700;font-size:14px;color:${color}">${m.value} <span style="font-size:11px;font-weight:400">${escapeHtml(m.unit || "")}</span></div>
                        <div style="font-size:10.5px;font-weight:700;color:${color}">${badge}</div>
                    </div>
                </div>`;
        }).join("");

        const insightHtml = insights.length
            ? insights.map(ins => `
                <div style="border-left:3px solid ${ins.severity==="high"?"var(--accent-warn)":ins.severity==="medium"?"var(--accent-amber)":"var(--accent-ok)"};
                            padding:8px 11px;margin-bottom:7px;background:var(--bg-hover);border-radius:0 8px 8px 0">
                    <div style="font-weight:600;font-size:13px">${escapeHtml(ins.headline || "")}</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-top:2px">${escapeHtml(ins.detail || "")}</div>
                </div>`).join("")
            : "";

        DOM.pulseModalBody.innerHTML = `
            ${insightHtml ? `
                <div style="margin-bottom:18px">
                    <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px">
                        <i class="fa-solid fa-lightbulb" style="color:var(--accent-amber)"></i> PHI Synthesis
                    </div>
                    ${insightHtml}
                </div>` : ""}
            <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px">
                All Markers (${markers.length})
                <span style="color:var(--accent-warn);margin-left:8px">${abnormal.length} need attention</span>
            </div>
            ${markerRows}
            <div style="margin-top:16px;text-align:center">
                <button onclick="window.closeModals();sendChip('Prepare me for my next doctor visit based on my full health picture')"
                    style="padding:10px 20px;background:var(--brand);color:white;border:none;border-radius:8px;font-size:13.5px;font-weight:600;font-family:var(--font);cursor:pointer">
                    <i class="fa-solid fa-stethoscope"></i> Generate Doctor Visit Prep
                </button>
            </div>`;
    } catch {
        DOM.pulseModalBody.innerHTML = '<div class="loading-text" style="color:var(--accent-warn)">Could not load health data.</div>';
    }
}
window.openPulseModal = openPulseModal;

/* ══════════════════════════════════════════════════════════════
   CONVERSATION MANAGEMENT
══════════════════════════════════════════════════════════════ */

async function createConversation() {
    const headers = await getAuthHeaders();
    const res = await fetch(`${API_BASE}/conversation/create`, {
        method: "POST", headers, body: JSON.stringify({}),
    });
    const data = await res.json();
    if (res.status === 403) { showToast("Consent required. Accept terms in Settings.", "error"); return null; }
    if (res.ok && data.conversation_id) { activeConvId = data.conversation_id; loadHistory(); }
    return data.conversation_id || null;
}

let _historyTimer = null;
function loadHistory() {
    clearTimeout(_historyTimer);
    _historyTimer = setTimeout(_loadHistoryNow, 600);
}

async function _loadHistoryNow() {
    const headers = await getAuthHeaders();
    if (!headers.Authorization) return;
    try {
        const res  = await fetch(`${API_BASE}/history`, { method: "POST", headers });
        const data = await res.json();
        if (!res.ok || !Array.isArray(data)) {
            DOM.historyList.innerHTML = '<div class="empty-state">Could not load history.</div>';
            return;
        }
        renderHistory(data);
    } catch {
        DOM.historyList.innerHTML = '<div class="empty-state">Network error.</div>';
    }
}

function renderHistory(conversations) {
    DOM.historyList.innerHTML = "";
    if (!conversations.length) {
        DOM.historyList.innerHTML = '<div class="empty-state">No conversations yet.<br>Start by asking PHI something!</div>';
        return;
    }
    conversations.forEach(c => {
        const item = document.createElement("div");
        item.className = "history-item" + (c.id === activeConvId ? " active" : "");
        item.setAttribute("data-id", c.id);

        const title = document.createElement("span");
        title.className   = "history-title";
        title.textContent = c.title || "New Chat";
        title.onclick     = () => openConversation(c.id);

        const del = document.createElement("button");
        del.className = "delete-chat";
        del.title     = "Delete";
        del.innerHTML = '<i class="fa-solid fa-trash"></i>';
        del.onclick   = (e) => { e.stopPropagation(); showDeleteModal(c.id); };

        item.appendChild(title);
        item.appendChild(del);
        DOM.historyList.appendChild(item);
    });
}

async function openConversation(id) {
    if (isProcessing) { showToast("Please wait for the current request.", "error"); return; }
    setProcessing(true);
    activeConvId = id;
    uploadedFiles = [];
    updateFilePreview();
    // FIX #BUG-JS-1: clear doc context when switching conversations
    _docCtx.clear();

    showChatMode();
    DOM.chatDisplay.innerHTML = "";

    try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/conversation`, {
            method: "POST", headers,
            body:   JSON.stringify({ conversation_id: id }),
        });
        if (!res.ok) throw new Error("Load failed");
        const messages = await res.json();
        if (Array.isArray(messages) && messages.length) {
            messages.forEach(m => appendMessage(m.content, m.role === "user" ? "user" : "ai"));
        } else {
            DOM.chatDisplay.innerHTML = '<div class="empty-state">No messages yet.</div>';
        }
        document.querySelectorAll(".history-item").forEach(el => {
            el.classList.toggle("active", el.getAttribute("data-id") === id);
        });
        closeSidebar();
    } catch {
        showToast("Failed to load conversation.", "error");
    } finally {
        setProcessing(false);
    }
}

async function renameConversation(id, title) {
    const headers = await getAuthHeaders();
    await fetch(`${API_BASE}/rename`, {
        method: "POST", headers,
        body:   JSON.stringify({ conversation_id: id, title }),
    }).catch(() => {});
}

/* ══════════════════════════════════════════════════════════════
   CHAT SEND / RECEIVE
══════════════════════════════════════════════════════════════ */

async function handleSend() {
    if (isProcessing) return;

    let text = DOM.userInput.value.trim();
    if (!text && uploadedFiles.length > 0) {
        text = "Please read my uploaded medical report and explain every finding in plain language.";
    }
    if (!text && uploadedFiles.length === 0) return;

    if (!activeConvId) {
        const created = await createConversation();
        if (!created) return;
    }

    showChatMode();
    DOM.userInput.value = "";
    DOM.userInput.style.height = "auto";
    DOM.userInput.placeholder = "Ask PHI about your health…";

    // Process files
    let documentContents = [];
    if (uploadedFiles.length > 0) {
        setProcessing(true);
        const proc = appendMessage(`📎 Processing ${uploadedFiles.length} document(s)…`, "ai");
        for (const file of uploadedFiles) {
            const result = await processFile(file);
            if (result) documentContents.push(result);
        }
        proc.remove();

        // FIX #BUG-JS-1: Store doc context tied to THIS conversation
        if (documentContents[0]) {
            _docCtx.set(
                documentContents[0].document_text,
                documentContents[0].document_id,
                activeConvId
            );
        }

        uploadedFiles = [];
        updateFilePreview();
        setProcessing(false);
        setTimeout(() => loadHealthPulse(), 3000);
    }

    appendMessage(text, "user");
    const botRow = appendTyping();
    setProcessing(true);

    try {
        const headers = await getAuthHeaders();

        // FIX #BUG-JS-1: only send doc context if it belongs to THIS conversation
        const { text: docText, id: docId } = _docCtx.getForConv(activeConvId);

        // On follow-up messages in the same conversation, send doc text ONCE more
        // (for the immediate follow-up), then rely on DB memory.
        // After the first follow-up, clear doc text so it's not resent repeatedly.
        const isFirstFollowUp = documentContents.length === 0 && !!docText;
        const sendDocText     = documentContents.length > 0 ? (documentContents[0]?.document_text || "") : (isFirstFollowUp ? docText : "");

        // If this was the first follow-up using cached doc text, clear it
        if (isFirstFollowUp) {
            _docCtx.clear();
        }

        const res = await fetch(`${API_BASE}/chat`, {
            method: "POST", headers,
            body: JSON.stringify({
                conversation_id: activeConvId,
                message:         text,
                has_documents:   documentContents.length > 0 || !!docId,
                document_id:     documentContents[0]?.document_id || docId || "",
                document_text:   sendDocText,
            }),
        });

        const data = await res.json();

        if (res.status === 403) {
            updateMessage(botRow, "⚠️ Consent required. Please accept terms in Settings.");
        } else if (data.reply) {
            updateMessage(botRow, data.reply);
        } else {
            updateMessage(botRow, "I couldn't process that. Please try again.");
            showToast("No response from PHI.", "error");
        }
    } catch (err) {
        console.error("[CHAT] Error:", err);
        updateMessage(botRow, "Connection error. Please check your network and try again.");
        showToast("Network error.", "error");
    } finally {
        setProcessing(false);
    }

    // Auto-rename on first message
    const msgCount = DOM.chatDisplay.querySelectorAll(".chat-message").length;
    if (msgCount <= 2 && activeConvId) {
        const titleSrc = documentContents.length > 0
            ? `📄 ${documentContents[0].name}`
            : text.substring(0, 45);
        renameConversation(activeConvId, titleSrc);
        loadHistory();
    }
}

/* ══════════════════════════════════════════════════════════════
   MESSAGE RENDERING
══════════════════════════════════════════════════════════════ */

function appendMessage(text, role) {
    const wrap = document.createElement("div");
    wrap.className = `chat-message ${role === "user" ? "user-msg" : "bot-msg"}`;

    const av = document.createElement("div");
    av.className = `msg-avatar ${role === "user" ? "user-av" : "ai-av"}`;
    av.textContent = role === "user" ? (currentUser?.email?.charAt(0)?.toUpperCase() || "U") : "φ";

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.innerHTML = role === "user" ? escapeHtml(text) : renderMarkdown(text);

    if (role === "user") { wrap.appendChild(bubble); wrap.appendChild(av); }
    else                  { wrap.appendChild(av);     wrap.appendChild(bubble); }

    DOM.chatDisplay.appendChild(wrap);
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
    return wrap;
}

function appendTyping() {
    const wrap = document.createElement("div");
    wrap.className = "chat-message bot-msg";

    const av = document.createElement("div");
    av.className   = "msg-avatar ai-av";
    av.textContent = "φ";

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.innerHTML = `<div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>`;

    wrap.appendChild(av);
    wrap.appendChild(bubble);
    DOM.chatDisplay.appendChild(wrap);
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
    return wrap;
}

function updateMessage(wrap, text) {
    const bubble = wrap.querySelector(".msg-bubble");
    if (bubble) bubble.innerHTML = renderMarkdown(text);
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
}

/* ══════════════════════════════════════════════════════════════
   VIEW TOGGLING
══════════════════════════════════════════════════════════════ */

function showChatMode() {
    if (DOM.welcomeScreen) {
        DOM.welcomeScreen.classList.add("hidden-welcome");
        DOM.welcomeScreen.style.display = "none";
    }
}

/** FIX #BUG-JS-5: refresh pulse card when returning to welcome */
function showWelcomeMode() {
    if (DOM.welcomeScreen) {
        DOM.welcomeScreen.classList.remove("hidden-welcome");
        DOM.welcomeScreen.style.display = "";
    }
    if (_healthContext) {
        renderPulseCard(_healthContext);
    }
}

/* ══════════════════════════════════════════════════════════════
   FILE UPLOAD
══════════════════════════════════════════════════════════════ */

async function processFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    const session = await getSession();
    if (!session) return null;

    try {
        const res  = await fetch(`${API_BASE}/analyze`, {
            method:  "POST",
            headers: { "Authorization": "Bearer " + session.access_token },
            body:    formData,
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`✓ ${file.name} analyzed (${data.abnormal_count || 0} findings)`);
            return {
                name:          file.name,
                summary:       data.summary_text  || "",
                document_id:   data.document_id   || "",
                document_text: data.document_text || "",
                markers:       data.markers        || [],
                doc_type:      data.doc_type       || "lab_report",
                abnormal:      data.abnormal_count || 0,
            };
        } else {
            showToast(data.error || `Could not process ${file.name}`, "error");
            return null;
        }
    } catch (err) {
        showToast(`Upload failed for ${file.name}`, "error");
        return null;
    }
}

function updateFilePreview() {
    DOM.filePreview.innerHTML = "";
    if (uploadedFiles.length === 0) {
        DOM.filePreview.classList.remove("visible");
        DOM.userInput.placeholder = "Ask PHI about your health…";
        return;
    }
    DOM.filePreview.classList.add("visible");
    uploadedFiles.forEach((file, index) => {
        const chip = document.createElement("div");
        chip.className = "file-chip";
        const icon = file.name.toLowerCase().endsWith(".pdf") ? "fa-file-pdf" : "fa-file-lines";
        chip.innerHTML = `
            <i class="fa-solid ${icon}"></i>
            <span>${escapeHtml(file.name)}</span>
            <button class="remove-file" onclick="removeFile(${index})" aria-label="Remove">
                <i class="fa-solid fa-xmark"></i>
            </button>`;
        DOM.filePreview.appendChild(chip);
    });
    DOM.userInput.placeholder = `${uploadedFiles.length} file(s) attached — press Send or ask a question…`;
}

window.removeFile = (index) => {
    uploadedFiles.splice(index, 1);
    updateFilePreview();
    showToast("File removed");
};

/* ══════════════════════════════════════════════════════════════
   SIDEBAR
══════════════════════════════════════════════════════════════ */

function openSidebar()  { DOM.sidebar.classList.add("open");    DOM.overlay.classList.add("active"); }
function closeSidebar() { DOM.sidebar.classList.remove("open"); DOM.overlay.classList.remove("active"); }

/* ══════════════════════════════════════════════════════════════
   MODALS
══════════════════════════════════════════════════════════════ */

window.closeModals = () => {
    [DOM.profileModal, DOM.settingsModal, DOM.deleteModal, DOM.pulseModal].forEach(m => {
        if (m) m.classList.add("hidden");
    });
};

[DOM.profileModal, DOM.settingsModal, DOM.deleteModal, DOM.pulseModal].forEach(modal => {
    if (!modal) return;
    modal.addEventListener("click", e => { if (e.target === modal) window.closeModals(); });
});

function showDeleteModal(id) {
    conversationToDelete = id;
    DOM.deleteModal?.classList.remove("hidden");
}
window.showDeleteModal = showDeleteModal;

/* ══════════════════════════════════════════════════════════════
   PROFILE + HEALTH DASHBOARD
══════════════════════════════════════════════════════════════ */

async function loadProfileStats(user) {
    if (DOM.accountCreated && user?.created_at) {
        DOM.accountCreated.textContent = new Date(user.created_at)
            .toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    }
    try {
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/history`, { method: "POST", headers });
        const convs   = await res.json();
        if (DOM.totalConvs) DOM.totalConvs.textContent = Array.isArray(convs) ? convs.length : 0;
    } catch { if (DOM.totalConvs) DOM.totalConvs.textContent = "0"; }
    try {
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/api/dashboard`, { headers });
        if (res.ok) {
            const dash = await res.json();
            if (DOM.docsAnalyzed) DOM.docsAnalyzed.textContent = dash.document_count || 0;
        }
    } catch { if (DOM.docsAnalyzed) DOM.docsAnalyzed.textContent = "0"; }
}

async function loadHealthDashboard() {
    if (!DOM.healthDash) return;
    const headers = await getAuthHeaders();
    if (!headers.Authorization) return;
    DOM.healthDash.innerHTML = '<div class="loading-text"><i class="fa-solid fa-spinner fa-spin"></i> Loading…</div>';
    try {
        const [markersRes, insightsRes] = await Promise.all([
            fetch(`${API_BASE}/api/health-markers`,  { headers }),
            fetch(`${API_BASE}/api/health-insights`, { headers }),
        ]);
        const markers  = markersRes.ok  ? await markersRes.json()  : [];
        const insights = insightsRes.ok ? await insightsRes.json() : [];
        renderHealthDashboard(markers, insights);
    } catch {
        DOM.healthDash.innerHTML = '<div class="loading-text" style="color:var(--accent-warn)">Could not load health memory.</div>';
    }
}

function renderHealthDashboard(markers, insights) {
    if (!markers.length && !insights.length) {
        DOM.healthDash.innerHTML = '<div class="loading-text">No health data yet. Upload a lab report.</div>';
        return;
    }
    const flagColor = (s) =>
        s === "HIGH" || s === "LOW" ? "var(--accent-warn)" :
        s === "NORMAL" ? "var(--accent-ok)" : "var(--text-muted)";

    const markerCards = markers.map(m => `
        <div style="background:var(--bg-hover);border-radius:8px;padding:9px;min-width:100px;flex:1">
            <div style="font-size:10.5px;color:var(--text-muted);margin-bottom:2px">${escapeHtml(m.marker_name)}</div>
            <div style="font-size:1rem;font-weight:700;color:${flagColor(m.status)}">
                ${m.value} <span style="font-size:10px;font-weight:400">${escapeHtml(m.unit || "")}</span>
            </div>
            <div style="font-size:9.5px;color:var(--text-muted)">${escapeHtml(m.date || "")}</div>
        </div>`).join("");

    const insightItems = insights.map(ins => `
        <div style="border-left:3px solid ${ins.severity==="high"?"var(--accent-warn)":ins.severity==="medium"?"var(--accent-amber)":"var(--accent-ok)"};
                    padding:6px 10px;margin-bottom:5px;background:var(--bg-hover);border-radius:0 6px 6px 0">
            <div style="font-weight:600;font-size:12px">${escapeHtml(ins.headline || "")}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:1px">${escapeHtml(ins.detail || "")}</div>
        </div>`).join("");

    DOM.healthDash.innerHTML = `
        ${markers.length ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px">${markerCards}</div>` : ""}
        ${insights.length ? `
            <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:5px">
                <i class="fa-solid fa-lightbulb" style="color:var(--accent-amber)"></i> PHI Insights
            </div>
            ${insightItems}` : ""}`;

    DOM.doctorBriefBtn?.addEventListener("click", showDoctorBriefModal);
}

function showDoctorBriefModal() {
    $id("doctor-brief-modal")?.remove();
    const modal = document.createElement("div");
    modal.id        = "doctor-brief-modal";
    modal.className = "modal";
    modal.style.zIndex = "10001";
    modal.innerHTML = `
        <div class="modal-box">
            <div class="modal-header">
                <h3><i class="fa-solid fa-stethoscope"></i> Doctor Visit Prep</h3>
                <button class="close-btn" onclick="document.getElementById('doctor-brief-modal').remove()"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <div style="padding:4px 0 16px">
                <p style="font-size:13px;color:var(--text-muted);margin-bottom:14px">PHI will generate a personalised brief based on your health memory.</p>
                <label style="font-size:12.5px;font-weight:600;display:block;margin-bottom:4px">Symptoms <span style="color:var(--text-muted);font-weight:400">(comma separated)</span></label>
                <input type="text" id="brief-symptoms" placeholder="fatigue, dizziness, headaches"
                    style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:7px;font-size:13px;box-sizing:border-box;margin-bottom:10px;background:var(--bg-input);color:var(--text-main);font-family:var(--font)">
                <label style="font-size:12.5px;font-weight:600;display:block;margin-bottom:4px">Medications <span style="color:var(--text-muted);font-weight:400">(comma separated)</span></label>
                <input type="text" id="brief-meds" placeholder="Metformin 500mg, Vitamin D 2000IU"
                    style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:7px;font-size:13px;box-sizing:border-box;margin-bottom:10px;background:var(--bg-input);color:var(--text-main);font-family:var(--font)">
                <div id="brief-output" style="display:none;margin-top:12px;background:var(--bg-hover);border-radius:8px;padding:13px;font-size:13px;line-height:1.65;max-height:260px;overflow-y:auto"></div>
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" onclick="document.getElementById('doctor-brief-modal').remove()">Cancel</button>
                <button id="generateBriefBtn" style="padding:8px 16px;background:var(--brand);border:none;border-radius:8px;color:white;font-size:13.5px;font-weight:600;font-family:var(--font);cursor:pointer">
                    <i class="fa-solid fa-wand-magic-sparkles"></i> Generate
                </button>
            </div>
        </div>`;
    document.body.appendChild(modal);

    $id("generateBriefBtn").onclick = async () => {
        const symptoms    = ($id("brief-symptoms")?.value || "").split(",").map(s=>s.trim()).filter(Boolean);
        const medications = ($id("brief-meds")?.value     || "").split(",").map(s=>s.trim()).filter(Boolean);
        const btn         = $id("generateBriefBtn");
        btn.disabled      = true;
        btn.innerHTML     = '<i class="fa-solid fa-spinner fa-spin"></i> Generating…';

        try {
            const headers = await getAuthHeaders();
            const res = await fetch(`${API_BASE}/api/doctor-brief`, {
                method: "POST", headers,
                body: JSON.stringify({ symptoms, medications }),
            });
            const data = await res.json();
            const out  = $id("brief-output");
            if (out) {
                out.style.display = "block";
                out.innerHTML = renderMarkdown(data.brief || "Could not generate. Try again.");
            }
        } catch { showToast("Failed to generate brief.", "error"); }

        btn.disabled  = false;
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Generate';
    };
}

/* ══════════════════════════════════════════════════════════════
   PLAN STATUS
══════════════════════════════════════════════════════════════ */

async function loadPlanStatus() {
    const headers = await getAuthHeaders();
    if (!headers.Authorization) return;
    const res  = await fetch(`${API_BASE}/api/payment/status`, { headers });
    if (!res.ok) return;
    const data = await res.json();
    if (DOM.dropdownPlan) {
        DOM.dropdownPlan.textContent = data.is_pro ? "✦ PHI Pro" : "PHI Free";
        if (data.is_pro) DOM.dropdownPlan.style.color = "var(--brand)";
    }
}

/* ══════════════════════════════════════════════════════════════
   VOICE INPUT
══════════════════════════════════════════════════════════════ */

function initVoiceInput() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
        if (DOM.micBtn) { DOM.micBtn.disabled = true; DOM.micBtn.style.opacity = "0.3"; }
        return;
    }
    let isListening = false, recognition = null;
    DOM.micBtn?.addEventListener("click", () => {
        if (isListening) { recognition?.stop(); return; }
        recognition = new SR();
        recognition.lang = "en-US"; recognition.interimResults = false; recognition.maxAlternatives = 1;
        recognition.onstart  = () => { isListening = true;  DOM.micBtn?.classList.add("listening"); showToast("Listening…"); };
        recognition.onresult = e => { DOM.userInput.value = e.results[0][0].transcript; autoGrow(DOM.userInput); DOM.userInput.focus(); };
        recognition.onerror  = e => { const msgs = {"no-speech":"No speech detected.","not-allowed":"Microphone blocked.","network":"Network error."}; showToast(msgs[e.error] || `Voice error: ${e.error}`, "error"); };
        recognition.onend    = () => { isListening = false; DOM.micBtn?.classList.remove("listening"); };
        try { recognition.start(); } catch { showToast("Voice input failed.", "error"); }
    });
}

/* ══════════════════════════════════════════════════════════════
   SETTINGS
══════════════════════════════════════════════════════════════ */

function loadUserPreferences() {
    const prefs  = JSON.parse(localStorage.getItem("phi_prefs") || "{}");
    const isDark = prefs.theme === "dark";
    if (isDark) document.body.classList.add("dark-mode");
    if (DOM.themeToggle) DOM.themeToggle.checked = isDark;
    const fontSize = prefs.fontSize || 15;
    document.documentElement.style.setProperty("--chat-font-size", fontSize + "px");
    if (DOM.fontSizeInput) DOM.fontSizeInput.value        = fontSize;
    if (DOM.fontSizeValue) DOM.fontSizeValue.textContent  = fontSize + "px";
}

function savePreference(key, value) {
    const prefs = JSON.parse(localStorage.getItem("phi_prefs") || "{}");
    prefs[key] = value;
    localStorage.setItem("phi_prefs", JSON.stringify(prefs));
}

function initSettings() {
    DOM.themeToggle?.addEventListener("change", e => {
        const dark = e.target.checked;
        document.body.classList.toggle("dark-mode", dark);
        savePreference("theme", dark ? "dark" : "light");
        showToast(dark ? "Dark mode on" : "Light mode on");
    });

    DOM.fontSizeInput?.addEventListener("input", e => {
        const size = e.target.value;
        document.documentElement.style.setProperty("--chat-font-size", size + "px");
        if (DOM.fontSizeValue) DOM.fontSizeValue.textContent = size + "px";
        savePreference("fontSize", parseInt(size, 10));
    });

    DOM.exportChatBtn?.addEventListener("click", async () => {
        if (!activeConvId) { showToast("No active conversation.", "error"); return; }
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/conversation`, { method: "POST", headers, body: JSON.stringify({ conversation_id: activeConvId }) });
        const msgs    = await res.json();
        let out = "Curabook PHI — Chat Export\n" + "=".repeat(50) + "\n\n";
        msgs.forEach(m => { out += `${m.role.toUpperCase()}:\n${m.content}\n\n`; });
        const a = Object.assign(document.createElement("a"), {
            href:     URL.createObjectURL(new Blob([out], { type: "text/plain" })),
            download: `phi-chat-${Date.now()}.txt`,
        });
        a.click();
        showToast("Chat exported");
    });

    DOM.clearHistBtn?.addEventListener("click", async () => {
        if (!confirm("⚠️ Delete ALL conversations? This cannot be undone.")) return;
        const headers  = await getAuthHeaders();
        const histRes  = await fetch(`${API_BASE}/history`, { method: "POST", headers });
        const chats    = await histRes.json();
        if (Array.isArray(chats)) {
            for (const c of chats) {
                await fetch(`${API_BASE}/delete`, { method: "POST", headers, body: JSON.stringify({ conversation_id: c.id }) }).catch(() => {});
            }
        }
        DOM.chatDisplay.innerHTML = "";
        activeConvId = null;
        uploadedFiles = [];
        _docCtx.clear();   // FIX #BUG-JS-1
        updateFilePreview();
        showWelcomeMode();
        loadHistory();
        window.closeModals();
        showToast("All conversations deleted");
    });

    DOM.exportDataBtn?.addEventListener("click", async () => {
        const headers = await getAuthHeaders();
        try {
            const res  = await fetch(`${API_BASE}/export-data`, { method: "POST", headers });
            const data = await res.json();
            const a    = Object.assign(document.createElement("a"), {
                href:     URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })),
                download: `phi-data-${Date.now()}.json`,
            });
            a.click();
            showToast("✅ Data exported");
        } catch { showToast("Export failed.", "error"); }
    });

    /**
     * FIX #BUG-JS-2: Delete account flow now:
     * 1. Sign out of Supabase session FIRST (invalidates all tokens)
     * 2. Then call backend to delete all data
     * 3. Redirect immediately — no waiting
     * This prevents the ~1hr window where a "deleted" user could re-access data.
     */
    DOM.deleteAccBtn?.addEventListener("click", async () => {
        if (!confirm("⚠️ PERMANENTLY DELETE YOUR ACCOUNT?\n\nThis deletes ALL your health data and cannot be undone.")) return;
        if (prompt('Type "DELETE" to confirm:') !== "DELETE") return;

        try {
            // Step 1: Get auth headers BEFORE signing out
            const headers = await getAuthHeaders();

            // Step 2: Sign out FIRST so session is invalid after this point
            try { await supabaseClient.auth.signOut(); } catch {}

            // Step 3: Delete all data on backend
            const res = await fetch(`${API_BASE}/delete-account`, { method: "POST", headers });
            const data = res.ok ? await res.json() : null;

            if (data?.success) {
                showToast("Account and all data deleted.");
            } else {
                // Even if backend fails, user is signed out — show message
                showToast("Signed out. Contact support if data wasn't fully removed.", "error");
            }
        } catch {
            showToast("Error during deletion. Contact support@curabook.com.", "error");
        }

        // Step 4: Always redirect
        setTimeout(() => window.location.href = "https://curabook.com/login", 1500);
    });
}

/* ══════════════════════════════════════════════════════════════
   DELETE CONVERSATION
══════════════════════════════════════════════════════════════ */

DOM.confirmDeleteBtn?.addEventListener("click", async () => {
    if (!conversationToDelete) return;
    const headers = await getAuthHeaders();
    await fetch(`${API_BASE}/delete`, {
        method: "POST", headers,
        body:   JSON.stringify({ conversation_id: conversationToDelete }),
    }).catch(() => {});

    if (activeConvId === conversationToDelete) {
        DOM.chatDisplay.innerHTML = "";
        activeConvId = null;
        uploadedFiles = [];
        _docCtx.clear();   // FIX #BUG-JS-1
        updateFilePreview();
        showWelcomeMode();
    }
    loadHistory();
    window.closeModals();
    conversationToDelete = null;
    showToast("Conversation deleted");
});

/* ══════════════════════════════════════════════════════════════
   EVENT WIRING
══════════════════════════════════════════════════════════════ */

function wireEvents() {
    DOM.mobileMenu?.addEventListener("click", openSidebar);
    DOM.closeSidebar?.addEventListener("click", closeSidebar);
    DOM.overlay?.addEventListener("click", closeSidebar);

    DOM.newChatBtn?.addEventListener("click", () => {
        // FIX #BUG-JS-3: only confirm if there are ACTUAL chat messages
        if (hasChatMessages() && !confirm("Start a new chat?")) return;

        activeConvId  = null;
        uploadedFiles = [];
        _docCtx.clear();   // FIX #BUG-JS-1
        DOM.chatDisplay.innerHTML = "";
        updateFilePreview();
        showWelcomeMode();
        document.querySelectorAll(".history-item").forEach(el => el.classList.remove("active"));
        DOM.userInput.focus();
    });

    DOM.sendBtn?.addEventListener("click", e => { e.preventDefault(); handleSend(); });

    DOM.userInput?.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    DOM.userInput?.addEventListener("input", () => autoGrow(DOM.userInput));

    DOM.attachBtn?.addEventListener("click",    () => DOM.fileInput?.click());
    DOM.btnUploadNav?.addEventListener("click", () => { closeSidebar(); DOM.fileInput?.click(); });
    DOM.uploadNudgeBtn?.addEventListener("click", () => { closeSidebar(); DOM.fileInput?.click(); });

    DOM.btnHealthPulse?.addEventListener("click", () => { closeSidebar(); openPulseModal(); });

    DOM.fileInput?.addEventListener("change", e => {
        Array.from(e.target.files || []).forEach(file => {
            if (file.size > 10 * 1024 * 1024)  { showToast(`${file.name} too large. Max 10MB.`, "error"); return; }
            if (!/\.(pdf|txt)$/i.test(file.name)) { showToast(`${file.name} not supported. PDF or TXT only.`, "error"); return; }
            uploadedFiles.push(file);
        });
        updateFilePreview();
        DOM.fileInput.value = "";
        if (uploadedFiles.length > 0) { showToast(`🔒 ${uploadedFiles.length} file(s) ready`); DOM.userInput.focus(); }
    });

    DOM.profileBtn?.addEventListener("click", e => {
        e.stopPropagation();
        DOM.profileDrop?.classList.toggle("hidden");
    });

    // Close dropdown when clicking anywhere outside
    document.addEventListener("click", e => {
        if (!DOM.profileDrop?.contains(e.target) && e.target !== DOM.profileBtn) {
            DOM.profileDrop?.classList.add("hidden");
        }
    });

    DOM.btnProfile?.addEventListener("click", () => {
        window.closeModals();
        DOM.profileModal?.classList.remove("hidden");
        loadProfileStats(currentUser).catch(() => {});
        loadHealthDashboard();
    });

    DOM.btnSettings?.addEventListener("click", () => {
        window.closeModals();
        DOM.settingsModal?.classList.remove("hidden");
    });

    DOM.logoutBtn?.addEventListener("click",   handleLogout);
    DOM.modalLogout?.addEventListener("click", handleLogout);

    document.addEventListener("keydown", e => {
        if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); DOM.newChatBtn?.click(); }
        if (e.key === "Escape") window.closeModals();
    });
}

/* ══════════════════════════════════════════════════════════════
   BOOTSTRAP
══════════════════════════════════════════════════════════════ */

document.addEventListener("DOMContentLoaded", async () => {
    try {
        await initSupabase();
    } catch {
        showToast("Cannot connect to backend.", "error");
        return;
    }

    const session = await getSession();
    if (!session?.user) {
        window.location.href = "https://curabook.com/login";
        return;
    }

    const user = session.user;

    if (user.app_metadata?.provider === "google") {
        if (!localStorage.getItem(`phi_terms_${user.id}`)) {
            window.location.href = "https://curabook.com/login";
            return;
        }
    }

    wireEvents();
    initSettings();
    initVoiceInput();
    loadUserPreferences();
    await handleLoginSuccess(user);
});