"""
app.py — Safety-hardened
FIXES APPLIED:
  #H2  — ENCRYPTION_KEY is now a hard startup failure if not set
  #M5  — Removed duplicate init_workers() call
  #H5  — Rate limiter documented as single-process only; warning printed
  #H1  — /api/config moved to compliance_routes.py (auth-protected)
"""

import os
import secrets
import time
from collections import defaultdict
from threading import Lock
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# ── Load environment ──────────────────────────────────────────────────────────
_here     = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_here, '.env')
print("🚀 Using Render environment variables")

SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

# Fix #H2 — Hard fail on missing ENCRYPTION_KEY.
# NEVER generate randomly — would break decryption after every restart.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise EnvironmentError(
        "❌  ENCRYPTION_KEY is not set in .env\n"
        "    Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "    Then add to .env: ENCRYPTION_KEY=<your_generated_key>\n"
        "    Never use auto-generated keys — they change on every restart, "
        "breaking all stored encrypted data."
    )

if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError("❌  Missing SUPABASE_URL / SUPABASE_KEY in .env")

SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY") or
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or
    os.getenv("SUPABASE_SECRET_KEY") or
    os.getenv("SUPABASE_ADMIN_KEY") or
    os.getenv("SERVICE_ROLE_KEY") or
    os.getenv("SUPABASE_SERVICE_KEY_SECRET")
)
if not SUPABASE_SERVICE_KEY:
    print("⚠️  No service role key — falling back to anon key. Add SUPABASE_SERVICE_KEY to .env")
    SUPABASE_SERVICE_KEY = SUPABASE_KEY

# ── AI clients ────────────────────────────────────────────────────────────────
_openai_key = os.getenv("OPENAI_API_KEY")
_groq_key   = os.getenv("GROQ_API_KEY")

if _openai_key:
    print("✅  OpenAI configured (primary AI)")
elif _groq_key:
    print("✅  Groq configured (add OPENAI_API_KEY for better quality)")
else:
    print("⚠️  No AI key found. Add OPENAI_API_KEY to .env")

try:
    from groq import Groq
    groq_client = Groq(api_key=_groq_key) if _groq_key else None
except Exception:
    groq_client = None

# ── Supabase singleton ────────────────────────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
print("✅  Supabase ready")

# ── Embedder — lazy loaded ────────────────────────────────────────────────────
_embedder_instance = None
_embedder_lock = Lock()

def get_embedder():
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance
    with _embedder_lock:
        if _embedder_instance is None:
            try:
                from sentence_transformers import SentenceTransformer
                print("⏳  Loading embedding model…")
                _embedder_instance = SentenceTransformer("all-MiniLM-L6-v2")
                print("✅  Embedding model ready")
            except Exception as _e:
                print(f"ℹ️  Embedding model unavailable: {_e}")
    return _embedder_instance

embedder = None  # backward-compatible alias


# ══════════════════════════════════════════════════════════════════════════════
# Rate limiter
# Fix #H5 — documented as single-process only.
# For multi-worker deployments, replace with Redis-backed limiter.
# ══════════════════════════════════════════════════════════════════════════════

_worker_count = int(os.getenv("WORKER_COUNT", "1"))
if _worker_count > 1:
    print(
        f"⚠️  WARNING: Running with {_worker_count} workers but using in-memory rate limiter.\n"
        "   Rate limits will be {_worker_count}x higher than configured.\n"
        "   Add Redis and use flask-limiter with Redis storage for production."
    )


class _RateLimiter:
    """Sliding-window in-memory rate limiter. Thread-safe. Single-process only."""
    def __init__(self):
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now    = time.time()
        cutoff = now - window_seconds
        with self._lock:
            calls = [t for t in self._windows[key] if t > cutoff]
            if len(calls) >= limit:
                return False
            calls.append(now)
            self._windows[key] = calls
            return True

    def cleanup(self):
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._windows.items() if not v or now - max(v) > 300]
            for k in stale:
                del self._windows[k]


from services.rate_limiter import get_rate_limiter
_limiter = get_rate_limiter()

RATE_LIMITS = {
    "/chat":                      (20, 60),
    "/conversation/create":       (10, 60),
    "/history":                   (120, 60),
    "/analyze":                   (10, 60),
    "/api/v1/chat":               (20, 60),
    "/api/v1/conversation/create": (10, 60),
    "/demo/chat":                 (30, 60),
    "/demo/analyze":              (15, 60),
}


def get_client_key(route: str) -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    # Use first IP if X-Forwarded-For contains multiple (proxy chain)
    ip = ip.split(",")[0].strip()
    return f"{ip}:{route}"


# ══════════════════════════════════════════════════════════════════════════════
# Monitoring stats
# ══════════════════════════════════════════════════════════════════════════════

_stats: dict = {
    "requests_total": 0,
    "llm_calls":      0,
    "llm_errors":     0,
    "llm_timeouts":   0,
    "rate_limited":   0,
    "errors_500":     0,
    "errors_401":     0,
    "errors_403":     0,
    "safety_blocks":  0,   # New — tracks hallucination/output violations
    "start_time":     time.time(),
}
_stats_lock = Lock()


def track(key: str, inc: int = 1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + inc


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

_raw_origins    = os.getenv("ALLOWED_ORIGINS", "")
_allowed_origins = ["*"]
CORS(
    app,
    resources       = {r"/*": {"origins": _allowed_origins}},
    supports_credentials = True,
    allow_headers   = ["Content-Type", "Authorization", "X-Demo-Session"],
    methods         = ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
print(f"✅  CORS configured for: {_allowed_origins}")


# ── Security headers ──────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]          = "DENY"
    response.headers["X-XSS-Protection"]         = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Rate limiting middleware ──────────────────────────────────────────────────
@app.before_request
def check_rate_limit():
    track("requests_total")
    route        = request.path
    limit_config = RATE_LIMITS.get(route)
    if limit_config:
        limit, window = limit_config
        key = get_client_key(route)
        if not _limiter.is_allowed(key, limit, window):
            track("rate_limited")
            return jsonify({
                "error":       "Too many requests. Please slow down.",
                "retry_after": window,
            }), 429


# ── Blueprints ────────────────────────────────────────────────────────────────

# Fix #M5 — init_workers called ONCE only (was called twice in original)
from services.job_queue import init_workers
init_workers(num_workers=int(os.getenv("WORKER_COUNT", "1")))

from api.auth_routes       import auth_bp
from api.chat_routes       import chat_bp
from api.document_routes   import document_bp
from api.health_routes     import health_bp
from api.compliance_routes import compliance_bp
from api.profile_routes    import profile_bp

app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(document_bp)
app.register_blueprint(health_bp)
app.register_blueprint(compliance_bp)
app.register_blueprint(profile_bp)

# API versioning — same blueprints under /api/v1 (backward compatible)
from api.auth_routes       import auth_bp      as auth_v1
from api.chat_routes       import chat_bp      as chat_v1
from api.document_routes   import document_bp  as doc_v1
from api.health_routes     import health_bp    as health_v1
from api.compliance_routes import compliance_bp as comp_v1

app.register_blueprint(auth_v1,   url_prefix="/api/v1", name="auth_v1")
app.register_blueprint(chat_v1,   url_prefix="/api/v1", name="chat_v1")
app.register_blueprint(doc_v1,    url_prefix="/api/v1", name="doc_v1")
app.register_blueprint(health_v1, url_prefix="/api/v1", name="health_v1")
app.register_blueprint(comp_v1,   url_prefix="/api/v1", name="comp_v1")

# Demo mode
try:
    from api.demo_routes import demo_bp
    app.register_blueprint(demo_bp)
    if os.getenv("DEMO_MODE", "false").lower() == "true":
        print("🧪  DEMO MODE ACTIVE — No real file uploads accepted in demo")
    else:
        print("ℹ️  Demo routes registered (DEMO_MODE=false)")
except Exception as _de:
    print(f"⚠️  Demo routes error: {_de}")

# Payment
try:
    from api.payment_routes import payment_bp
    app.register_blueprint(payment_bp)
    print("✅  Payment routes ready")
except ImportError:
    print("ℹ️  Payment routes not active")


# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(429)
def too_many_requests(e):
    return jsonify({"error": "Too many requests. Please slow down."}), 429

@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large. Maximum 5MB."}), 413

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    traceback.print_exc()
    track("errors_500")
    return jsonify({"error": "Something went wrong. Please try again."}), 500


# ── Health + monitoring endpoints ─────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health_check():
    from services.compliance import check_baa_compliance
    return jsonify({
        "status":           "healthy",
        "openai":           bool(_openai_key),
        "groq":             bool(_groq_key),
        "supabase":         True,
        "rate_limiting":    True,
        "security_headers": True,
        "encryption_key":   bool(ENCRYPTION_KEY),
        "baa_signed":       check_baa_compliance(),
    })


@app.route("/api/v1/stats", methods=["GET"])
def monitoring_stats():
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    uptime = round(time.time() - _stats["start_time"])
    from services.job_queue import queue_stats
    return jsonify({**_stats, "uptime_seconds": uptime, "queue": queue_stats()})


@app.route("/")
def home():
    return {"status": "Curabook.com backend running 🚀"}


if __name__ == "__main__":
    print("🚀  Curabook PHI starting…")
    app.run(port=5000, debug=False)