/**
 * script.js — Curabook PHI  v4.0 — April 15 US Launch
 *
 * CHANGES FROM v3.5:
 *
 * #OAUTH-FIX     onAuthStateChange is wired as the FIRST thing after
 *                createClient(). It catches SIGNED_IN from the OAuth hash
 *                before anything else touches the URL. Hash is cleared after
 *                the session is captured.
 *
 * #TEMPORAL      Pulse modal and profile modal now show three temporal
 *                sections:
 *                  1. "Your Story"        — Health Persona (GET /api/persona)
 *                  2. "Current Snapshot"  — latest labs with status badges
 *                  3. "Active Insights"   — Observation Cards (POST /api/correlate)
 *
 * #BEHAVIORAL-LOG Full wire of Log Activity modal → POST /api/behavioral-logs,
 *                 with metric tabs, validation, recent-entry preview.
 *
 * #ADVOCACY      advocacyInfoBtn → advocacy-guide-modal → "Generate My Brief"
 *                fires the PA chat query.
 *
 * #PERSONA-POLL  After a document upload, if the server returns
 *                `persona_refresh: true`, we poll GET /api/persona once (750ms
 *                delay) and update the "Your Story" section live.
 *
 * #EMPTY-STATES  Every data section shows a branded empty-state card with a
 *                direct "Upload Report" CTA when no data exists.
 *
 * #DISCLAIMER    Every AI-generated block appended to chat ends with the
 *                ⚕️ informational footer. The append functions enforce this.
 */

"use strict";

/* ── Constants ─────────────────────────────────────────────────────────── */
const API_BASE = "https://api.curabook.com";

/* ── App state ─────────────────────────────────────────────────────────── */
let supabaseClient       = null;
let currentUser          = null;
let currentUserName      = "";
let activeConvId         = null;
let uploadedFiles        = [];
let isProcessing         = false;
let conversationToDelete = null;
let _healthContext       = null;
let _personaText         = "";     // cached persona from /api/persona

const _chipTexts = new Map();
let   _chipCounter = 0;

/** Document context: raw text is sent ONCE on first turn after upload, then cleared. */
const _docCtx = {
    text: null, id: null, convId: null,
    set(t, i, c)  { this.text = t || null; this.id = i || null; this.convId = c || null; },
    getForConv(c) { return this.convId === c ? { text: this.text, id: this.id } : { text: null, id: null }; },
    clear()       { this.text = null; this.id = null; this.convId = null; },
};

/** Simple TTL cache keyed by string. */
const _cache = {
    _s: {},
    set(k, v, ms = 30000) { this._s[k] = { v, e: Date.now() + ms }; },
    get(k) { const e = this._s[k]; return e && e.e > Date.now() ? e.v : null; },
    del(k) { delete this._s[k]; },
    clear() { this._s = {}; },
};

/* ── DOM refs ──────────────────────────────────────────────────────────── */
const $id = id => document.getElementById(id);

const DOM = {
    sidebar:          $id("sidebar"),
    overlay:          $id("overlay"),
    closeSidebar:     $id("close-sidebar-btn"),
    mobileMenu:       $id("mobile-menu-btn"),
    avatarInitial:    $id("avatar-initial"),
    dropdownEmail:    $id("dropdown-email"),
    dropdownPlan:     $id("dropdown-plan-label"),
    profileBtn:       $id("profileBtn"),
    profileDrop:      $id("profileDropdown"),
    logoutBtn:        $id("logoutBtn"),
    welcomeScreen:    $id("welcomeScreen"),
    welcomeHero:      $id("welcomeHero"),
    welcomeChips:     $id("welcomeChips"),
    uploadNudge:      $id("uploadNudge"),
    uploadNudgeBtn:   $id("uploadNudgeBtn"),
    pulseCard:        $id("pulseCard"),
    pulseLoading:     $id("pulseLoading"),
    pulseContent:     $id("pulseContent"),
    chatDisplay:      $id("chat-display"),
    userInput:        $id("userInput"),
    sendBtn:          $id("sendBtn"),
    attachBtn:        $id("attachBtn"),
    fileInput:        $id("fileInput"),
    micBtn:           $id("micBtn"),
    filePreview:      $id("file-preview-container"),
    newChatBtn:       $id("newChatBtn"),
    historyList:      $id("historyList"),
    userEmailDisp:    $id("user-email-display"),
    btnUploadNav:     $id("btn-upload-nav"),
    btnHealthPulse:   $id("btn-health-pulse"),
    btnLogActivity:   $id("btn-log-activity"),
    logActivityModal: $id("log-activity-modal"),
    advocacyGuideModal: $id("advocacy-guide-modal"),
    advocacyInfoBtn:  $id("advocacyInfoBtn"),
    profileModal:     $id("profile-modal"),
    settingsModal:    $id("settings-modal"),
    deleteModal:      $id("delete-modal"),
    pulseModal:       $id("pulse-modal"),
    pulseModalBody:   $id("pulse-modal-body"),
    modalAvatar:      $id("modal-avatar"),
    modalEmail:       $id("modal-email"),
    accountCreated:   $id("account-created"),
    totalConvs:       $id("total-conversations"),
    docsAnalyzed:     $id("documents-analyzed"),
    healthDash:       $id("health-dashboard"),
    doctorBriefBtn:   $id("doctorBriefBtn"),
    btnProfile:       $id("btn-sidebar-profile"),
    btnSettings:      $id("btn-sidebar-settings"),
    modalLogout:      $id("modalLogout"),
    themeToggle:      $id("themeToggle"),
    fontSizeInput:    $id("fontSizeInput"),
    fontSizeValue:    $id("fontSizeValue"),
    exportChatBtn:    $id("exportChatBtn"),
    clearHistBtn:     $id("clearHistoryBtn"),
    exportDataBtn:    $id("exportDataBtn"),
    deleteAccBtn:     $id("deleteAccountBtn"),
    confirmDeleteBtn: $id("confirmDeleteBtn"),
};

/* ╔══════════════════════════════════════════════════════════════════════
   UTILITIES
══════════════════════════════════════════════════════════════════════╝ */

async function safeJson(res) {
    if (!res) return { error: "No response" };
    const text = await res.text().catch(() => "");
    if (!text.trim()) return { error: `Empty response (HTTP ${res.status})` };
    try { return JSON.parse(text); }
    catch { return { error: `Server error (HTTP ${res.status})` }; }
}

function showToast(msg, type = "success") {
    const el = document.createElement("div");
    el.className = type === "success" ? "success-toast" : "error-toast";
    el.innerHTML = `<i class="fa-solid fa-${type === "success" ? "circle-check" : "circle-exclamation"}"></i> ${escapeHtml(msg)}`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function escapeHtml(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
    const md = typeof marked !== "undefined" ? marked.parse(String(text || "")) : escapeHtml(String(text || ""));
    // Enforce disclaimer presence on every AI response
    const disclaimer = "⚕️";
    return md.includes(disclaimer) ? md : md + `<p class="phi-disclaimer">${disclaimer} <em>PHI provides health information only — not medical advice. Always consult your doctor.</em></p>`;
}

function autoGrow(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 150) + "px";
}

function setProcessing(on) {
    isProcessing = on;
    if (DOM.sendBtn)   DOM.sendBtn.disabled   = on;
    if (DOM.userInput) DOM.userInput.disabled = on;
    if (DOM.sendBtn)   DOM.sendBtn.innerHTML  = on
        ? '<i class="fa-solid fa-spinner fa-spin"></i>'
        : '<i class="fa-solid fa-arrow-up"></i>';
}

function hasChatMessages() {
    return DOM.chatDisplay.querySelectorAll(".chat-message").length > 0;
}

function _greeting(name) {
    const h = new Date().getHours();
    const p = h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
    return name ? `Good ${p}, ${name}` : `Good ${p}`;
}

function _today_iso() {
    return new Date().toISOString().slice(0, 10);
}

/* ╔══════════════════════════════════════════════════════════════════════
   #OAUTH-FIX — AUTH
   onAuthStateChange is wired BEFORE any other session checks.
   This ensures the SIGNED_IN event from the OAuth hash fires correctly.
══════════════════════════════════════════════════════════════════════╝ */

function initSupabase() {
    const URL = "https://pbeaawlxdcrdbvlmpqhc.supabase.co";
    const KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBiZWFhd2x4ZGNyZGJ2bG1wcWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMDk0MzksImV4cCI6MjA5MTU4NTQzOX0.6bUpYrDbe0mQjjBHX8Qscj-5R8i4-SqAtW_Z1UFzJ10";

    supabaseClient = supabase.createClient(URL, KEY, {
        auth: { detectSessionInUrl: true, persistSession: true, autoRefreshToken: true },
    });
    window.supabaseClient = supabaseClient;

    // ── #OAUTH-FIX: Wire state listener FIRST ──────────────────────────────
    // The SIGNED_IN event fires when Supabase parses the #access_token hash.
    // Any code that runs before this listener is wired risks missing the event.
    supabaseClient.auth.onAuthStateChange(async (event, session) => {
        if (event === "SIGNED_IN" || event === "USER_UPDATED") {
            if (session?.user && !currentUser) {
                // Clear the OAuth hash from the URL cleanly
                if (window.location.hash && window.location.hash.includes("access_token")) {
                    history.replaceState(null, document.title, window.location.pathname);
                }
                await handleLoginSuccess(session.user);
            }
        }
        if (event === "SIGNED_OUT") {
            currentUser = null;
            _cache.clear();
        }
        if (event === "TOKEN_REFRESHED" && session?.user) {
            currentUser = session.user;
        }
    });
}

async function getSession() {
    const { data } = await supabaseClient.auth.getSession();
    return data.session;
}

async function getAuthHeaders() {
    const s = await getSession();
    if (!s) return {};
    return { "Content-Type": "application/json", "Authorization": "Bearer " + s.access_token };
}

function _extractFirstName(user) {
    const meta = user.user_metadata || {};
    if (meta.first_name) return meta.first_name;
    if (meta.name)       return meta.name.split(" ")[0];
    const local = (user.email || "").split("@")[0];
    return local ? local.charAt(0).toUpperCase() + local.slice(1).replace(/[._-]/g, " ").split(" ")[0] : "";
}

async function handleLoginSuccess(user) {
    currentUser     = user;
    currentUserName = _extractFirstName(user);
    _docCtx.clear();
    _cache.clear();
    _personaText = "";

    const initial = (user.email || "?")[0].toUpperCase();
    if (DOM.avatarInitial) DOM.avatarInitial.textContent = initial;
    if (DOM.dropdownEmail) DOM.dropdownEmail.textContent = user.email;
    if (DOM.userEmailDisp) DOM.userEmailDisp.textContent = user.email;
    if (DOM.modalEmail)    DOM.modalEmail.textContent    = user.email;
    if (DOM.modalAvatar)   DOM.modalAvatar.textContent   = initial;

    await Promise.all([
        saveUserConsents().catch(() => {}),
        loadHistory(),
        loadPlanStatus().catch(() => {}),
    ]);

    loadHealthPulse().catch(() => {});
    // Warm the persona cache in background
    loadPersona(false).catch(() => {});

    showToast(currentUserName ? `Welcome back, ${currentUserName} 👋` : "Welcome back 👋");
    _carryOverDemoDoc();

    if (new URLSearchParams(location.search).get("upload") === "1") {
        history.replaceState({}, "", location.pathname);
        setTimeout(() => DOM.fileInput?.click(), 600);
    }
}

async function saveUserConsents() {
    const h = await getAuthHeaders();
    if (!h.Authorization) return;
    await fetch(`${API_BASE}/api/consent`, {
        method: "POST", headers: h,
        body: JSON.stringify({ consents: ["data_processing", "ai_processing", "document_processing"] }),
    }).catch(() => {});
}

async function handleLogout() {
    if (!confirm("Sign out of Curabook PHI?")) return;
    DOM.profileDrop?.classList.add("hidden");
    try { await supabaseClient.auth.signOut(); } catch {}
    window.location.href = "/login";
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
        _docCtx.set(docText, null, activeConvId);
        if (docSummary) appendMessage(`📋 **${docName}** (from demo)\n\n${docSummary}`, "ai");
        DOM.userInput.value = "Please explain my uploaded report thoroughly.";
        handleSend();
    }, 1200);
}

/* ╔══════════════════════════════════════════════════════════════════════
   PERSONA  (#TEMPORAL — "Your Story")
══════════════════════════════════════════════════════════════════════╝ */

/**
 * Load the 200-word Health Persona from GET /api/persona.
 * Caches in module-level _personaText for use in dashboard sections.
 * @param {boolean} force - skip local cache and always re-fetch
 */
async function loadPersona(force = false) {
    if (!force && _personaText && _personaText.length > 20) return _personaText;

    try {
        const h   = await getAuthHeaders();
        if (!h.Authorization) return "";
        const res = await fetch(`${API_BASE}/api/persona`, { headers: h });
        const d   = await safeJson(res);
        if (res.ok && d.persona && d.persona.length > 20) {
            _personaText = d.persona;
            return _personaText;
        }
    } catch (e) {
        console.warn("[PERSONA]", e);
    }
    return "";
}

/** Render the "Your Story" section HTML from cached persona text. */
function _renderPersonaSection(persona) {
    if (!persona || persona.length < 20) {
        return `<div class="temporal-empty-state">
            <i class="fa-solid fa-brain"></i>
            <p>Your health story builds as you upload reports and chat with PHI.</p>
        </div>`;
    }
    return `<div class="persona-block">
        <p class="persona-text">${escapeHtml(persona)}</p>
        <p class="persona-disclaimer">⚕️ <em>This synthesis is informational only — not a medical assessment.</em></p>
    </div>`;
}

/* ╔══════════════════════════════════════════════════════════════════════
   HEALTH PULSE (welcome screen card)
══════════════════════════════════════════════════════════════════════╝ */

async function loadHealthPulse() {
    if (DOM.pulseLoading) DOM.pulseLoading.classList.remove("hidden");
    if (DOM.pulseContent) DOM.pulseContent.classList.add("hidden");
    if (DOM.pulseCard)    DOM.pulseCard.style.display = "";

    const cached = _cache.get("dashboard");
    if (cached) {
        _healthContext = cached;
        renderPulseCard(cached);
        generateContextualChips(cached);
        return;
    }

    try {
        const h   = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/dashboard`, { headers: h });
        const d   = await safeJson(res);
        if (!res.ok || d.error || !d.total_markers) { showNoDataState(); return; }
        _cache.set("dashboard", d, 30000);
        _healthContext = d;
        renderPulseCard(d);
        generateContextualChips(d);
    } catch (e) {
        console.warn("[PULSE]", e);
        showNoDataState();
    }
}

function showNoDataState() {
    if (DOM.pulseCard)   DOM.pulseCard.style.display = "none";
    if (DOM.welcomeHero) DOM.welcomeHero.style.display = "";
    if (DOM.uploadNudge) DOM.uploadNudge.classList.remove("hidden");

    if (currentUserName && DOM.welcomeHero) {
        const titleEl = DOM.welcomeHero.querySelector(".welcome-title");
        if (titleEl) titleEl.innerHTML = `${escapeHtml(currentUserName)}'s health <em>co-pilot</em>`;
    }

    setChips([
        { icon: "fa-file-medical",    text: "Upload my first lab report" },
        { icon: "fa-circle-question", text: "What can PHI do for me?" },
        { icon: "fa-stethoscope",     text: "How do I prepare for a doctor visit?" },
        { icon: "fa-chart-line",      text: "What health markers should I track?" },
    ]);
}

function renderPulseCard(d) {
    if (!DOM.pulseLoading || !DOM.pulseContent) return;
    DOM.pulseLoading.classList.add("hidden");
    DOM.pulseContent.classList.remove("hidden");

    const abnormal = d.abnormal_count || 0;
    const total    = d.total_markers  || 0;
    const feed     = d.feed           || [];
    const trends   = d.trends         || [];

    let sc = "healthy", label = "✓ All Looking Good";
    const greet = currentUserName ? _greeting(currentUserName) + " — " : "";
    let headline = `${greet}all ${total} tracked markers are within normal ranges.`;

    if (abnormal >= 3)     { sc = "urgent";   label = "⚠ Needs Attention";       headline = `${greet}${abnormal} markers need attention.`; }
    else if (abnormal > 0) { sc = "moderate"; label = "↑ Some Items to Review";  headline = `${greet}${abnormal} marker${abnormal > 1 ? "s" : ""} outside normal range.`; }

    const worseTrends = trends.filter(t => t.concerning || t.pct_change >= 20);
    if (worseTrends.length && abnormal === 0) {
        sc = "moderate"; label = "↑ Trend to Watch";
        headline = `${greet}${worseTrends[0].marker} has moved ${worseTrends[0].pct_change}% since ${worseTrends[0].from_date}.`;
    }

    const alerts = feed.slice(0, 4).map(item => {
        const s   = item.severity === "high" ? "high" : item.severity === "medium" ? "medium" : item.severity === "none" ? "positive" : "low";
        const cid = _registerChip(item.cta || "Tell me more");
        return `<div class="pulse-alert-item ${s}" data-chip-id="${cid}" title="Ask PHI">
            <span class="alert-icon">${item.icon || "→"}</span>
            <div class="alert-body">
                <div class="alert-title">${escapeHtml(item.title || "")}</div>
                <div class="alert-desc">${escapeHtml(item.body || "")}</div>
            </div>
            <span class="alert-cta">Ask →</span>
        </div>`;
    }).join("");

    DOM.pulseContent.innerHTML = `
        <div class="pulse-card-header">
            <span class="pulse-status-pill ${sc}">${label}</span>
            <p class="pulse-headline">${escapeHtml(headline)}</p>
        </div>
        ${alerts ? `<div class="pulse-alerts">${alerts}</div>` : ""}
        <button class="pulse-view-more" id="pulseViewMoreBtn">
            <i class="fa-solid fa-chart-line"></i> View full health picture
        </button>`;

    DOM.pulseContent.querySelectorAll(".pulse-alert-item[data-chip-id]").forEach(el => {
        el.addEventListener("click", () => {
            const text = _chipTexts.get(parseInt(el.getAttribute("data-chip-id"), 10));
            if (text) sendChip(text);
        });
    });
    $id("pulseViewMoreBtn")?.addEventListener("click", openPulseModal);
}

function generateContextualChips(d) {
    const trends   = d.trends           || [];
    const abnormal = d.abnormal_count   || 0;
    const markers  = d.abnormal_markers || [];
    const chips    = [];

    if (markers.length) {
        const name = markers[0].marker_name || markers[0].name || "my top marker";
        chips.push({ icon: "fa-triangle-exclamation", text: `What does my ${name} result mean?` });
    }
    const top = trends.find(t => t.concerning || t.pct_change >= 15);
    if (top) chips.push({ icon: "fa-chart-line", text: `Why is my ${top.marker} ${top.direction}?` });

    chips.push({ icon: "fa-stethoscope", text: "Prepare me for my next doctor visit" });
    chips.push({
        icon: abnormal > 0 ? "fa-dumbbell" : "fa-heart-pulse",
        text: abnormal > 0 ? "What lifestyle changes will help most?" : "How do I maintain these healthy levels?",
    });
    setChips(chips);

    if (abnormal > 0 && markers[0]) {
        const n = markers[0].marker_name || "results";
        DOM.userInput.placeholder = currentUserName ? `Ask about your ${n} trend, ${currentUserName}…` : `Ask about your ${n} trend…`;
    } else {
        DOM.userInput.placeholder = currentUserName ? `Ask PHI about your health, ${currentUserName}…` : "Ask PHI about your health…";
    }
}

/* ── Chip registry ─────────────────────────────────────────────────────── */

function _registerChip(text) {
    const id = _chipCounter++;
    _chipTexts.set(id, text);
    return id;
}

function setChips(chips) {
    if (!DOM.welcomeChips) return;
    _chipTexts.clear();
    _chipCounter = 0;

    DOM.welcomeChips.innerHTML = chips.map(c => {
        const id = _registerChip(c.text);
        return `<button class="chip" data-chip-id="${id}">
            <i class="fa-solid ${c.icon}"></i> ${escapeHtml(c.text)}
        </button>`;
    }).join("");

    DOM.welcomeChips.querySelectorAll(".chip[data-chip-id]").forEach(btn => {
        btn.addEventListener("click", () => {
            const text = _chipTexts.get(parseInt(btn.getAttribute("data-chip-id"), 10));
            if (text) sendChip(text);
        });
    });
}

let _sendChipDebounce = null;
function sendChip(text) {
    if (isProcessing) return;
    clearTimeout(_sendChipDebounce);
    DOM.userInput.value = text;
    DOM.userInput.focus();
    _sendChipDebounce = setTimeout(handleSend, 60);
}
window.sendChip = sendChip;

/* ╔══════════════════════════════════════════════════════════════════════
   PULSE MODAL — Temporal Intelligence (#TEMPORAL)
══════════════════════════════════════════════════════════════════════╝ */

async function openPulseModal() {
    window.closeModals();
    DOM.pulseModal?.classList.remove("hidden");
    await renderPulseModalContent();
}
window.openPulseModal = openPulseModal;

async function renderPulseModalContent() {
    if (!DOM.pulseModalBody) return;

    DOM.pulseModalBody.innerHTML = `
        <div class="temporal-loading">
            <div class="pulse-spinner"></div>
            <span>Loading your health intelligence…</span>
        </div>`;

    try {
        const h = await getAuthHeaders();

        // Parallel fetch: markers, insights, persona, top observation card
        const [mr, ir, personaText] = await Promise.all([
            fetch(`${API_BASE}/api/health-markers`,  { headers: h }).then(safeJson),
            fetch(`${API_BASE}/api/health-insights`, { headers: h }).then(safeJson),
            loadPersona(false),
        ]);

        const mArr = Array.isArray(mr) ? mr : [];
        const iArr = Array.isArray(ir) ? ir : [];

        // Try to load one observation card based on the most concerning marker
        let observationCards = [];
        if (mArr.length) {
            const topMarker = mArr.find(m => m.status === "HIGH" || m.status === "LOW");
            const query     = topMarker ? `What pattern exists with my ${topMarker.marker_name}?` : "What patterns do you see in my health?";
            try {
                const cr = await fetch(`${API_BASE}/api/correlate`, {
                    method: "POST", headers: h,
                    body: JSON.stringify({ query, lookback_days: 90, max_cards: 2 }),
                });
                const cd = await safeJson(cr);
                if (cr.ok && Array.isArray(cd.observation_cards)) {
                    observationCards = cd.observation_cards.filter(c => c.confidence !== "insufficient");
                }
            } catch {}
        }

        if (!mArr.length && !personaText) {
            DOM.pulseModalBody.innerHTML = _emptyHealthState(
                "No health data yet",
                "Upload a lab report using the 📎 button and PHI will extract all your markers automatically.",
                "Upload a Report"
            );
            DOM.pulseModalBody.querySelector(".empty-cta-btn")?.addEventListener("click", () => {
                window.closeModals();
                DOM.fileInput?.click();
            });
            return;
        }

        DOM.pulseModalBody.innerHTML = _buildTemporalDashboardHTML(
            personaText, mArr, iArr, observationCards
        );

        // Wire "Generate Doctor Brief" button
        $id("pulseModalDoctorBtn")?.addEventListener("click", () => {
            window.closeModals();
            sendChip("Prepare me for my next doctor visit based on my full health picture");
        });

        // Wire observation card CTAs
        DOM.pulseModalBody.querySelectorAll(".obs-card-cta[data-query]").forEach(btn => {
            btn.addEventListener("click", () => {
                window.closeModals();
                sendChip(btn.getAttribute("data-query") || "Tell me more");
            });
        });

    } catch (err) {
        console.error("[PULSE MODAL]", err);
        DOM.pulseModalBody.innerHTML = `<div class="loading-text" style="color:var(--accent-warn)">Could not load health data.</div>`;
    }
}

/**
 * Build the three-section temporal dashboard HTML.
 * Section 1 — Your Story    (persona)
 * Section 2 — Current Snapshot (labs)
 * Section 3 — Active Insights (observation cards + AI insights)
 */
function _buildTemporalDashboardHTML(persona, markers, insights, obsCards) {
    /* ── Section 1: Your Story ── */
    const storySection = `
        <div class="temporal-section">
            <div class="temporal-section-header">
                <i class="fa-solid fa-brain temporal-icon"></i>
                <h4>Your Story</h4>
                <span class="temporal-badge">PHI synthesis</span>
            </div>
            ${_renderPersonaSection(persona)}
        </div>`;

    /* ── Section 2: Current Snapshot ── */
    const color = s => s === "HIGH" || s === "LOW" ? "var(--accent-warn)" : s === "NORMAL" ? "var(--accent-ok)" : "var(--text-muted)";
    const badge  = s => s === "HIGH" ? "⬆ HIGH" : s === "LOW" ? "⬇ LOW" : "✓ NORMAL";
    const badge_cls = s => s === "HIGH" || s === "LOW" ? "marker-badge abnormal" : "marker-badge normal";

    const snapshotRows = markers.map(m => `
        <div class="snapshot-row">
            <div class="snapshot-left">
                <span class="snapshot-name">${escapeHtml(m.marker_name)}</span>
                <span class="snapshot-ref">Ref: ${escapeHtml(m.reference_range || "—")} · ${escapeHtml(m.date || "")}</span>
            </div>
            <div class="snapshot-right">
                <span class="snapshot-value" style="color:${color(m.status)}">${m.value} <span class="snapshot-unit">${escapeHtml(m.unit || "")}</span></span>
                <span class="${badge_cls(m.status)}">${badge(m.status)}</span>
            </div>
        </div>`).join("");

    const snapshotContent = markers.length
        ? `<div class="snapshot-list">${snapshotRows}</div>
           <div style="margin-top:14px;text-align:center">
               <button id="pulseModalDoctorBtn" class="btn-doctor-brief" style="font-size:13px;padding:8px 18px">
                   <i class="fa-solid fa-stethoscope"></i> Generate Doctor Visit Prep
               </button>
           </div>`
        : _emptyHealthState("No lab results yet", "Upload your first PDF report to populate this section.", null);

    const snapshotSection = `
        <div class="temporal-section">
            <div class="temporal-section-header">
                <i class="fa-solid fa-flask temporal-icon"></i>
                <h4>Current Snapshot</h4>
                <span class="temporal-badge">${markers.length} markers · ${markers.filter(m => m.status === "HIGH" || m.status === "LOW").length} need attention</span>
            </div>
            ${snapshotContent}
        </div>`;

    /* ── Section 3: Active Insights ── */
    const aiInsightRows = insights.map(ins => {
        const borderColor = ins.severity === "high" ? "var(--accent-warn)" : ins.severity === "medium" ? "var(--accent-amber)" : "var(--accent-ok)";
        return `<div class="insight-row" style="border-left-color:${borderColor}">
            <div class="insight-headline">${escapeHtml(ins.headline || "")}</div>
            <div class="insight-detail">${escapeHtml(ins.detail || "")}</div>
        </div>`;
    }).join("");

    const obsCardRows = obsCards.map(card => `
        <div class="obs-card confidence-${escapeHtml(card.confidence || "limited")}">
            <div class="obs-card-title">${escapeHtml(card.title || "")}</div>
            <div class="obs-card-body">${escapeHtml(card.observation || "")}</div>
            <div class="obs-card-meta">
                <span class="obs-confidence-pill">${escapeHtml(card.confidence || "limited")} confidence</span>
                <span class="obs-datapoints">${card.data_points || 0} data points</span>
            </div>
            ${card.suggestion ? `<div class="obs-card-suggestion">💡 ${escapeHtml(card.suggestion)}</div>` : ""}
            <button class="obs-card-cta" data-query="Tell me more about the ${escapeHtml(card.metric_a || "marker")} pattern">
                Explore this pattern →
            </button>
            <p class="obs-disclaimer">⚕️ <em>${escapeHtml(card.disclaimer || "")}</em></p>
        </div>`).join("");

    const insightsContent = aiInsightRows || obsCardRows
        ? `<div class="insights-ai-block">${aiInsightRows}</div>${obsCardRows ? `<div class="obs-cards-block"><div class="obs-label"><i class="fa-solid fa-magnifying-glass-chart"></i> Behavioral Correlations</div>${obsCardRows}</div>` : ""}`
        : `<div class="temporal-empty-state"><i class="fa-solid fa-magnifying-glass-chart"></i><p>Log daily activity (steps, food, sleep) using the sidebar to unlock cross-domain pattern analysis.</p></div>`;

    const insightsSection = `
        <div class="temporal-section">
            <div class="temporal-section-header">
                <i class="fa-solid fa-lightbulb temporal-icon"></i>
                <h4>Active Insights</h4>
                <span class="temporal-badge">AI synthesis</span>
            </div>
            ${insightsContent}
        </div>`;

    return `<div class="temporal-dashboard">${storySection}${snapshotSection}${insightsSection}</div>`;
}

/* ── Empty state helper ────────────────────────────────────────────────── */
function _emptyHealthState(title, desc, btnLabel) {
    return `<div class="empty-health-state">
        <i class="fa-solid fa-file-medical"></i>
        <h4>${escapeHtml(title)}</h4>
        <p>${escapeHtml(desc)}</p>
        ${btnLabel ? `<button class="upload-nudge-btn empty-cta-btn" style="font-size:13px;padding:9px 18px">
            <i class="fa-solid fa-upload"></i> ${escapeHtml(btnLabel)}
        </button>` : ""}
    </div>`;
}

/* ╔══════════════════════════════════════════════════════════════════════
   CONVERSATIONS
══════════════════════════════════════════════════════════════════════╝ */

async function createConversation() {
    const h   = await getAuthHeaders();
    const res = await fetch(`${API_BASE}/conversation/create`, {
        method: "POST", headers: h, body: JSON.stringify({}),
    }).catch(() => null);
    if (!res) return null;
    const d = await safeJson(res);
    if (res.status === 403) { showToast("Consent required. Accept terms in Settings.", "error"); return null; }
    if (res.ok && d.conversation_id) {
        activeConvId = d.conversation_id;
        _prependConvToHistory(d.conversation_id, "New Chat");
    }
    return d.conversation_id || null;
}

function _prependConvToHistory(id, title) {
    if (!DOM.historyList) return;
    DOM.historyList.querySelector(".empty-state")?.remove();

    let todayGroup = DOM.historyList.querySelector(".history-group-today");
    if (!todayGroup) {
        todayGroup = document.createElement("div");
        todayGroup.className = "history-group-label history-group-today";
        todayGroup.textContent = "Today";
        DOM.historyList.prepend(todayGroup);
    }

    const item = document.createElement("div");
    item.className = "history-item active";
    item.setAttribute("data-id", id);
    item.innerHTML = `
        <span class="history-title" data-conv-id="${id}">${escapeHtml(title)}</span>
        <button class="delete-chat" title="Delete" data-del-id="${id}">
            <i class="fa-solid fa-trash"></i>
        </button>`;
    item.querySelector(".history-title").addEventListener("click", () => openConversation(id));
    item.querySelector(".delete-chat").addEventListener("click",  e => { e.stopPropagation(); showDeleteModal(id); });

    todayGroup.insertAdjacentElement("afterend", item);
    DOM.historyList.querySelectorAll(".history-item").forEach(el => {
        el.classList.toggle("active", el.getAttribute("data-id") === id);
    });
}

async function loadHistory() {
    const h = await getAuthHeaders();
    if (!h.Authorization) return;
    try {
        const res = await fetch(`${API_BASE}/history`, { method: "POST", headers: h });
        const d   = await safeJson(res);
        if (!res.ok || !Array.isArray(d)) {
            DOM.historyList.innerHTML = '<div class="empty-state">Could not load history.</div>';
            return;
        }
        renderHistory(d);
    } catch {
        DOM.historyList.innerHTML = '<div class="empty-state">Network error.</div>';
    }
}

function renderHistory(conversations) {
    if (!conversations.length) {
        DOM.historyList.innerHTML = '<div class="empty-state">No conversations yet.<br>Start by asking PHI something!</div>';
        return;
    }

    const today     = new Date(); today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);

    function _groupLabel(dateStr) {
        if (!dateStr) return "Older";
        const d = new Date(dateStr); d.setHours(0, 0, 0, 0);
        if (d.getTime() === today.getTime())     return "Today";
        if (d.getTime() === yesterday.getTime()) return "Yesterday";
        return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    }

    const groups = new Map();
    conversations.forEach(c => {
        const label = _groupLabel(c.created_at);
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(c);
    });

    let html = "";
    groups.forEach((convs, label) => {
        const isTodayClass = label === "Today" ? " history-group-today" : "";
        html += `<div class="history-group-label${isTodayClass}">${escapeHtml(label)}</div>`;
        convs.forEach(c => {
            const isActive = c.id === activeConvId;
            html += `<div class="history-item${isActive ? " active" : ""}" data-id="${escapeHtml(c.id)}">
                <span class="history-title" data-conv-id="${escapeHtml(c.id)}">${escapeHtml(c.title || "New Chat")}</span>
                <button class="delete-chat" title="Delete" data-del-id="${escapeHtml(c.id)}">
                    <i class="fa-solid fa-trash"></i>
                </button>
            </div>`;
        });
    });

    DOM.historyList.innerHTML = html;
    DOM.historyList.addEventListener("click", _historyClickHandler);
}

function _historyClickHandler(e) {
    const titleEl  = e.target.closest(".history-title[data-conv-id]");
    const deleteEl = e.target.closest(".delete-chat[data-del-id]");
    if (titleEl)       openConversation(titleEl.getAttribute("data-conv-id"));
    else if (deleteEl) { e.stopPropagation(); showDeleteModal(deleteEl.getAttribute("data-del-id")); }
}

async function openConversation(id) {
    if (isProcessing) { showToast("Please wait…", "error"); return; }
    setProcessing(true);
    activeConvId = id;
    uploadedFiles = [];
    updateFilePreview();
    _docCtx.clear();
    showChatMode();
    DOM.chatDisplay.innerHTML = "";

    DOM.historyList.querySelectorAll(".history-item").forEach(el => {
        el.classList.toggle("active", el.getAttribute("data-id") === id);
    });

    try {
        const h   = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/conversation`, {
            method: "POST", headers: h, body: JSON.stringify({ conversation_id: id }),
        });
        if (!res.ok) throw new Error("Load failed");
        const msgs = await safeJson(res);
        if (Array.isArray(msgs) && msgs.length) {
            DOM.chatDisplay.innerHTML = msgs.map(m => _msgHTML(m.content, m.role === "user" ? "user" : "ai")).join("");
            DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
        } else {
            DOM.chatDisplay.innerHTML = '<div class="empty-state">No messages yet.</div>';
        }
        closeSidebar();
    } catch {
        showToast("Failed to load conversation.", "error");
    } finally {
        setProcessing(false);
    }
}

async function renameConversation(id, title) {
    const h = await getAuthHeaders();
    const titleEl = DOM.historyList.querySelector(`.history-title[data-conv-id="${id}"]`);
    if (titleEl) titleEl.textContent = title.substring(0, 50);
    fetch(`${API_BASE}/rename`, {
        method: "POST", headers: h, body: JSON.stringify({ conversation_id: id, title }),
    }).catch(() => {});
}

/* ╔══════════════════════════════════════════════════════════════════════
   CHAT
══════════════════════════════════════════════════════════════════════╝ */

async function handleSend() {
    if (isProcessing) return;

    let text = DOM.userInput.value.trim();
    if (!text && uploadedFiles.length > 0) text = "Please read my uploaded medical report and explain every finding in plain language.";
    if (!text) return;

    setProcessing(true);

    if (!activeConvId) {
        const c = await createConversation();
        if (!c) { setProcessing(false); return; }
    }

    showChatMode();
    DOM.userInput.value = "";
    DOM.userInput.style.height = "auto";
    DOM.userInput.placeholder  = currentUserName ? `Ask PHI about your health, ${currentUserName}…` : "Ask PHI about your health…";

    let documentContents = [];
    if (uploadedFiles.length > 0) {
        const proc = appendMessage(`📎 Analyzing ${uploadedFiles.length} document(s)…`, "ai");
        documentContents = (await Promise.all(uploadedFiles.map(processFile))).filter(Boolean);
        proc.remove();

        if (documentContents[0]) {
            _docCtx.set(documentContents[0].document_text, documentContents[0].document_id, activeConvId);

            // #PERSONA-POLL — if server signals persona_refresh, re-fetch after short delay
            if (documentContents[0].persona_refresh) {
                setTimeout(async () => {
                    const fresh = await loadPersona(true);
                    if (fresh) console.log("[PHI] Persona refreshed after upload.");
                }, 750);
            }
        }

        uploadedFiles = [];
        updateFilePreview();
        _cache.del("dashboard");
        setTimeout(loadHealthPulse, 4000);
    }

    appendMessage(text, "user");
    const botRow = appendTyping();

    try {
        const h = await getAuthHeaders();
        const { text: docText, id: docId } = _docCtx.getForConv(activeConvId);
        const isFirstFollowUp = !documentContents.length && !!docText;
        const sendDocText     = documentContents[0]?.document_text || (isFirstFollowUp ? docText : "");
        if (isFirstFollowUp) _docCtx.clear();

        const res = await fetch(`${API_BASE}/chat`, {
            method: "POST", headers: h,
            body: JSON.stringify({
                conversation_id: activeConvId,
                message:         text,
                has_documents:   documentContents.length > 0 || !!docId,
                document_id:     documentContents[0]?.document_id || docId || "",
                document_text:   sendDocText,
            }),
        });
        const d = await safeJson(res);

        if (res.status === 403)       updateMessage(botRow, "⚠️ Consent required. Please accept terms in Settings.");
        else if (d.error && !d.reply) updateMessage(botRow, `Something went wrong: ${d.error}`);
        else                          updateMessage(botRow, d.reply || "I couldn't process that. Please try again.");
    } catch {
        updateMessage(botRow, "Connection error. Please check your network.");
        showToast("Network error.", "error");
    } finally {
        setProcessing(false);
    }

    const msgCount = DOM.chatDisplay.querySelectorAll(".chat-message").length;
    if (msgCount <= 2 && activeConvId) {
        const newTitle = documentContents.length ? `📄 ${documentContents[0].name}` : text.substring(0, 45);
        renameConversation(activeConvId, newTitle);
    }
}

/* ── Message rendering ─────────────────────────────────────────────────── */

function _msgHTML(text, role) {
    const initial = role === "user" ? (currentUser?.email?.[0]?.toUpperCase() || "U") : "φ";
    const avClass  = role === "user" ? "user-av" : "ai-av";
    const content  = role === "user" ? escapeHtml(text) : renderMarkdown(text);
    return role === "user"
        ? `<div class="chat-message user-msg"><div class="msg-bubble">${content}</div><div class="msg-avatar ${avClass}">${initial}</div></div>`
        : `<div class="chat-message bot-msg"><div class="msg-avatar ${avClass}">${initial}</div><div class="msg-bubble">${content}</div></div>`;
}

function appendMessage(text, role) {
    const wrap = document.createElement("div");
    wrap.className = `chat-message ${role === "user" ? "user-msg" : "bot-msg"}`;
    const av = document.createElement("div");
    av.className = `msg-avatar ${role === "user" ? "user-av" : "ai-av"}`;
    av.textContent = role === "user" ? (currentUser?.email?.[0]?.toUpperCase() || "U") : "φ";
    const bub = document.createElement("div");
    bub.className = "msg-bubble";
    bub.innerHTML = role === "user" ? escapeHtml(text) : renderMarkdown(text);
    if (role === "user") { wrap.appendChild(bub); wrap.appendChild(av); }
    else                 { wrap.appendChild(av);  wrap.appendChild(bub); }
    DOM.chatDisplay.appendChild(wrap);
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
    return wrap;
}

function appendTyping() {
    const wrap = document.createElement("div");
    wrap.className = "chat-message bot-msg";
    wrap.innerHTML = `<div class="msg-avatar ai-av">φ</div><div class="msg-bubble"><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>`;
    DOM.chatDisplay.appendChild(wrap);
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
    return wrap;
}

function updateMessage(wrap, text) {
    const b = wrap.querySelector(".msg-bubble");
    if (b) b.innerHTML = renderMarkdown(text);
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
}

function showChatMode() {
    if (DOM.welcomeScreen) {
        DOM.welcomeScreen.style.display = "none";
        DOM.welcomeScreen.classList.add("hidden-welcome");
    }
}
function showWelcomeMode() {
    if (DOM.welcomeScreen) {
        DOM.welcomeScreen.style.display = "";
        DOM.welcomeScreen.classList.remove("hidden-welcome");
    }
    if (_healthContext) renderPulseCard(_healthContext);
}

/* ── File upload ───────────────────────────────────────────────────────── */

async function processFile(file) {
    const form = new FormData();
    form.append("file", file);
    const s = await getSession();
    if (!s) return null;
    try {
        const res = await fetch(`${API_BASE}/analyze`, {
            method: "POST",
            headers: { "Authorization": "Bearer " + s.access_token },
            body: form,
        });
        const d = await safeJson(res);
        if (!res.ok || !d.success) { showToast(d.error || `Could not process ${file.name}`, "error"); return null; }
        showToast(`✓ ${file.name} analyzed (${d.abnormal_count || 0} findings)`);
        return {
            name:           file.name,
            summary:        d.summary_text   || "",
            document_id:    d.document_id    || "",
            document_text:  d.document_text  || "",
            markers:        d.markers        || [],
            abnormal:       d.abnormal_count || 0,
            report_date:    d.report_date    || _today_iso(),
            persona_refresh: !!d.persona_refresh,
        };
    } catch {
        showToast(`Upload failed for ${file.name}`, "error");
        return null;
    }
}

function updateFilePreview() {
    if (!DOM.filePreview) return;
    DOM.filePreview.innerHTML = "";
    if (!uploadedFiles.length) { DOM.filePreview.classList.remove("visible"); return; }
    DOM.filePreview.classList.add("visible");
    DOM.filePreview.innerHTML = uploadedFiles.map((f, i) => {
        const icon = f.name.toLowerCase().endsWith(".pdf") ? "fa-file-pdf" : "fa-file-lines";
        return `<div class="file-chip"><i class="fa-solid ${icon}"></i><span>${escapeHtml(f.name)}</span><button class="remove-file" data-idx="${i}"><i class="fa-solid fa-xmark"></i></button></div>`;
    }).join("");
    DOM.filePreview.querySelectorAll(".remove-file").forEach(btn => {
        btn.addEventListener("click", () => {
            uploadedFiles.splice(parseInt(btn.getAttribute("data-idx"), 10), 1);
            updateFilePreview();
        });
    });
    DOM.userInput.placeholder = `${uploadedFiles.length} file(s) attached — press Send or ask a question…`;
}

/* ╔══════════════════════════════════════════════════════════════════════
   #BEHAVIORAL-LOG — Log Activity Modal (Task 3 data feed)
══════════════════════════════════════════════════════════════════════╝ */

const _METRIC_CONFIG = {
    steps:  { label: "Steps",           unit: "steps",  placeholder: "e.g. 8500",  hint: "Total steps for the day. PHI correlates with glucose and HbA1c readings." },
    food:   { label: "Calories (kcal)", unit: "kcal",   placeholder: "e.g. 1800",  hint: "Approximate daily caloric intake. Used to correlate with cholesterol and blood sugar." },
    sleep:  { label: "Sleep (hours)",   unit: "hours",  placeholder: "e.g. 7.5",   hint: "Hours of sleep last night. Poor sleep is correlated with elevated cortisol and glucose." },
    stress: { label: "Stress (1–10)",   unit: "1-10",   placeholder: "e.g. 6",     hint: "Subjective stress level 1 (calm) to 10 (extremely stressed). Linked to blood pressure." },
    weight: { label: "Weight",          unit: "lbs",    placeholder: "e.g. 172",   hint: "Morning body weight. PHI tracks trend over time and correlates with metabolic markers." },
};

function openLogActivity() {
    window.closeModals();
    DOM.logActivityModal?.classList.remove("hidden");

    // Default date to today
    const dateEl = $id("log-date");
    if (dateEl) dateEl.value = _today_iso();

    // Hide success banner
    const successEl = $id("log-success-msg");
    if (successEl) successEl.style.display = "none";

    // Wire metric tabs (remove any stale listeners by replacing with clones)
    document.querySelectorAll(".metric-tab").forEach(tab => {
        const fresh = tab.cloneNode(true);
        tab.parentNode.replaceChild(fresh, tab);
        fresh.addEventListener("click", () => {
            document.querySelectorAll(".metric-tab").forEach(t => t.classList.remove("active"));
            fresh.classList.add("active");
            _updateLogFormForMetric(fresh.getAttribute("data-metric"));
        });
    });

    // Wire submit button (replace to remove stale listeners)
    const oldSubmit = $id("submitLogBtn");
    if (oldSubmit) {
        const fresh = oldSubmit.cloneNode(true);
        oldSubmit.parentNode.replaceChild(fresh, oldSubmit);
        fresh.addEventListener("click", submitBehavioralLog);
    }

    _updateLogFormForMetric("steps");
    _loadRecentLogs();
}

function _updateLogFormForMetric(metric) {
    const cfg = _METRIC_CONFIG[metric] || _METRIC_CONFIG.steps;
    const labelEl = $id("log-value-label");
    const unitEl  = $id("log-unit");
    const valEl   = $id("log-value");
    const hintEl  = $id("metric-hint");

    if (labelEl) labelEl.textContent = cfg.label;
    if (unitEl)  unitEl.value        = cfg.unit;
    if (valEl) {
        valEl.placeholder = cfg.placeholder;
        valEl.value = "";
    }
    if (hintEl) hintEl.innerHTML = `<i class="fa-solid fa-lightbulb" style="color:var(--brand)"></i>&nbsp; ${escapeHtml(cfg.hint)}`;
}

async function submitBehavioralLog() {
    const btn      = $id("submitLogBtn");
    const date     = $id("log-date")?.value?.trim();
    const rawValue = $id("log-value")?.value?.trim();
    const unit     = $id("log-unit")?.value?.trim();
    const notes    = $id("log-notes")?.value?.trim() || "";
    const activeTab = document.querySelector(".metric-tab.active");
    const metric   = activeTab?.getAttribute("data-metric") || "steps";

    // Validation
    if (!date) {
        showToast("Please select a date.", "error"); return;
    }
    const value = parseFloat(rawValue);
    if (!rawValue || isNaN(value)) {
        showToast("Please enter a valid number.", "error"); return;
    }
    if (metric === "stress" && (value < 1 || value > 10)) {
        showToast("Stress must be 1–10.", "error"); return;
    }
    if ((metric === "steps" || metric === "food" || metric === "weight") && value < 0) {
        showToast("Value must be positive.", "error"); return;
    }

    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving…'; }

    try {
        const h = await getAuthHeaders();
        if (!h.Authorization) { showToast("Please sign in again.", "error"); return; }

        const res = await fetch(`${API_BASE}/api/behavioral-logs`, {
            method: "POST",
            headers: h,
            body: JSON.stringify({
                date,
                metric_name: metric,
                value,
                unit: unit || _METRIC_CONFIG[metric]?.unit || "units",
                notes,
            }),
        });
        const d = await safeJson(res);

        if (!res.ok || d.error) {
            showToast(d.error || "Could not save. Try again.", "error");
        } else {
            // Show inline success banner
            const successEl   = $id("log-success-msg");
            const successText = $id("log-success-text");
            if (successEl && successText) {
                successText.textContent = `${_METRIC_CONFIG[metric]?.label || metric} logged for ${date} ✓`;
                successEl.style.display = "flex";
                setTimeout(() => { successEl.style.display = "none"; }, 3500);
            }
            // Clear value + notes
            const valEl   = $id("log-value");
            const notesEl = $id("log-notes");
            if (valEl)   valEl.value   = "";
            if (notesEl) notesEl.value = "";

            showToast(`✓ ${_METRIC_CONFIG[metric]?.label || metric} logged`);
            _loadRecentLogs();
        }
    } catch {
        showToast("Network error. Please try again.", "error");
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-check"></i> Save Entry'; }
    }
}

async function _loadRecentLogs() {
    const preview = $id("recent-logs-preview");
    const list    = $id("recent-logs-list");
    if (!preview || !list) return;

    try {
        const h   = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/api/behavioral-logs?days=7`, { headers: h });
        const d   = await safeJson(res);
        if (!res.ok || !Array.isArray(d) || !d.length) { preview.style.display = "none"; return; }

        preview.style.display = "";
        list.innerHTML = d.slice(0, 6).map(r =>
            `<div class="recent-log-row">
                <span class="recent-log-meta">${escapeHtml(r.date || "")} · ${escapeHtml(r.metric_name || "")}</span>
                <span class="recent-log-value">${r.value} <span class="recent-log-unit">${escapeHtml(r.unit || "")}</span></span>
            </div>`
        ).join("");
    } catch {
        preview.style.display = "none";
    }
}

/* ╔══════════════════════════════════════════════════════════════════════
   #ADVOCACY — Advocacy Guide Modal (Task 2)
══════════════════════════════════════════════════════════════════════╝ */

function openAdvocacyGuide() {
    window.closeModals();
    DOM.advocacyGuideModal?.classList.remove("hidden");

    // Replace the button to remove any stale listeners
    const oldBtn = $id("startAdvocacyBtn");
    if (oldBtn) {
        const fresh = oldBtn.cloneNode(true);
        oldBtn.parentNode.replaceChild(fresh, oldBtn);
        fresh.addEventListener("click", async () => {
            window.closeModals();
            const query = "Generate a GLP-1 prior authorization support brief based on my full health record.";
            if (!activeConvId) {
                const c = await createConversation();
                if (!c) return;
            }
            showChatMode();
            DOM.userInput.value = query;
            handleSend();
        });
    }
}

/* ╔══════════════════════════════════════════════════════════════════════
   SIDEBAR & MODALS
══════════════════════════════════════════════════════════════════════╝ */

function openSidebar()  { DOM.sidebar.classList.add("open");    DOM.overlay.classList.add("active"); }
function closeSidebar() { DOM.sidebar.classList.remove("open"); DOM.overlay.classList.remove("active"); }

window.closeModals = () => {
    [DOM.profileModal, DOM.settingsModal, DOM.deleteModal,
     DOM.pulseModal, DOM.logActivityModal, DOM.advocacyGuideModal].forEach(m => m?.classList.add("hidden"));
};

[DOM.profileModal, DOM.settingsModal, DOM.deleteModal,
 DOM.pulseModal, DOM.logActivityModal, DOM.advocacyGuideModal].forEach(m => {
    m?.addEventListener("click", e => { if (e.target === m) window.closeModals(); });
});

function showDeleteModal(id) { conversationToDelete = id; DOM.deleteModal?.classList.remove("hidden"); }
window.showDeleteModal = showDeleteModal;

/* ╔══════════════════════════════════════════════════════════════════════
   PROFILE MODAL — Temporal dashboard inline
══════════════════════════════════════════════════════════════════════╝ */

async function loadProfileStats(user) {
    if (DOM.accountCreated && user?.created_at) {
        DOM.accountCreated.textContent = new Date(user.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    }
    const h = await getAuthHeaders();
    const [histRes, dashRes, profileRes] = await Promise.all([
        fetch(`${API_BASE}/history`,       { method: "POST", headers: h }).catch(() => null),
        fetch(`${API_BASE}/api/dashboard`, { headers: h }).catch(() => null),
        fetch(`${API_BASE}/api/profile`,   { headers: h }).catch(() => null),
    ]);

    if (histRes) {
        const convs = await safeJson(histRes);
        if (DOM.totalConvs) DOM.totalConvs.textContent = Array.isArray(convs) ? convs.length : 0;
    }
    if (dashRes?.ok) {
        const dash = await safeJson(dashRes);
        if (DOM.docsAnalyzed && !dash.error) DOM.docsAnalyzed.textContent = dash.document_count || 0;
    }
    if (profileRes?.ok) {
        const profile = await safeJson(profileRes);
        if (!profile.error) _renderProfileDemographics(profile);
    }
}

function _renderProfileDemographics(profile) {
    let sec = $id("profile-demographics");
    if (!sec) {
        const hr = document.querySelector("#profile-modal hr");
        if (!hr) return;
        sec = document.createElement("div");
        sec.id = "profile-demographics";
        hr.parentNode.insertBefore(sec, hr.nextSibling);
    }
    const rows = [];
    if (profile.first_name || profile.last_name) {
        const name = [profile.first_name, profile.last_name].filter(Boolean).join(" ");
        rows.push(`<div class="setting-item"><div class="setting-info"><h4><i class="fa-solid fa-user"></i> Name</h4><p>${escapeHtml(name)}</p></div></div>`);
    }
    if (profile.age)    rows.push(`<div class="setting-item"><div class="setting-info"><h4><i class="fa-solid fa-cake-candles"></i> Age</h4><p>${escapeHtml(String(profile.age))} years old</p></div></div>`);
    if (profile.gender) rows.push(`<div class="setting-item"><div class="setting-info"><h4><i class="fa-solid fa-person"></i> Gender</h4><p>${escapeHtml(String(profile.gender).charAt(0).toUpperCase() + String(profile.gender).slice(1))}</p></div></div>`);
    if (profile.plan)   rows.push(`<div class="setting-item"><div class="setting-info"><h4><i class="fa-solid fa-star"></i> Plan</h4><p>${profile.plan === "pro" ? "✦ PHI Pro" : "PHI Free"}</p></div></div>`);

    if (rows.length) {
        sec.innerHTML = `
            <div class="profile-section-label"><i class="fa-solid fa-brain" style="color:var(--brand)"></i> What PHI knows about you</div>
            ${rows.join("")}
            <p style="font-size:11.5px;color:var(--text-muted);margin-top:6px;margin-bottom:4px">PHI uses your age and gender to apply clinically appropriate reference ranges.</p>
            <hr>`;
    }
}

async function loadHealthDashboard() {
    if (!DOM.healthDash) return;
    DOM.healthDash.innerHTML = '<div class="loading-text"><i class="fa-solid fa-spinner fa-spin"></i> Loading…</div>';

    const h = await getAuthHeaders();
    const [mr, ir] = await Promise.all([
        fetch(`${API_BASE}/api/health-markers`,  { headers: h }).catch(() => null),
        fetch(`${API_BASE}/api/health-insights`, { headers: h }).catch(() => null),
    ]);
    const markers  = mr?.ok ? await safeJson(mr) : [];
    const insights = ir?.ok ? await safeJson(ir) : [];

    // #EMPTY-STATES
    if (!Array.isArray(markers) || !markers.length) {
        DOM.healthDash.innerHTML = _emptyHealthState(
            "No lab reports yet",
            "Upload a PDF lab report using the 📎 button. PHI will extract all your markers, explain what they mean, and track changes over time.",
            "Upload My First Report"
        );
        DOM.healthDash.querySelector(".empty-cta-btn")?.addEventListener("click", () => {
            window.closeModals();
            DOM.fileInput?.click();
        });
        return;
    }
    renderHealthDashboard(markers, Array.isArray(insights) ? insights : []);
}

function renderHealthDashboard(markers, insights) {
    const color = s => s === "HIGH" || s === "LOW" ? "var(--accent-warn)" : s === "NORMAL" ? "var(--accent-ok)" : "var(--text-muted)";
    const cards = markers.map(m =>
        `<div class="health-snap-card">
            <div class="snap-name">${escapeHtml(m.marker_name)}</div>
            <div class="snap-value" style="color:${color(m.status)}">${m.value} <span class="snap-unit">${escapeHtml(m.unit || "")}</span></div>
            <div class="snap-date">${escapeHtml(m.date || "")}</div>
        </div>`
    ).join("");

    const insH = insights.map(ins =>
        `<div class="insight-row" style="border-left-color:${ins.severity === "high" ? "var(--accent-warn)" : ins.severity === "medium" ? "var(--accent-amber)" : "var(--accent-ok)"}">
            <div class="insight-headline">${escapeHtml(ins.headline || "")}</div>
            <div class="insight-detail">${escapeHtml(ins.detail || "")}</div>
        </div>`
    ).join("");

    DOM.healthDash.innerHTML =
        (cards ? `<div class="health-snap-grid">${cards}</div>` : "") +
        (insH  ? `<div class="insight-section-label"><i class="fa-solid fa-lightbulb" style="color:var(--accent-amber)"></i> PHI Insights</div>${insH}` : "");

    DOM.doctorBriefBtn?.addEventListener("click", showDoctorBriefModal, { once: true });
}

function showDoctorBriefModal() {
    $id("doctor-brief-modal")?.remove();
    const m = document.createElement("div");
    m.id = "doctor-brief-modal"; m.className = "modal"; m.style.zIndex = "10001";
    m.innerHTML = `<div class="modal-box">
        <div class="modal-header">
            <h3><i class="fa-solid fa-stethoscope"></i> Doctor Visit Prep</h3>
            <button class="close-btn" id="briefCloseBtn"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div style="padding:4px 0 16px">
            <p style="font-size:13px;color:var(--text-muted);margin-bottom:14px">Personalised brief based on your health memory.</p>
            <label class="form-label-sm">Symptoms</label>
            <input type="text" id="brief-symptoms" placeholder="fatigue, dizziness" class="log-input" style="margin-bottom:10px">
            <label class="form-label-sm">Medications</label>
            <input type="text" id="brief-meds" placeholder="Metformin 500mg, Vitamin D" class="log-input" style="margin-bottom:10px">
            <div id="brief-output" style="display:none;margin-top:12px;background:var(--bg-hover);border-radius:8px;padding:13px;font-size:13px;line-height:1.65;max-height:260px;overflow-y:auto"></div>
        </div>
        <div class="modal-actions">
            <button class="btn-cancel" id="briefCancelBtn">Cancel</button>
            <button id="genBriefBtn" style="padding:8px 16px;background:var(--brand);border:none;border-radius:8px;color:white;font-size:13.5px;font-weight:600;font-family:var(--font);cursor:pointer">
                <i class="fa-solid fa-wand-magic-sparkles"></i> Generate
            </button>
        </div>
    </div>`;
    document.body.appendChild(m);

    $id("briefCloseBtn").addEventListener("click",  () => m.remove());
    $id("briefCancelBtn").addEventListener("click", () => m.remove());
    m.addEventListener("click", e => { if (e.target === m) m.remove(); });

    $id("genBriefBtn").addEventListener("click", async () => {
        const btn = $id("genBriefBtn");
        btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
        try {
            const h = await getAuthHeaders();
            const symptoms    = ($id("brief-symptoms")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
            const medications = ($id("brief-meds")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
            const res = await fetch(`${API_BASE}/api/doctor-brief`, {
                method: "POST", headers: h, body: JSON.stringify({ symptoms, medications }),
            });
            const d = await safeJson(res);
            const out = $id("brief-output");
            if (out) { out.style.display = "block"; out.innerHTML = renderMarkdown(d.brief || "Could not generate. Try again."); }
        } catch { showToast("Failed to generate brief.", "error"); }
        btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Generate';
    });
}

async function loadPlanStatus() {
    const h = await getAuthHeaders();
    if (!h.Authorization) return;
    const res = await fetch(`${API_BASE}/api/payment/status`, { headers: h }).catch(() => null);
    if (!res?.ok) return;
    const d = await safeJson(res);
    if (DOM.dropdownPlan) {
        DOM.dropdownPlan.textContent = d.is_pro ? "✦ PHI Pro" : "PHI Free";
        if (d.is_pro) DOM.dropdownPlan.style.color = "var(--brand)";
    }
}

/* ── Voice input ───────────────────────────────────────────────────────── */

function initVoiceInput() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { if (DOM.micBtn) { DOM.micBtn.disabled = true; DOM.micBtn.style.opacity = "0.3"; } return; }

    let listening = false, rec = null;
    DOM.micBtn?.addEventListener("click", () => {
        if (listening) { rec?.stop(); return; }
        rec = new SR(); rec.lang = "en-US"; rec.interimResults = false; rec.maxAlternatives = 1;
        rec.onstart  = () => { listening = true; DOM.micBtn.classList.add("listening"); showToast("Listening…"); };
        rec.onresult = e => { DOM.userInput.value = e.results[0][0].transcript; autoGrow(DOM.userInput); DOM.userInput.focus(); };
        rec.onerror  = e => {
            const msgs = { "no-speech": "No speech.", "not-allowed": "Mic blocked.", "network": "Network error." };
            showToast(msgs[e.error] || `Error: ${e.error}`, "error");
        };
        rec.onend = () => { listening = false; DOM.micBtn.classList.remove("listening"); };
        try { rec.start(); } catch { showToast("Voice failed.", "error"); }
    });
}

/* ── Settings ──────────────────────────────────────────────────────────── */

function loadUserPreferences() {
    const p    = JSON.parse(localStorage.getItem("phi_prefs") || "{}");
    const dark = p.theme === "dark";
    if (dark) document.body.classList.add("dark-mode");
    if (DOM.themeToggle) DOM.themeToggle.checked = dark;
    const fs = p.fontSize || 15;
    document.documentElement.style.setProperty("--chat-font-size", fs + "px");
    if (DOM.fontSizeInput) DOM.fontSizeInput.value = fs;
    if (DOM.fontSizeValue) DOM.fontSizeValue.textContent = fs + "px";
}

function savePref(k, v) {
    const p = JSON.parse(localStorage.getItem("phi_prefs") || "{}");
    p[k] = v;
    localStorage.setItem("phi_prefs", JSON.stringify(p));
}

function initSettings() {
    DOM.themeToggle?.addEventListener("change", e => {
        document.body.classList.toggle("dark-mode", e.target.checked);
        savePref("theme", e.target.checked ? "dark" : "light");
        showToast(e.target.checked ? "Dark mode on" : "Light mode on");
    });
    DOM.fontSizeInput?.addEventListener("input", e => {
        document.documentElement.style.setProperty("--chat-font-size", e.target.value + "px");
        if (DOM.fontSizeValue) DOM.fontSizeValue.textContent = e.target.value + "px";
        savePref("fontSize", +e.target.value);
    });
    DOM.exportChatBtn?.addEventListener("click", async () => {
        if (!activeConvId) { showToast("No active conversation.", "error"); return; }
        const h = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/conversation`, {
            method: "POST", headers: h, body: JSON.stringify({ conversation_id: activeConvId }),
        });
        const d = await safeJson(res);
        if (!Array.isArray(d)) { showToast("Could not export.", "error"); return; }
        let out = "Curabook PHI Chat Export\n" + "=".repeat(40) + "\n\n";
        d.forEach(m => { out += `${m.role.toUpperCase()}:\n${m.content}\n\n`; });
        Object.assign(document.createElement("a"), {
            href: URL.createObjectURL(new Blob([out], { type: "text/plain" })),
            download: `phi-chat-${Date.now()}.txt`,
        }).click();
        showToast("Chat exported");
    });
    DOM.clearHistBtn?.addEventListener("click", async () => {
        if (!confirm("⚠️ Delete ALL conversations?")) return;
        const h = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/history`, { method: "POST", headers: h });
        const convs = await safeJson(res);
        if (Array.isArray(convs)) {
            await Promise.all(convs.map(c => fetch(`${API_BASE}/delete`, {
                method: "POST", headers: h, body: JSON.stringify({ conversation_id: c.id }),
            }).catch(() => {})));
        }
        DOM.chatDisplay.innerHTML = "";
        activeConvId = null; uploadedFiles = []; _docCtx.clear(); _cache.clear();
        updateFilePreview(); showWelcomeMode(); loadHistory(); window.closeModals();
        showToast("All conversations deleted");
    });
    DOM.exportDataBtn?.addEventListener("click", async () => {
        const h = await getAuthHeaders();
        try {
            const res = await fetch(`${API_BASE}/export-data`, { method: "POST", headers: h });
            const d   = await safeJson(res);
            Object.assign(document.createElement("a"), {
                href: URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" })),
                download: `phi-data-${Date.now()}.json`,
            }).click();
            showToast("✅ Data exported");
        } catch { showToast("Export failed.", "error"); }
    });
    DOM.deleteAccBtn?.addEventListener("click", async () => {
        if (!confirm("⚠️ PERMANENTLY DELETE YOUR ACCOUNT?\n\nAll health data will be erased. Cannot be undone.")) return;
        if (prompt('Type "DELETE" to confirm:') !== "DELETE") return;
        const h = await getAuthHeaders();
        try { await supabaseClient.auth.signOut(); } catch {}
        try {
            const res = await fetch(`${API_BASE}/delete-account`, { method: "POST", headers: h });
            const d   = await safeJson(res);
            showToast(d.success ? "Account deleted." : "Signed out. Contact support if data persists.", d.success ? "success" : "error");
        } catch { showToast("Error. Contact support@curabook.com.", "error"); }
        setTimeout(() => window.location.href = "/login", 1500);
    });
}

/* ── Confirm delete conversation ─────────────────────────────────────── */

DOM.confirmDeleteBtn?.addEventListener("click", async () => {
    if (!conversationToDelete) return;
    const isActive = activeConvId === conversationToDelete;
    DOM.historyList.querySelector(`.history-item[data-id="${conversationToDelete}"]`)?.remove();
    if (isActive) {
        DOM.chatDisplay.innerHTML = "";
        activeConvId = null; uploadedFiles = []; _docCtx.clear();
        updateFilePreview(); showWelcomeMode();
    }
    window.closeModals();
    const idToDelete = conversationToDelete; conversationToDelete = null;
    showToast("Conversation deleted");
    const h = await getAuthHeaders();
    fetch(`${API_BASE}/delete`, { method: "POST", headers: h, body: JSON.stringify({ conversation_id: idToDelete }) }).catch(() => {});
    // Remove orphan group labels
    DOM.historyList.querySelectorAll(".history-group-label").forEach(label => {
        const next = label.nextElementSibling;
        if (!next || next.classList.contains("history-group-label")) label.remove();
    });
});

/* ── Group label style injection ────────────────────────────────────── */
(function injectStyles() {
    const style = document.createElement("style");
    style.textContent = `
        /* ── History group labels ────────────────────────────── */
        .history-group-label {
            font-size: 10px; font-weight: 700; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.9px;
            padding: 12px 10px 4px; user-select: none;
        }
        .history-group-label:first-child { padding-top: 6px; }

        /* ── Temporal Dashboard ───────────────────────────────── */
        .temporal-dashboard { display: flex; flex-direction: column; gap: 20px; }
        .temporal-section { background: var(--bg-body); border-radius: 12px; padding: 16px 18px; }
        .temporal-section-header { display: flex; align-items: center; gap: 9px; margin-bottom: 13px; }
        .temporal-section-header h4 { font-size: 13.5px; font-weight: 700; flex: 1; }
        .temporal-icon { color: var(--brand); font-size: 14px; }
        .temporal-badge { font-size: 10.5px; font-weight: 600; background: var(--brand-dim); color: var(--brand); padding: 2px 9px; border-radius: 20px; }
        .temporal-empty-state { text-align: center; padding: 20px 12px; color: var(--text-muted); font-size: 12.5px; line-height: 1.6; }
        .temporal-empty-state i { font-size: 1.5rem; color: var(--brand); opacity: .5; margin-bottom: 8px; display: block; }
        .temporal-loading { display: flex; align-items: center; gap: 12px; padding: 20px; color: var(--text-muted); font-size: 13.5px; }

        /* Persona block */
        .persona-block { background: var(--bg-card); border-radius: 8px; padding: 13px 15px; }
        .persona-text { font-size: 13px; color: var(--text-main); line-height: 1.75; margin-bottom: 8px; }
        .persona-disclaimer { font-size: 11px; color: var(--text-muted); font-style: italic; }

        /* Snapshot */
        .snapshot-list { display: flex; flex-direction: column; gap: 0; }
        .snapshot-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); }
        .snapshot-row:last-child { border-bottom: none; }
        .snapshot-left { display: flex; flex-direction: column; gap: 2px; }
        .snapshot-name { font-weight: 500; font-size: 13px; color: var(--text-main); }
        .snapshot-ref { font-size: 11px; color: var(--text-muted); }
        .snapshot-right { display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }
        .snapshot-value { font-weight: 700; font-size: 14px; }
        .snapshot-unit { font-size: 11px; font-weight: 400; }
        .marker-badge { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 10px; }
        .marker-badge.abnormal { background: rgba(255,107,107,.12); color: var(--accent-warn); }
        .marker-badge.normal   { background: rgba(74,222,128,.1);  color: var(--accent-ok); }

        /* Insights */
        .insight-row { border-left: 3px solid var(--brand); padding: 8px 11px; margin-bottom: 7px; background: var(--bg-hover); border-radius: 0 8px 8px 0; }
        .insight-headline { font-weight: 600; font-size: 13px; color: var(--text-main); }
        .insight-detail { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
        .insight-section-label { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .8px; margin: 10px 0 6px; }

        /* Observation cards */
        .obs-cards-block { margin-top: 12px; }
        .obs-label { font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
        .obs-card { background: var(--bg-card); border-radius: 10px; padding: 13px 15px; margin-bottom: 10px; border: 1px solid var(--border); }
        .obs-card.confidence-strong   { border-left: 3px solid var(--accent-ok); }
        .obs-card.confidence-moderate { border-left: 3px solid var(--accent-amber); }
        .obs-card.confidence-limited  { border-left: 3px solid var(--text-muted); }
        .obs-card-title { font-weight: 700; font-size: 13px; margin-bottom: 6px; color: var(--text-main); }
        .obs-card-body  { font-size: 12.5px; color: var(--text-muted); line-height: 1.6; margin-bottom: 8px; }
        .obs-card-meta  { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; }
        .obs-confidence-pill { font-size: 10.5px; font-weight: 700; background: var(--brand-dim); color: var(--brand); padding: 2px 8px; border-radius: 10px; }
        .obs-datapoints { font-size: 11px; color: var(--text-muted); }
        .obs-card-suggestion { font-size: 12px; color: var(--text-main); background: var(--bg-body); padding: 7px 10px; border-radius: 7px; margin-bottom: 8px; }
        .obs-card-cta { background: none; border: 1px solid var(--border); border-radius: 6px; padding: 5px 12px; font-size: 12px; color: var(--brand); cursor: pointer; font-family: var(--font); transition: all .15s; }
        .obs-card-cta:hover { background: var(--brand-dim); border-color: var(--brand); }
        .obs-disclaimer { font-size: 10.5px; color: var(--text-muted); font-style: italic; margin-top: 7px; }

        /* Profile */
        .profile-section-label { font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .8px; padding: 14px 0 8px; }
        .health-snap-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
        .health-snap-card { background: var(--bg-hover); border-radius: 8px; padding: 9px 12px; min-width: 90px; flex: 1; }
        .snap-name  { font-size: 10.5px; color: var(--text-muted); margin-bottom: 3px; }
        .snap-value { font-size: 1rem; font-weight: 700; }
        .snap-unit  { font-size: 10px; font-weight: 400; }
        .snap-date  { font-size: 9.5px; color: var(--text-muted); margin-top: 2px; }

        /* Log modal */
        .log-input { width: 100%; padding: 9px 11px; border: 1.5px solid var(--border); border-radius: 8px; font-size: 14px; font-family: var(--font); color: var(--text-main); background: var(--bg-input); outline: none; }
        .log-input:focus { border-color: var(--brand); }
        .form-label-sm { display: block; font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px; }
        .recent-log-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
        .recent-log-row:last-child { border-bottom: none; }
        .recent-log-meta { color: var(--text-muted); }
        .recent-log-value { font-weight: 600; }
        .recent-log-unit { font-weight: 400; opacity: .7; }

        /* PHI disclaimer in chat */
        .phi-disclaimer { font-size: 11px !important; color: var(--text-muted) !important; margin-top: 10px !important; font-style: italic; }

        /* 1440px desktop optimisation */
        @media (min-width: 1440px) {
            :root { --sidebar-width: 290px; }
            .chat-display { padding: 16px 0 8px; }
            .chat-message { max-width: 860px; padding: 10px 32px; }
            .input-bar-wrapper { max-width: 860px; padding: 6px 32px 18px; }
            .welcome-screen { padding: 28px 32px 0; }
            .pulse-card { max-width: 760px; }
            .suggestion-chips { max-width: 760px; }
            .modal-box { max-width: 560px; }
            .modal-box-wide { max-width: 700px; }
            .temporal-section { padding: 18px 22px; }
        }

        body.dark-mode .temporal-section { background: #0a0a0f; }
        body.dark-mode .persona-block { background: #13131a; }
        body.dark-mode .obs-card { background: #13131a; border-color: rgba(255,255,255,.07); }
    `;
    document.head.appendChild(style);
})();

/* ╔══════════════════════════════════════════════════════════════════════
   EVENT WIRING
══════════════════════════════════════════════════════════════════════╝ */

function wireEvents() {
    DOM.mobileMenu?.addEventListener("click",     openSidebar);
    DOM.closeSidebar?.addEventListener("click",   closeSidebar);
    DOM.overlay?.addEventListener("click",        closeSidebar);

    DOM.newChatBtn?.addEventListener("click", () => {
        if (hasChatMessages() && !confirm("Start a new chat?")) return;
        activeConvId = null; uploadedFiles = []; _docCtx.clear();
        DOM.chatDisplay.innerHTML = "";
        updateFilePreview(); showWelcomeMode();
        DOM.historyList.querySelectorAll(".history-item").forEach(el => el.classList.remove("active"));
        DOM.userInput.focus();
    });

    DOM.sendBtn?.addEventListener("click",    e => { e.preventDefault(); handleSend(); });
    DOM.userInput?.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } });
    DOM.userInput?.addEventListener("input",   () => autoGrow(DOM.userInput));
    DOM.attachBtn?.addEventListener("click",   () => DOM.fileInput?.click());
    DOM.btnUploadNav?.addEventListener("click",  () => { closeSidebar(); DOM.fileInput?.click(); });
    DOM.uploadNudgeBtn?.addEventListener("click", () => { closeSidebar(); DOM.fileInput?.click(); });
    DOM.btnHealthPulse?.addEventListener("click", () => { closeSidebar(); openPulseModal(); });
    DOM.btnLogActivity?.addEventListener("click", () => { closeSidebar(); openLogActivity(); });
    DOM.advocacyInfoBtn?.addEventListener("click", openAdvocacyGuide);

    DOM.fileInput?.addEventListener("change", e => {
        Array.from(e.target.files || []).forEach(f => {
            if (f.size > 10 * 1024 * 1024) { showToast(`${f.name} too large. Max 10MB.`, "error"); return; }
            if (!/\.(pdf|txt)$/i.test(f.name)) { showToast(`${f.name}: PDF or TXT only.`, "error"); return; }
            uploadedFiles.push(f);
        });
        updateFilePreview();
        DOM.fileInput.value = "";
        if (uploadedFiles.length) { showToast(`🔒 ${uploadedFiles.length} file(s) ready`); DOM.userInput.focus(); }
    });

    DOM.profileBtn?.addEventListener("click", e => {
        e.stopPropagation();
        DOM.profileDrop?.classList.toggle("hidden");
    });
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

/* ╔══════════════════════════════════════════════════════════════════════
   BOOT
   #OAUTH-FIX: initSupabase() wires onAuthStateChange FIRST, then we
   check for an existing session. If OAuth just completed, the state
   change fires automatically before the getSession check runs.
══════════════════════════════════════════════════════════════════════╝ */

document.addEventListener("DOMContentLoaded", async () => {
    try {
        // Step 1: init Supabase + wire auth listener (catches OAuth hash event)
        initSupabase();

        // Step 2: wire UI events (non-auth)
        wireEvents();
        initSettings();
        initVoiceInput();
        loadUserPreferences();

        // Step 3: check for existing session (covers normal page loads)
        const session = await getSession();
        if (!session?.user) {
            window.location.href = "/login";
            return;
        }

        // Step 4: login flow (only if onAuthStateChange didn't already run it)
        if (!currentUser) {
            const user = session.user;
            if (user.app_metadata?.provider === "google" && !localStorage.getItem(`phi_terms_${user.id}`)) {
                window.location.href = "/login";
                return;
            }
            await handleLoginSuccess(user);
        }

    } catch (err) {
        console.error("[PHI BOOT]", err);
        showToast("Could not connect to PHI. Please refresh.", "error");
    }
});