/**
 * script.js — Curabook PHI v3.8 — DSS Market Ready
 * Includes Decision Support UI (Action Plan + Follow-ups) and synchronized branding.
 */

"use strict";

const API_BASE = "https://api.curabook.com";

/* ── State ─────────────────────────────────────────────────── */
let supabaseClient       = null;
let currentUser          = null;
let currentUserName      = "";
let activeConvId         = null;
let uploadedFiles        = [];
let isProcessing         = false;
let conversationToDelete = null;
let _healthContext       = null;

const _chipTexts = new Map();
let _chipCounter = 0;

const _docCtx = {
    text: null, id: null, convId: null,
    set(t, i, c)  { this.text = t||null; this.id = i||null; this.convId = c||null; },
    getForConv(c) { return this.convId === c ? {text:this.text, id:this.id} : {text:null,id:null}; },
    clear()       { this.text = null; this.id = null; this.convId = null; },
};

const _cache = {
    _s: {},
    set(k, v, ms=30000) { this._s[k]={v,e:Date.now()+ms}; },
    get(k) { const e=this._s[k]; return (e&&e.e>Date.now())?e.v:null; },
    del(k) { delete this._s[k]; },
    clear() { this._s={}; },
};

let DOM = {};

function buildDOM() {
    const $id = id => document.getElementById(id);
    DOM = {
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
        filePreview:    $id("file-preview-container"),
        newChatBtn:     $id("newChatBtn"),
        historyList:    $id("historyList"),
        userEmailDisp:  $id("user-email-display"),
        btnUploadNav:   $id("btn-upload-nav"),
        btnHealthPulse: $id("btn-health-pulse"),
        btnLogActivity: $id("btn-log-activity"),
        logActivityModal:    $id("log-activity-modal"),
        advocacyGuideModal:  $id("advocacy-guide-modal"),
        advocacyInfoBtn:     $id("advocacyInfoBtn"),
        btnAdvocacy:      $id("btn-advocacy"),
        advocacyModal:    $id("advocacy-modal"),
        profileModal:   $id("profile-modal"),
        settingsModal:  $id("settings-modal"),
        deleteModal:    $id("delete-modal"),
        pulseModal:     $id("pulse-modal"),
        pulseModalBody: $id("pulse-modal-body")
    };
}

/* ── Utilities ──────────────────────────────────────────────── */
async function safeJson(res) {
    if (!res) return { error: "No response" };
    const text = await res.text().catch(() => "");
    if (!text || !text.trim()) return { error: `Empty response (HTTP ${res.status})` };
    try { return JSON.parse(text); }
    catch { return { error: `Server error (HTTP ${res.status})` }; }
}

function showToast(msg, type = "success") {
    const el = document.createElement("div");
    el.className = type === "success" ? "success-toast" : "error-toast";
    el.innerHTML = `<i class="fa-solid fa-${type==="success"?"circle-check":"circle-exclamation"}"></i> ${escapeHtml(msg)}`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function escapeHtml(s) {
    return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function renderMarkdown(text) {
    return typeof marked !== "undefined" ? marked.parse(String(text||"")) : escapeHtml(String(text||""));
}

function autoGrow(el) { el.style.height="auto"; el.style.height=Math.min(el.scrollHeight,150)+"px"; }

function setProcessing(on) {
    isProcessing = on;
    if (DOM.sendBtn)   DOM.sendBtn.disabled  = on;
    if (DOM.userInput) DOM.userInput.disabled = on;
    if (DOM.sendBtn)   DOM.sendBtn.innerHTML = on ? '<i class="fa-solid fa-spinner fa-spin"></i>' : '<i class="fa-solid fa-arrow-up"></i>';
}

function _greeting(name) {
    const h = new Date().getHours();
    const period = h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
    return name ? `Good ${period}, ${name}` : `Good ${period}`;
}

/* ── Auth (Supabase) ────────────────────────────────────────── */
async function initSupabase() {
    const URL = "https://pbeaawlxdcrdbvlmpqhc.supabase.co";
    const KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBiZWFhd2x4ZGNyZGJ2bG1wcWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMDk0MzksImV4cCI6MjA5MTU4NTQzOX0.6bUpYrDbe0mQjjBHX8Qscj-5R8i4-SqAtW_Z1UFzJ10";
    supabaseClient = supabase.createClient(URL, KEY);
    window.supabaseClient = supabaseClient;
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
    return local ? local.charAt(0).toUpperCase() + local.slice(1) : "";
}

async function handleLoginSuccess(user) {
    currentUser     = user;
    currentUserName = _extractFirstName(user);
    _docCtx.clear(); _cache.clear();

    const initial = (user.email||"?")[0].toUpperCase();
    if (DOM.avatarInitial) DOM.avatarInitial.textContent = initial;
    if (DOM.dropdownEmail) DOM.dropdownEmail.textContent = user.email;
    if (DOM.userEmailDisp) DOM.userEmailDisp.textContent = user.email;

    loadHistory(); loadHealthPulse().catch(()=>{});
}

/* ── UI Logic for Decision Support ──────────────────────────── */

function _postProcessBubble(bubbleEl) {
    if (!bubbleEl) return;
    _wrapLegalFooter(bubbleEl);
    _wrapNextSteps(bubbleEl);
    _wrapFollowUp(bubbleEl);
}

function _wrapLegalFooter(el) {
    const allNodes = Array.from(el.childNodes);
    for (let i = 0; i < allNodes.length; i++) {
        const node = allNodes[i];
        if (!node || node.nodeType !== Node.ELEMENT_NODE) continue;
        const text = node.textContent || "";
        if (text.includes("⚕️")) {
            const prev = node.previousElementSibling;
            const wrapper = document.createElement("div");
            wrapper.className = "phi-legal-footer";
            if (prev && prev.tagName === "HR") {
                el.insertBefore(wrapper, prev);
                wrapper.appendChild(prev);
            } else {
                el.insertBefore(wrapper, node);
            }
            wrapper.appendChild(node);
            wrapper.innerHTML = wrapper.innerHTML.replace(/⚕️/g, '<span class="phi-legal-icon">⚕️</span>');
            break;
        }
    }
}

function _wrapNextSteps(el) {
    const headingPattern = /recommended\s+next\s+steps/i;
    const children = Array.from(el.children);
    for (let i = 0; i < children.length; i++) {
        const child = children[i];
        const tag   = child.tagName.toLowerCase();
        const text  = child.textContent || "";
        if (!headingPattern.test(text)) continue;
        if (!["h1","h2","h3","h4","p","strong"].includes(tag)) continue;
        
        const wrapper = document.createElement("div");
        wrapper.className = "phi-next-steps";
        el.insertBefore(wrapper, child);
        wrapper.appendChild(child);
        
        let next = wrapper.nextElementSibling;
        while (next && (next.tagName === "OL" || next.tagName === "UL" || (next.tagName === "P" && next.textContent.trim() === ""))) {
            const toMove = next;
            next = next.nextElementSibling;
            wrapper.appendChild(toMove);
        }
        break;
    }
}

function _wrapFollowUp(el) {
    const followUpPattern = /you\s+might\s+ask\s+phi/i;
    const children = Array.from(el.children);
    for (let i = 0; i < children.length; i++) {
        const child = children[i];
        const text  = child.textContent || "";
        if (!followUpPattern.test(text)) continue;

        const wrapper = document.createElement("div");
        wrapper.className = "phi-follow-up";
        el.insertBefore(wrapper, child);
        child.remove();

        let next = wrapper.nextElementSibling;
        if (next && (next.tagName === "OL" || next.tagName === "UL")) {
            const chipContainer = document.createElement("div");
            chipContainer.style.cssText = "display:flex;flex-wrap:wrap;gap:5px;margin-top:2px";
            Array.from(next.querySelectorAll("li")).forEach(li => {
                const chipText = li.textContent.trim();
                if (!chipText) return;
                const chip = document.createElement("button");
                chip.className = "phi-follow-up-chip";
                chip.textContent = chipText;
                chip.setAttribute("title", "Ask PHI this question");
                chip.addEventListener("click", () => {
                    if (typeof sendChip === "function") sendChip(chipText);
                });
                chipContainer.appendChild(chip);
            });
            wrapper.appendChild(chipContainer);
            next.remove();
        }
        break;
    }
}

/* ── Chat Display ────────────────────────────────────────────── */

function appendMessage(text, role) {
    const wrap = document.createElement("div");
    wrap.className = `chat-message ${role === "user" ? "user-msg" : "bot-msg"}`;

    const av = document.createElement("div");
    av.className = `msg-avatar ${role === "user" ? "user-av" : "ai-av"}`;
    av.textContent = role === "user" ? (currentUser?.email?.[0]?.toUpperCase() || "U") : "φ";

    const bub = document.createElement("div");
    bub.className = "msg-bubble";
    bub.innerHTML = role === "user" ? escapeHtml(text) : renderMarkdown(text);

    // Apply the Decision UI post-processing to AI bubbles
    if (role !== "user") _postProcessBubble(bub);

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
    if (b) {
        b.innerHTML = renderMarkdown(text);
        _postProcessBubble(b);
    }
    DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
}

function _msgHTML(text, role) {
    const initial = role === "user" ? (currentUser?.email?.[0]?.toUpperCase() || "U") : "φ";
    const avClass = role === "user" ? "user-av" : "ai-av";

    if (role === "user") {
        return `<div class="chat-message user-msg">
            <div class="msg-bubble">${escapeHtml(text)}</div>
            <div class="msg-avatar ${avClass}">${initial}</div>
        </div>`;
    }

    const temp = document.createElement("div");
    temp.className = "msg-bubble";
    temp.innerHTML = renderMarkdown(text);
    _postProcessBubble(temp);

    return `<div class="chat-message bot-msg">
        <div class="msg-avatar ${avClass}">${initial}</div>
        ${temp.outerHTML}
    </div>`;
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

/* ── API Integrations (Chat, History, Files) ────────────────── */

async function createConversation() {
    const h = await getAuthHeaders();
    const res = await fetch(`${API_BASE}/conversation/create`, { method:"POST", headers:h, body:JSON.stringify({}) }).catch(()=>null);
    if (!res) return null;
    const d = await safeJson(res);
    if (res.ok && d.conversation_id) {
        activeConvId = d.conversation_id;
        loadHistory();
    }
    return d.conversation_id || null;
}

async function loadHistory() {
    const h = await getAuthHeaders();
    if (!h.Authorization) return;
    try {
        const res = await fetch(`${API_BASE}/history`, { method:"POST", headers:h });
        const d   = await safeJson(res);
        if (!res.ok || !Array.isArray(d)) { DOM.historyList.innerHTML = '<div class="empty-state">Could not load history.</div>'; return; }
        DOM.historyList.innerHTML = d.map(c => `<div class="history-item" data-id="${c.id}"><span class="history-title" onclick="openConversation('${c.id}')">${escapeHtml(c.title||"New Chat")}</span></div>`).join("");
    } catch {
        DOM.historyList.innerHTML = '<div class="empty-state">Network error.</div>';
    }
}

async function openConversation(id) {
    if (isProcessing) return;
    setProcessing(true);
    activeConvId = id; uploadedFiles = []; showChatMode(); DOM.chatDisplay.innerHTML = "";
    
    try {
        const h = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/conversation`, { method:"POST", headers:h, body:JSON.stringify({conversation_id:id}) });
        const msgs = await safeJson(res);
        if (Array.isArray(msgs) && msgs.length) {
            DOM.chatDisplay.innerHTML = msgs.map(m => _msgHTML(m.content, m.role==="user"?"user":"ai")).join("");
            DOM.chatDisplay.scrollTop = DOM.chatDisplay.scrollHeight;
        } else {
            DOM.chatDisplay.innerHTML = '<div class="empty-state">No messages yet.</div>';
        }
        DOM.sidebar.classList.remove("open"); DOM.overlay.classList.remove("active");
    } catch { showToast("Failed to load conversation.","error"); }
    finally { setProcessing(false); }
}

async function processFile(file) {
    const form = new FormData(); form.append("file", file);
    const s = await getSession();
    try {
        const res = await fetch(`${API_BASE}/analyze`, { method:"POST", headers:{"Authorization":"Bearer " + s.access_token}, body: form });
        const d = await safeJson(res);
        if (!res.ok || !d.success) { showToast(d.error || `Could not process ${file.name}`, "error"); return null; }
        showToast(`✓ ${file.name} analyzed`);
        return { document_id: d.document_id, document_text: d.document_text, name: file.name };
    } catch { return null; }
}

async function handleSend() {
    if (isProcessing) return;

    let text = DOM.userInput.value.trim();
    if (!text && uploadedFiles.length > 0) text = "Please read my uploaded medical report and explain the findings.";
    if (!text) return;

    setProcessing(true);

    if (!activeConvId) {
        const c = await createConversation();
        if (!c) { setProcessing(false); return; }
    }

    showChatMode();
    DOM.userInput.value = ""; autoGrow(DOM.userInput);

    let documentContents = [];
    if (uploadedFiles.length > 0) {
        const proc = appendMessage(`Analyzing ${uploadedFiles.length} document(s)…`, "ai");
        documentContents = (await Promise.all(uploadedFiles.map(processFile))).filter(Boolean);
        proc.remove();
        uploadedFiles = [];
        DOM.filePreview.innerHTML = ""; DOM.filePreview.classList.remove("visible");
    }

    appendMessage(text, "user");
    const botRow = appendTyping();

    try {
        const h = await getAuthHeaders();
        const res = await fetch(`${API_BASE}/chat`, {
            method:"POST", headers:h,
            body: JSON.stringify({
                conversation_id: activeConvId,
                message:         text,
                has_documents:   documentContents.length > 0,
                document_id:     documentContents[0]?.document_id || "",
                document_text:   documentContents[0]?.document_text || "",
            }),
        });
        const d = await safeJson(res);
        if (d.error && !d.reply) updateMessage(botRow, `Something went wrong: ${d.error}`);
        else updateMessage(botRow, d.reply || "I couldn't process that.");
    } catch (err) {
        updateMessage(botRow, "Connection error. Please check your network.");
    } finally {
        setProcessing(false);
    }
}

function sendChip(text) {
    if (isProcessing) return;
    DOM.userInput.value = text;
    handleSend();
}
window.sendChip = sendChip;

/* ── DOM & Events ────────────────────────────────────────────── */

function wireEvents() {
    DOM.mobileMenu?.addEventListener("click", () => { DOM.sidebar.classList.add("open"); DOM.overlay.classList.add("active"); });
    DOM.overlay?.addEventListener("click", () => { DOM.sidebar.classList.remove("open"); DOM.overlay.classList.remove("active"); });
    DOM.newChatBtn?.addEventListener("click", () => { activeConvId=null; showWelcomeMode(); DOM.chatDisplay.innerHTML=""; DOM.userInput.focus(); });
    DOM.sendBtn?.addEventListener("click", e => { e.preventDefault(); handleSend(); });
    DOM.userInput?.addEventListener("keydown", e => { if (e.key==="Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } });
    DOM.userInput?.addEventListener("input", () => autoGrow(DOM.userInput));
    DOM.attachBtn?.addEventListener("click", () => DOM.fileInput?.click());
    DOM.fileInput?.addEventListener("change", e => {
        Array.from(e.target.files||[]).forEach(f => uploadedFiles.push(f));
        DOM.filePreview.classList.add("visible");
        DOM.filePreview.innerHTML = uploadedFiles.map((f,i) => `<div class="file-chip"><span>${escapeHtml(f.name)}</span></div>`).join("");
        DOM.fileInput.value = "";
    });
    DOM.profileBtn?.addEventListener("click", e => { e.stopPropagation(); DOM.profileDrop?.classList.toggle("hidden"); });
    document.addEventListener("click", e => { if (!DOM.profileDrop?.contains(e.target) && e.target !== DOM.profileBtn) DOM.profileDrop?.classList.add("hidden"); });
    DOM.logoutBtn?.addEventListener("click", async () => { await supabaseClient.auth.signOut(); window.location.href="/login"; });
}

document.addEventListener("DOMContentLoaded", async () => {
    buildDOM();
    try { await initSupabase(); } catch { return; }
    const session = await getSession();
    if (!session?.user) { window.location.href = "/login"; return; }
    wireEvents();
    await handleLoginSuccess(session.user);
});