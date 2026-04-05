/**
 * script.js — Curabook PHI Chat-First Frontend
 *
 * Responsibilities:
 *   • Supabase authentication (login redirect, session check, logout)
 *   • Chat UI: send, receive, render markdown, typing indicator
 *   • File upload: attach, preview, send with message
 *   • Conversation history: load, open, delete
 *   • Settings: dark mode, font size, export, clear, delete account
 *   • Profile modal: stats, health memory, doctor brief
 *   • Voice input (Speech Recognition API)
 *   • Plan/upgrade status
 *   • Keyboard shortcuts
 */

"use strict";

/* ══════════════════════════════════════════════════════════════
   CONSTANTS & STATE
══════════════════════════════════════════════════════════════ */

const API_BASE = "https://api.curabook.com";

// Global state
let supabaseClient     = null;
let currentUser        = null;
let activeConvId       = null;
let uploadedFiles      = [];
let isProcessing       = false;
let conversationToDelete = null;

/* ══════════════════════════════════════════════════════════════
   DOM REFERENCES
══════════════════════════════════════════════════════════════ */

const $id   = (id) => document.getElementById(id);
const $qs   = (sel) => document.querySelector(sel);

const DOM = {
    // Layout
    sidebar:        $id("sidebar"),
    overlay:        $id("overlay"),
    closeSidebar:   $id("close-sidebar-btn"),
    mobileMenu:     $id("mobile-menu-btn"),

    // Header
    avatarInitial:  $id("avatar-initial"),
    dropdownEmail:  $id("dropdown-email"),
    dropdownPlan:   $id("dropdown-plan-label"),
    profileBtn:     $id("profileBtn"),
    profileDrop:    $id("profileDropdown"),
    logoutBtn:      $id("logoutBtn"),
    statusLabel:    $id("statusLabel"),

    // Chat
    welcomeScreen:  $id("welcomeScreen"),
    welcomeChips:   $id("welcomeChips"),
    chatDisplay:    $id("chat-display"),
    userInput:      $id("userInput"),
    sendBtn:        $id("sendBtn"),
    attachBtn:      $id("attachBtn"),
    fileInput:      $id("fileInput"),
    micBtn:         $id("micBtn"),
    filePreview:    $id("file-preview-container"),
    inputBar:       $id("inputBar"),

    // Sidebar
    newChatBtn:     $id("newChatBtn"),
    historyList:    $id("historyList"),
    userEmailDisp:  $id("user-email-display"),
    btnUploadNav:   $id("btn-upload-nav"),

    // Modals
    profileModal:   $id("profile-modal"),
    settingsModal:  $id("settings-modal"),
    deleteModal:    $id("delete-modal"),
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

    // Settings controls
    themeToggle:    $id("themeToggle"),
    fontSizeInput:  $id("fontSizeInput"),
    fontSizeValue:  $id("fontSizeValue"),
    exportChatBtn:  $id("exportChatBtn"),
    clearHistBtn:   $id("clearHistoryBtn"),
    exportDataBtn:  $id("exportDataBtn"),
    deleteAccBtn:   $id("deleteAccountBtn"),

    // Delete confirm
    confirmDeleteBtn: $id("confirmDeleteBtn"),
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
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
    if (typeof marked !== "undefined") {
        return marked.parse(String(text || ""));
    }
    return escapeHtml(String(text || ""));
}

function autoGrowTextarea(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

function setProcessing(loading) {
    isProcessing = loading;
    DOM.sendBtn.disabled = loading;
    DOM.userInput.disabled = loading;
    DOM.sendBtn.innerHTML = loading
        ? '<i class="fa-solid fa-spinner fa-spin"></i>'
        : '<i class="fa-solid fa-arrow-up"></i>';
}

/* ══════════════════════════════════════════════════════════════
   SUPABASE + AUTH
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

    // UI: set avatar, emails
    const initial = (user.email || "?").charAt(0).toUpperCase();
    DOM.avatarInitial.textContent = initial;
    if (DOM.dropdownEmail) DOM.dropdownEmail.textContent  = user.email;
    if (DOM.userEmailDisp) DOM.userEmailDisp.textContent  = user.email;
    if (DOM.modalEmail)    DOM.modalEmail.textContent     = user.email;
    if (DOM.modalAvatar)   DOM.modalAvatar.textContent    = initial;

    // Consent (non-blocking)
    saveUserConsents().catch(() => {});

    // Preferences
    loadUserPreferences();

    // History
    loadHistory();

    // Profile stats (non-blocking)
    loadProfileStats(user).catch(() => {});

    // Plan status (non-blocking)
    loadPlanStatus().catch(() => {});

    showToast("Welcome back! 👋");

    // Check for pending demo doc
    _carryOverDemoDoc();

    // Check for ?upload=1 redirect from landing page
    if (new URLSearchParams(location.search).get("upload") === "1") {
        history.replaceState({}, "", location.pathname);
        setTimeout(() => DOM.fileInput?.click(), 600);
    }
}

async function saveUserConsents() {
    const headers = await getAuthHeaders();
    if (!headers.Authorization) return;
    await fetch(`${API_BASE}/api/consent`, {
        method:  "POST",
        headers,
        body:    JSON.stringify({
            consents: ["data_processing", "ai_processing", "document_processing"],
        }),
    }).catch(() => {});
}

async function handleLogout() {
    if (!confirm("Sign out of Curabook PHI?")) return;
    await supabaseClient.auth.signOut();
    window.location.href = "login.html";
}

function _carryOverDemoDoc() {
    const docText    = sessionStorage.getItem("phi_pending_doc_text");
    const docName    = sessionStorage.getItem("phi_pending_doc_name");
    const docSummary = sessionStorage.getItem("phi_pending_doc_summary");
    if (!docText || !docName) return;

    sessionStorage.removeItem("phi_pending_doc_text");
    sessionStorage.removeItem("phi_pending_doc_name");
    sessionStorage.removeItem("phi_pending_doc_summary");

    window._lastDocText = docText;
    setTimeout(async () => {
        if (!activeConvId) await createConversation();
        if (docSummary) {
            appendMessage(`📋 **${docName}** (from demo session)\n\n${docSummary}`, "ai");
        }
        DOM.userInput.value = "Please explain my uploaded report thoroughly — every finding, what is normal, what needs attention, and what to discuss with my doctor.";
        handleSend();
    }, 1200);
}

/* ══════════════════════════════════════════════════════════════
   CONVERSATION MANAGEMENT
══════════════════════════════════════════════════════════════ */

async function createConversation() {
    const headers = await getAuthHeaders();
    const res     = await fetch(`${API_BASE}/conversation/create`, {
        method:  "POST",
        headers,
        body:    JSON.stringify({}),
    });
    const data = await res.json();
    if (res.status === 403) {
        showToast("Consent required. Please accept terms in settings.", "error");
        return null;
    }
    if (res.ok && data.conversation_id) {
        activeConvId = data.conversation_id;
        loadHistory();
    }
    return data.conversation_id || null;
}

// Debounced history loader
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
    conversations.forEach((c) => {
        const item  = document.createElement("div");
        item.className = "history-item" + (c.id === activeConvId ? " active" : "");
        item.setAttribute("data-id", c.id);
        item.setAttribute("role", "listitem");

        const title = document.createElement("span");
        title.className   = "history-title";
        title.textContent = c.title || "New Chat";
        title.onclick     = () => openConversation(c.id);

        const del = document.createElement("button");
        del.className   = "delete-chat";
        del.title       = "Delete conversation";
        del.setAttribute("aria-label", "Delete conversation");
        del.innerHTML   = '<i class="fa-solid fa-trash"></i>';
        del.onclick     = (e) => { e.stopPropagation(); showDeleteModal(c.id); };

        item.appendChild(title);
        item.appendChild(del);
        DOM.historyList.appendChild(item);
    });
}

async function openConversation(id) {
    if (isProcessing) { showToast("Please wait for the current request to finish.", "error"); return; }

    setProcessing(true);
    activeConvId = id;
    uploadedFiles = [];
    updateFilePreview();
    window._lastDocText   = null;
    window._lastDocId     = null;
    window._lastDocStored = false;  // Fix #7 — reset on conversation switch

    // Show chat, hide welcome
    showChatMode();

    DOM.chatDisplay.innerHTML = "";

    try {
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/conversation`, {
            method:  "POST",
            headers,
            body:    JSON.stringify({ conversation_id: id }),
        });
        if (!res.ok) throw new Error("Load failed");

        const messages = await res.json();
        if (Array.isArray(messages) && messages.length) {
            messages.forEach((m) => appendMessage(m.content, m.role === "user" ? "user" : "ai"));
        } else {
            DOM.chatDisplay.innerHTML = '<div class="empty-state">No messages in this conversation.</div>';
        }

        // Highlight active in sidebar
        document.querySelectorAll(".history-item").forEach((el) => {
            el.classList.toggle("active", el.getAttribute("data-id") === id);
        });

        // Close mobile sidebar
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
        method:  "POST",
        headers,
        body:    JSON.stringify({ conversation_id: id, title }),
    }).catch(() => {});
}

/* ══════════════════════════════════════════════════════════════
   CHAT SEND / RECEIVE
══════════════════════════════════════════════════════════════ */

async function handleSend() {
    if (isProcessing) return;

    let text = DOM.userInput.value.trim();

    // If files attached but no text, auto-generate explanation request
    if (!text && uploadedFiles.length > 0) {
        text = "Please read my uploaded medical report thoroughly and explain every finding in plain language — what is normal, what needs attention, what each value means, and what I should discuss with my doctor.";
    }

    if (!text && uploadedFiles.length === 0) return;

    // Ensure conversation exists
    if (!activeConvId) {
        const created = await createConversation();
        if (!created) return;
    }

    // Show chat view
    showChatMode();

    // Clear input
    DOM.userInput.value = "";
    DOM.userInput.style.height = "auto";

    // Process attached files first
    let documentContents = [];
    if (uploadedFiles.length > 0) {
        setProcessing(true);
        const processingMsg = appendMessage(`📎 Processing ${uploadedFiles.length} document(s)…`, "ai");

        for (const file of uploadedFiles) {
            const result = await processFile(file);
            if (result) documentContents.push(result);
        }

        // Remove processing message
        processingMsg.remove();

        // Cache doc context
        const primary = documentContents[0];
        if (primary) {
            if (primary.document_id)   window._lastDocId     = primary.document_id;
            if (primary.document_text) window._lastDocText   = primary.document_text;
            window._lastDocStored = false;  // Reset: this is a fresh document, not yet sent
        }

        uploadedFiles = [];
        updateFilePreview();
        setProcessing(false);

        // Update chips based on findings
        updateSuggestionChips(documentContents);

        // Refresh profile stats after upload
        setTimeout(() => loadProfileStats(currentUser).catch(() => {}), 2000);
    }

    // Show user message
    appendMessage(text, "user");

    // Show typing indicator
    const botRow = appendTyping();
    setProcessing(true);

    try {
        const headers = await getAuthHeaders();

        // Fix #3 — Send raw document_text ONLY on the first turn after upload.
        // Follow-up questions reference by document_id only; the backend reads
        // from DB memory. This prevents 12KB of text being re-sent every message
        // and makes context work correctly after page refresh.
        const isFirstDocTurn = documentContents.length > 0 && !window._lastDocStored;
        const primaryDocText = isFirstDocTurn
            ? (documentContents.map((d) => d.document_text || "").filter(Boolean)[0] || "")
            : "";  // Don't re-send on follow-ups
        const primaryDocId = documentContents.map((d) => d.document_id || "").filter(Boolean)[0]
            || window._lastDocId || "";

        // Mark as sent so subsequent messages don't re-send the full text
        if (isFirstDocTurn) window._lastDocStored = true;

        const body = {
            conversation_id: activeConvId,
            message:         text,
            has_documents:   isFirstDocTurn || !!primaryDocId,
            document_id:     primaryDocId,
            document_text:   primaryDocText,
        };

        const res  = await fetch(`${API_BASE}/chat`, {
            method:  "POST",
            headers,
            body:    JSON.stringify(body),
        });

        const data = await res.json();

        if (res.status === 403) {
            updateMessage(botRow, "⚠️ Consent required. Please accept terms in Settings.");
        } else if (data.reply) {
            updateMessage(botRow, data.reply);
        } else {
            updateMessage(botRow, "Sorry, I couldn't process that. Please try again.");
            showToast("No response received.", "error");
        }
    } catch (err) {
        console.error("[CHAT] Send error:", err);
        updateMessage(botRow, "Connection error. Please check your network and try again.");
        showToast("Network error.", "error");
    } finally {
        setProcessing(false);
    }

    // Auto-rename after first user message
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
    const wrap    = document.createElement("div");
    wrap.className = `chat-message ${role === "user" ? "user-msg" : "bot-msg"}`;

    const av    = document.createElement("div");
    av.className = `msg-avatar ${role === "user" ? "user-av" : "ai-av"}`;
    av.textContent = role === "user"
        ? (currentUser?.email?.charAt(0)?.toUpperCase() || "U")
        : "φ";

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.innerHTML = role === "user" ? escapeHtml(text) : renderMarkdown(text);

    if (role === "user") {
        wrap.appendChild(bubble);
        wrap.appendChild(av);
    } else {
        wrap.appendChild(av);
        wrap.appendChild(bubble);
    }

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
    bubble.innerHTML = `
        <div class="typing-indicator">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>`;

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
   WELCOME / CHAT MODE TOGGLE
══════════════════════════════════════════════════════════════ */

function showChatMode() {
    if (DOM.welcomeScreen) {
        DOM.welcomeScreen.classList.add("hidden-welcome");
        DOM.welcomeScreen.style.display = "none";
    }
}

function showWelcomeMode() {
    if (DOM.welcomeScreen) {
        DOM.welcomeScreen.classList.remove("hidden-welcome");
        DOM.welcomeScreen.style.display = "";
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
            if (data.document_id) window._lastDocId = data.document_id;
            showToast(`✓ ${file.name} analyzed (${data.abnormal_count || 0} findings need attention)`);
            return {
                name:          file.name,
                summary:       data.summary_text  || "",
                document_id:   data.document_id   || "",
                document_text: data.document_text || "",
                markers:       data.markers        || [],
                doc_type:      data.doc_type       || "clinical",
                doctor_prep:   data.doctor_prep    || "",
                abnormal:      data.abnormal_count || 0,
            };
        } else {
            showToast(data.error || `Could not process ${file.name}`, "error");
            return null;
        }
    } catch (err) {
        console.error("[UPLOAD] Error:", err);
        showToast(`Upload failed for ${file.name}`, "error");
        return null;
    }
}

function updateFilePreview() {
    DOM.filePreview.innerHTML = "";
    if (uploadedFiles.length === 0) {
        DOM.filePreview.classList.remove("visible");
        DOM.userInput.placeholder = "Ask PHI about your health, upload a report, or type a question…";
        return;
    }

    DOM.filePreview.classList.add("visible");
    uploadedFiles.forEach((file, index) => {
        const chip    = document.createElement("div");
        chip.className = "file-chip";
        const icon = file.name.toLowerCase().endsWith(".pdf") ? "fa-file-pdf" : "fa-file-lines";
        chip.innerHTML = `
            <i class="fa-solid ${icon}"></i>
            <span>${escapeHtml(file.name)}</span>
            <button class="remove-file" aria-label="Remove ${escapeHtml(file.name)}" onclick="removeFile(${index})">
                <i class="fa-solid fa-xmark"></i>
            </button>`;
        DOM.filePreview.appendChild(chip);
    });

    DOM.userInput.placeholder = `${uploadedFiles.length} file(s) attached — press Send or type a question…`;
}

window.removeFile = function (index) {
    uploadedFiles.splice(index, 1);
    updateFilePreview();
    showToast("File removed");
};

function updateSuggestionChips(docResults) {
    if (!DOM.welcomeChips) return;
    const markers  = docResults.flatMap((d) => d.markers || []);
    const abnormal = markers.filter((m) => m.status === "HIGH" || m.status === "LOW");

    let chips = [];
    if (abnormal.length > 0) {
        const name = abnormal[0].marker || abnormal[0].marker_name || "";
        const val  = abnormal[0].value || "";
        chips = [
            { icon: "fa-triangle-exclamation", text: `What does my ${name} of ${val} mean?` },
            { icon: "fa-stethoscope",           text: "Is this serious? Do I need to see a doctor urgently?" },
            { icon: "fa-dumbbell",              text: "What lifestyle changes can help improve my results?" },
        ];
    } else if (markers.length > 0) {
        chips = [
            { icon: "fa-circle-check",  text: "Are all my results truly normal, or should I be concerned?" },
            { icon: "fa-heart-pulse",   text: "What should I focus on to maintain healthy levels?" },
            { icon: "fa-calendar-check",text: "When should I get my next blood test?" },
        ];
    } else {
        chips = [
            { icon: "fa-file-medical", text: "What does this report tell me about my health?" },
            { icon: "fa-stethoscope",  text: "Prepare me for my next doctor visit based on this report" },
            { icon: "fa-chart-line",   text: "Are there any warning signs I should watch for?" },
        ];
    }

    DOM.welcomeChips.innerHTML = chips.map((c) =>
        `<button class="chip" data-suggestion="${escapeHtml(c.text)}">
            <i class="fa-solid ${c.icon}"></i> ${escapeHtml(c.text)}
        </button>`
    ).join("");

    attachChipListeners();
}

/* ══════════════════════════════════════════════════════════════
   SUGGESTION CHIPS
══════════════════════════════════════════════════════════════ */

function attachChipListeners() {
    document.querySelectorAll(".chip[data-suggestion]").forEach((btn) => {
        btn.onclick = () => {
            DOM.userInput.value = btn.dataset.suggestion;
            DOM.userInput.focus();
            autoGrowTextarea(DOM.userInput);
            setTimeout(() => handleSend(), 100);
        };
    });
}

// Global helper for inline onclick (kept for backwards compat)
window.useSuggestion = function (btn) {
    const text = btn.dataset?.suggestion || btn.textContent?.trim();
    if (!text) return;
    DOM.userInput.value = text;
    DOM.userInput.focus();
    setTimeout(() => handleSend(), 100);
};

/* ══════════════════════════════════════════════════════════════
   SIDEBAR TOGGLE
══════════════════════════════════════════════════════════════ */

function openSidebar() {
    DOM.sidebar.classList.add("open");
    DOM.overlay.classList.add("active");
}

function closeSidebar() {
    DOM.sidebar.classList.remove("open");
    DOM.overlay.classList.remove("active");
}

/* ══════════════════════════════════════════════════════════════
   MODALS
══════════════════════════════════════════════════════════════ */

window.closeModals = function () {
    [DOM.profileModal, DOM.settingsModal, DOM.deleteModal].forEach((m) => {
        if (m) m.classList.add("hidden");
    });
};

function showDeleteModal(id) {
    conversationToDelete = id;
    DOM.deleteModal?.classList.remove("hidden");
}

// Click-outside-to-close for modals
[DOM.profileModal, DOM.settingsModal, DOM.deleteModal].forEach((modal) => {
    if (!modal) return;
    modal.addEventListener("click", (e) => {
        if (e.target === modal) window.closeModals();
    });
});

/* ══════════════════════════════════════════════════════════════
   PROFILE & HEALTH DASHBOARD
══════════════════════════════════════════════════════════════ */

async function loadProfileStats(user) {
    // Member since
    if (DOM.accountCreated && user?.created_at) {
        DOM.accountCreated.textContent = new Date(user.created_at)
            .toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    }

    // Total conversations
    try {
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/history`, { method: "POST", headers });
        const convs   = await res.json();
        if (DOM.totalConvs) DOM.totalConvs.textContent = Array.isArray(convs) ? convs.length : 0;
    } catch {
        if (DOM.totalConvs) DOM.totalConvs.textContent = "0";
    }

    // Dashboard stats (docs analyzed + markers)
    try {
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/api/dashboard`, { headers });
        if (res.ok) {
            const dash = await res.json();
            if (DOM.docsAnalyzed) DOM.docsAnalyzed.textContent = dash.document_count || 0;
        }
    } catch {
        if (DOM.docsAnalyzed) DOM.docsAnalyzed.textContent = "0";
    }
}

async function loadHealthDashboard() {
    if (!DOM.healthDash) return;
    const headers = await getAuthHeaders();
    if (!headers.Authorization) return;

    DOM.healthDash.innerHTML = '<div class="loading-text"><i class="fa-solid fa-spinner fa-spin"></i> Loading health memory…</div>';

    try {
        const [markersRes, insightsRes] = await Promise.all([
            fetch(`${API_BASE}/api/health-markers`,  { headers }),
            fetch(`${API_BASE}/api/health-insights`, { headers }),
        ]);
        const markers  = markersRes.ok  ? await markersRes.json()  : [];
        const insights = insightsRes.ok ? await insightsRes.json() : [];
        renderHealthDashboard(markers, insights);
    } catch {
        DOM.healthDash.innerHTML = '<div class="loading-text" style="color:#ef4444">Could not load health memory.</div>';
    }
}

function renderHealthDashboard(markers, insights) {
    if (!markers.length && !insights.length) {
        DOM.healthDash.innerHTML = '<div class="loading-text">No health data yet. Upload a lab report to build your health memory.</div>';
        return;
    }

    function flagColor(ref, val) {
        if (!ref) return "var(--text-main)";
        try {
            const r = String(ref).trim();
            if (r.startsWith("<") && val > parseFloat(r.slice(1))) return "#ef4444";
            if (r.startsWith(">") && val < parseFloat(r.slice(1))) return "#f59e0b";
            if (r.includes("-")) {
                const [lo, hi] = r.split("-").map(Number);
                if (val < lo || val > hi) return "#ef4444";
            }
            return "#22c55e";
        } catch { return "var(--text-main)"; }
    }

    const markerCards = markers.map((m) => `
        <div style="background:var(--bg-hover);border-radius:8px;padding:10px;min-width:110px;flex:1">
            <div style="font-size:11px;color:var(--text-muted);margin-bottom:2px">${escapeHtml(m.marker_name)}</div>
            <div style="font-size:1.05rem;font-weight:700;color:${flagColor(m.reference_range, m.value)}">
                ${m.value} <span style="font-size:11px;font-weight:400">${escapeHtml(m.unit || "")}</span>
            </div>
            <div style="font-size:10px;color:var(--text-muted)">ref: ${escapeHtml(m.reference_range || "—")} · ${escapeHtml(m.date || "")}</div>
        </div>`).join("");

    const insightItems = insights.map((ins) => `
        <div style="border-left:3px solid ${ins.severity === "high" ? "#ef4444" : ins.severity === "medium" ? "#f59e0b" : "#22c55e"};
                    padding:7px 10px;margin-bottom:5px;background:var(--bg-hover);border-radius:0 6px 6px 0">
            <div style="font-weight:600;font-size:12px">${escapeHtml(ins.headline || "")}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${escapeHtml(ins.detail || "")}</div>
        </div>`).join("");

    DOM.healthDash.innerHTML = `
        ${markers.length  ? `<div style="display:flex;flex-wrap:wrap;gap:7px;margin-bottom:12px">${markerCards}</div>` : ""}
        ${insights.length ? `
            <div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:5px">
                <i class="fa-solid fa-lightbulb" style="color:#f59e0b"></i> Insights
            </div>
            ${insightItems}` : ""}`;

    DOM.doctorBriefBtn?.addEventListener("click", showDoctorBriefModal);
}

function showDoctorBriefModal() {
    $id("doctor-brief-modal")?.remove();
    const modal = document.createElement("div");
    modal.id         = "doctor-brief-modal";
    modal.className  = "modal";
    modal.style.zIndex = "10001";
    modal.innerHTML  = `
        <div class="modal-box" style="max-width:560px">
            <div class="modal-header">
                <h3><i class="fa-solid fa-stethoscope"></i> Prepare for Doctor Visit</h3>
                <button class="close-btn" onclick="document.getElementById('doctor-brief-modal').remove()">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
            <div class="modal-body" style="padding:20px">
                <label style="font-size:13px;font-weight:600;display:block;margin-bottom:4px">
                    Symptoms <span style="color:var(--text-muted);font-weight:400">(comma separated)</span>
                </label>
                <input type="text" id="brief-symptoms" placeholder="fatigue, headaches, dizziness"
                    style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:13px;box-sizing:border-box;margin-bottom:10px;background:var(--bg-input);color:var(--text-main)">
                <label style="font-size:13px;font-weight:600;display:block;margin-bottom:4px">
                    Medications <span style="color:var(--text-muted);font-weight:400">(comma separated)</span>
                </label>
                <input type="text" id="brief-meds" placeholder="Metformin 500mg, Lisinopril 10mg"
                    style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:13px;box-sizing:border-box;margin-bottom:10px;background:var(--bg-input);color:var(--text-main)">
                <label style="font-size:13px;font-weight:600;display:block;margin-bottom:4px">Notes</label>
                <textarea id="brief-notes" rows="2" placeholder="Annual check-up, concerned about cholesterol"
                    style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:13px;box-sizing:border-box;resize:vertical;background:var(--bg-input);color:var(--text-main)"></textarea>
                <div id="brief-output" style="display:none;margin-top:14px;background:var(--bg-hover);border-radius:8px;padding:14px;font-size:13px;line-height:1.6;max-height:280px;overflow-y:auto"></div>
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" onclick="document.getElementById('doctor-brief-modal').remove()">Cancel</button>
                <button id="generateBriefBtn" style="padding:9px 18px;background:var(--brand);border:none;border-radius:8px;color:white;font-size:14px;font-weight:600;font-family:var(--font);cursor:pointer">
                    <i class="fa-solid fa-wand-magic-sparkles"></i> Generate
                </button>
            </div>
        </div>`;
    document.body.appendChild(modal);

    $id("generateBriefBtn").onclick = async () => {
        const symptoms    = $id("brief-symptoms").value.split(",").map((s) => s.trim()).filter(Boolean);
        const medications = $id("brief-meds").value.split(",").map((s) => s.trim()).filter(Boolean);
        const notes       = $id("brief-notes").value;
        const btn         = $id("generateBriefBtn");

        btn.disabled     = true;
        btn.innerHTML    = '<i class="fa-solid fa-spinner fa-spin"></i> Generating…';

        try {
            const headers = await getAuthHeaders();
            const res     = await fetch(`${API_BASE}/api/doctor-brief`, {
                method:  "POST",
                headers,
                body:    JSON.stringify({ symptoms, medications, notes }),
            });
            const data    = await res.json();
            const output  = $id("brief-output");
            output.style.display = "block";
            output.innerHTML = renderMarkdown(data.brief || "Could not generate brief. Please try again.");

            const dlBtn = document.createElement("button");
            dlBtn.style.cssText = "margin-top:8px;padding:7px 14px;background:var(--brand);border:none;border-radius:6px;color:white;font-size:12px;font-weight:600;cursor:pointer";
            dlBtn.innerHTML = '<i class="fa-solid fa-download"></i> Download';
            dlBtn.onclick = () => {
                const blob = new Blob([data.brief || ""], { type: "text/plain" });
                const a    = document.createElement("a");
                a.href     = URL.createObjectURL(blob);
                a.download = `doctor-brief-${Date.now()}.txt`;
                a.click();
            };
            output.after(dlBtn);
        } catch {
            showToast("Failed to generate brief.", "error");
        }

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
        DOM.dropdownPlan.textContent  = data.is_pro ? "✦ PHI Pro" : "PHI Free";
        if (data.is_pro) DOM.dropdownPlan.style.color = "var(--brand)";
    }

    // Show upgrade button for free users
    if (!data.is_pro && DOM.profileDrop && !DOM.profileDrop.querySelector(".upgrade-btn")) {
        const btn = document.createElement("button");
        btn.className = "dropdown-item upgrade-btn";
        btn.style.cssText = "color:var(--brand);font-weight:600";
        btn.innerHTML = '<i class="fa-solid fa-bolt"></i> Upgrade to Pro';
        btn.onclick   = async () => {
            const h    = await getAuthHeaders();
            const r    = await fetch(`${API_BASE}/api/payment/checkout`, {
                method: "POST", headers: h,
                body:   JSON.stringify({ plan: "monthly" }),
            });
            const d    = await r.json();
            if (d.checkout_url) window.location.href = d.checkout_url;
            else showToast(d.error || "Payment not configured.", "error");
        };
        const hr = DOM.profileDrop.querySelector(".dropdown-divider");
        if (hr) DOM.profileDrop.insertBefore(btn, hr.nextSibling);
    }
}

/* ══════════════════════════════════════════════════════════════
   VOICE INPUT
══════════════════════════════════════════════════════════════ */

function initVoiceInput() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
        if (DOM.micBtn) {
            DOM.micBtn.disabled      = true;
            DOM.micBtn.style.opacity = "0.3";
            DOM.micBtn.title         = "Voice not supported — use Chrome or Edge";
        }
        return;
    }

    let isListening = false;
    let recognition = null;

    DOM.micBtn?.addEventListener("click", () => {
        if (isListening) { recognition?.stop(); return; }

        recognition                = new SR();
        recognition.lang           = "en-US";
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;

        recognition.onstart = () => {
            isListening = true;
            DOM.micBtn?.classList.add("listening");
            showToast("Listening… speak now");
        };

        recognition.onresult = (e) => {
            DOM.userInput.value = e.results[0][0].transcript;
            autoGrowTextarea(DOM.userInput);
            DOM.userInput.focus();
        };

        recognition.onerror = (e) => {
            const msgs = {
                "no-speech":   "No speech detected. Try again.",
                "not-allowed": "Microphone blocked. Allow microphone access in browser settings.",
                "network":     "Network error during voice recognition.",
            };
            showToast(msgs[e.error] || `Voice error: ${e.error}`, "error");
        };

        recognition.onend = () => {
            isListening = false;
            DOM.micBtn?.classList.remove("listening");
        };

        try { recognition.start(); } catch { showToast("Voice input failed to start.", "error"); }
    });
}

/* ══════════════════════════════════════════════════════════════
   SETTINGS
══════════════════════════════════════════════════════════════ */

function loadUserPreferences() {
    const prefs = JSON.parse(localStorage.getItem("phi_prefs") || "{}");

    // Theme
    const isDark = prefs.theme === "dark";
    if (isDark) document.body.classList.add("dark-mode");
    if (DOM.themeToggle) DOM.themeToggle.checked = isDark;

    // Font size
    const fontSize = prefs.fontSize || 15;
    document.documentElement.style.setProperty("--chat-font-size", fontSize + "px");
    if (DOM.fontSizeInput) DOM.fontSizeInput.value   = fontSize;
    if (DOM.fontSizeValue) DOM.fontSizeValue.textContent = fontSize + "px";
}

function savePreference(key, value) {
    const prefs = JSON.parse(localStorage.getItem("phi_prefs") || "{}");
    prefs[key]  = value;
    localStorage.setItem("phi_prefs", JSON.stringify(prefs));
}

function initSettings() {
    // Dark mode toggle
    DOM.themeToggle?.addEventListener("change", (e) => {
        const dark = e.target.checked;
        document.body.classList.toggle("dark-mode", dark);
        savePreference("theme", dark ? "dark" : "light");
        showToast(dark ? "Dark mode enabled" : "Light mode enabled");
    });

    // Font size slider
    DOM.fontSizeInput?.addEventListener("input", (e) => {
        const size = e.target.value;
        document.documentElement.style.setProperty("--chat-font-size", size + "px");
        if (DOM.fontSizeValue) DOM.fontSizeValue.textContent = size + "px";
        savePreference("fontSize", parseInt(size, 10));
    });

    // Export chat
    DOM.exportChatBtn?.addEventListener("click", async () => {
        if (!activeConvId) { showToast("No active conversation to export.", "error"); return; }
        const headers = await getAuthHeaders();
        const res     = await fetch(`${API_BASE}/conversation`, {
            method: "POST", headers,
            body:   JSON.stringify({ conversation_id: activeConvId }),
        });
        const msgs = await res.json();
        let out = "Curabook PHI — Chat Export\n" + "=".repeat(50) + "\n\n";
        msgs.forEach((m) => { out += `${m.role.toUpperCase()}:\n${m.content}\n\n`; });
        const a = Object.assign(document.createElement("a"), {
            href:     URL.createObjectURL(new Blob([out], { type: "text/plain" })),
            download: `phi-chat-${Date.now()}.txt`,
        });
        a.click();
        showToast("Chat exported");
    });

    // Clear all history
    DOM.clearHistBtn?.addEventListener("click", async () => {
        if (!confirm("⚠️ Delete ALL conversations? This cannot be undone.")) return;
        const headers = await getAuthHeaders();
        const histRes = await fetch(`${API_BASE}/history`, { method: "POST", headers });
        const chats   = await histRes.json();
        if (Array.isArray(chats)) {
            for (const c of chats) {
                await fetch(`${API_BASE}/delete`, {
                    method: "POST", headers,
                    body:   JSON.stringify({ conversation_id: c.id }),
                }).catch(() => {});
            }
        }
        DOM.chatDisplay.innerHTML = "";
        activeConvId  = null;
        uploadedFiles = [];
        updateFilePreview();
        showWelcomeMode();
        loadHistory();
        window.closeModals();
        showToast("All conversations deleted");
    });

    // Export data (GDPR)
    DOM.exportDataBtn?.addEventListener("click", async () => {
        const headers = await getAuthHeaders();
        try {
            const res  = await fetch(`${API_BASE}/export-data`, { method: "POST", headers });
            const data = await res.json();
            const a    = Object.assign(document.createElement("a"), {
                href:     URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })),
                download: `phi-data-export-${Date.now()}.json`,
            });
            a.click();
            showToast("✅ Data exported");
        } catch { showToast("Failed to export data.", "error"); }
    });

    // Delete account
    DOM.deleteAccBtn?.addEventListener("click", async () => {
        const confirmed = confirm(
            "⚠️ PERMANENTLY DELETE YOUR ACCOUNT?\n\n"
            + "This will delete ALL your conversations, reports, and health data.\n"
            + "This CANNOT be undone.\n\nContinue?"
        );
        if (!confirmed) return;
        if (prompt('Type "DELETE" to confirm:') !== "DELETE") return;

        const headers = await getAuthHeaders();
        try {
            const res = await fetch(`${API_BASE}/delete-account`, { method: "POST", headers });
            if (res.ok) {
                showToast("Account deleted. Goodbye.");
                setTimeout(() => window.location.href = "login.html", 2000);
            } else {
                showToast("Failed to delete account. Contact support.", "error");
            }
        } catch { showToast("Network error during account deletion.", "error"); }
    });
}

/* ══════════════════════════════════════════════════════════════
   DELETE CONVERSATION (CONFIRM MODAL)
══════════════════════════════════════════════════════════════ */

window.showDeleteModal = function (id) {
    conversationToDelete = id;
    DOM.deleteModal?.classList.remove("hidden");
};

DOM.confirmDeleteBtn?.addEventListener("click", async () => {
    if (!conversationToDelete) return;
    const headers = await getAuthHeaders();
    await fetch(`${API_BASE}/delete`, {
        method: "POST", headers,
        body:   JSON.stringify({ conversation_id: conversationToDelete }),
    }).catch(() => {});

    if (activeConvId === conversationToDelete) {
        DOM.chatDisplay.innerHTML = "";
        activeConvId  = null;
        uploadedFiles = [];
        updateFilePreview();
        showWelcomeMode();
    }

    loadHistory();
    window.closeModals();
    conversationToDelete = null;
    showToast("Conversation deleted");
});

/* ══════════════════════════════════════════════════════════════
   INITIAL AI GREETING
══════════════════════════════════════════════════════════════ */

function showInitialGreeting() {
    // Only show if no conversation is active
    if (activeConvId) return;
    // Don't add to chat display — it's shown in the welcome screen area
    // The welcome screen itself serves as the greeting
}

/* ══════════════════════════════════════════════════════════════
   EVENT WIRING
══════════════════════════════════════════════════════════════ */

function wireEvents() {
    // Sidebar open/close
    DOM.mobileMenu?.addEventListener("click", openSidebar);
    DOM.closeSidebar?.addEventListener("click", closeSidebar);
    DOM.overlay?.addEventListener("click", closeSidebar);

    // New chat
    DOM.newChatBtn?.addEventListener("click", () => {
        if (
            DOM.chatDisplay.children.length > 0 &&
            !confirm("Start a new chat? The current conversation is already saved.")
        ) return;

        activeConvId  = null;
        uploadedFiles = [];
        // Fix #7 — clear ALL doc state so next chat starts fresh
        window._lastDocText   = null;
        window._lastDocId     = null;
        window._lastDocStored = false;   // flag: has this doc been sent once already?
        DOM.chatDisplay.innerHTML = "";
        updateFilePreview();
        showWelcomeMode();

        document.querySelectorAll(".history-item").forEach((el) => el.classList.remove("active"));
        DOM.userInput.focus();
    });

    // Send
    DOM.sendBtn?.addEventListener("click", (e) => { e.preventDefault(); handleSend(); });

    // Enter to send (Shift+Enter for newline)
    DOM.userInput?.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    // Auto-grow textarea
    DOM.userInput?.addEventListener("input", () => autoGrowTextarea(DOM.userInput));

    // File attach button
    DOM.attachBtn?.addEventListener("click", () => DOM.fileInput?.click());

    // Upload nav button
    DOM.btnUploadNav?.addEventListener("click", () => {
        closeSidebar();
        DOM.fileInput?.click();
    });

    // File input change
    DOM.fileInput?.addEventListener("change", (e) => {
        const files = Array.from(e.target.files || []);
        files.forEach((file) => {
            if (file.size > 10 * 1024 * 1024) {
                showToast(`${file.name} is too large. Max 10MB.`, "error");
                return;
            }
            if (!/\.(pdf|txt)$/i.test(file.name)) {
                showToast(`${file.name} is not supported. Use PDF or TXT.`, "error");
                return;
            }
            uploadedFiles.push(file);
        });
        updateFilePreview();
        DOM.fileInput.value = "";
        if (uploadedFiles.length > 0) {
            showToast(`🔒 ${uploadedFiles.length} file(s) attached`);
            DOM.userInput.focus();
        }
    });

    // Profile button
    DOM.profileBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        DOM.profileDrop?.classList.toggle("hidden");
    });

    document.addEventListener("click", (e) => {
        if (!DOM.profileDrop?.contains(e.target) && e.target !== DOM.profileBtn) {
            DOM.profileDrop?.classList.add("hidden");
        }
    });

    // Profile/settings modal buttons
    DOM.btnProfile?.addEventListener("click", () => {
        window.closeModals();
        DOM.profileModal?.classList.remove("hidden");
        loadHealthDashboard();
    });

    DOM.btnSettings?.addEventListener("click", () => {
        window.closeModals();
        DOM.settingsModal?.classList.remove("hidden");
    });

    // Logout
    DOM.logoutBtn?.addEventListener("click", handleLogout);
    DOM.modalLogout?.addEventListener("click", handleLogout);

    // Suggestion chips (initial)
    attachChipListeners();

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "k") {
            e.preventDefault();
            DOM.newChatBtn?.click();
        }
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
        showToast("Cannot connect to server. Check if the backend is running.", "error");
        return;
    }

    // Check auth
    const session = await getSession();
    if (!session?.user) {
        window.location.href = "login.html";
        return;
    }

    const user = session.user;

    // Google OAuth — enforce terms acceptance
    if (user.app_metadata?.provider === "google") {
        const termsKey = `phi_terms_${user.id}`;
        if (!localStorage.getItem(termsKey)) {
            // Redirect to login.html which handles the terms modal
            window.location.href = "login.html";
            return;
        }
    }

    // Wire all events
    wireEvents();

    // Init settings
    initSettings();

    // Init voice input
    initVoiceInput();

    // Preferences (theme, font size)
    loadUserPreferences();

    // Complete login
    await handleLoginSuccess(user);

    // Show welcome screen greeting
    showInitialGreeting();
});