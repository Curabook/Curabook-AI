"""
app.py — Safety-hardened & Route-Synchronized
FIXES IN THIS VERSION:
  - Maintenance mode enforcement (was stored but never checked)
  - PayPal payment blueprint registered (replaces Razorpay)
  - payment_routes now always loaded (was optional try/except)
  - retention_bp registered (was missing, caused /api/appointment-prep 404)
  - Demo mode removed — demo_routes.py deleted
  - Worker count capped at 4 to avoid Render free-tier OOM
"""

import os
import time
from threading import Lock
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
print("🚀 Using environment variables")

SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

if not ENCRYPTION_KEY:
    raise EnvironmentError("❌ ENCRYPTION_KEY is not set in .env")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError("❌ Missing SUPABASE_URL / SUPABASE_KEY in .env")

# Service role key — needed for admin operations (account deletion, etc.)
SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY") or
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or
    os.getenv("SUPABASE_SECRET_KEY") or
    os.getenv("SUPABASE_ADMIN_KEY") or
    os.getenv("SERVICE_ROLE_KEY") or
    os.getenv("SUPABASE_SERVICE_KEY_SECRET")
)
if not SUPABASE_SERVICE_KEY:
    print("⚠️ No service role key — falling back to anon key.")
    SUPABASE_SERVICE_KEY = SUPABASE_KEY

_openai_key = os.getenv("OPENAI_API_KEY")
_groq_key   = os.getenv("GROQ_API_KEY")

if _openai_key: print("✅ OpenAI configured (primary AI)")
elif _groq_key: print("✅ Groq configured")
else:           print("⚠️ No AI key set — chat will not work")

# PayPal config check
_paypal_client_id = os.getenv("PAYPAL_CLIENT_ID")
if _paypal_client_id:
    _paypal_env = os.getenv("PAYPAL_ENV", "sandbox")
    print(f"✅ PayPal configured ({_paypal_env} mode)")
else:
    print("⚠️ PAYPAL_CLIENT_ID not set — payment endpoints will return 503")

try:
    from groq import Groq
    groq_client = Groq(api_key=_groq_key) if _groq_key else None
except Exception:
    groq_client = None

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
print("✅ Supabase ready")

# ── Rate limiter ──────────────────────────────────────────────────────────────
_worker_count = min(int(os.getenv("WORKER_COUNT", "1")), 2)  # cap at 2 — Render free tier is 512MB; 4 workers OOM-kills the process causing CORS-less 502s on /chat
from services.rate_limiter import get_rate_limiter
_limiter = get_rate_limiter()

RATE_LIMITS = {
    "/chat":                                   (20,  60),
    "/conversation/create":                    (10,  60),
    "/history":                                (120, 60),
    "/analyze":                                (10,  60),
    "/api/payment/paypal/create-subscription": (5,   60),
}

def get_client_key(route: str) -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    return f"{ip.split(',')[0].strip()}:{route}"

_stats = {"requests_total": 0, "rate_limited": 0, "errors_500": 0, "start_time": time.time()}
_stats_lock = Lock()

# Maintenance mode cache — checked at most once per 30s to avoid DB hammering
_maintenance_cache = {"enabled": False, "last_check": 0.0}
_MAINTENANCE_CACHE_TTL = 30  # seconds


def track(key: str, inc: int = 1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + inc


def _is_maintenance_mode() -> bool:
    """Check maintenance mode with 30s cache to avoid hitting DB on every request."""
    global _maintenance_cache
    now = time.time()
    if now - _maintenance_cache["last_check"] < _MAINTENANCE_CACHE_TTL:
        return _maintenance_cache["enabled"]
    try:
        cfg = supabase.table("app_config").select("value").eq("key", "maintenance_mode").limit(1).execute()
        enabled = bool(cfg.data and cfg.data[0].get("value") == "true")
        _maintenance_cache = {"enabled": enabled, "last_check": now}
        return enabled
    except Exception:
        # DB error — don't enforce maintenance (fail open)
        _maintenance_cache["last_check"] = now  # Still update timestamp to avoid hammering
        return False


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
_allowed_origins = ["*"]
CORS(
    app,
    resources={r"/*": {"origins": _allowed_origins}},
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization", "X-Demo-Session", "X-Founder-Secret"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

def _apply_cors(response):
    origin = request.headers.get("Origin", "")
    if origin:
        response.headers["Access-Control-Allow-Origin"]      = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    else:
        response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Demo-Session, X-Founder-Secret"
    )
    return response

@app.before_request
def handle_options_preflight():
    if request.method == "OPTIONS":
        resp = make_response("", 200)
        _apply_cors(resp)
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

@app.after_request
def add_security_headers(response):
    _apply_cors(response)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-XSS-Protection"]       = "1; mode=block"
    return response

@app.before_request
def check_maintenance_mode():
    """
    FIX: Maintenance mode was stored in DB but NEVER enforced on requests.
    Now checked on every non-exempt request with a 30s cache.
    """
    # Skip CORS preflight
    if request.method == "OPTIONS":
        return None

    path = request.path

    # Exempt paths: health check, stats, founder endpoints
    _EXEMPT_PATHS = {"/health", "/api/stats", "/"}
    _EXEMPT_PREFIXES = ("/api/founder/",)

    if path in _EXEMPT_PATHS:
        return None
    if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return None

    if not _is_maintenance_mode():
        return None

    # Maintenance is ON — allow founders through
    founder_secret = os.getenv("FOUNDER_SECRET", "")
    if founder_secret and request.headers.get("X-Founder-Secret") == founder_secret:
        return None

    # Block everyone else with a clear maintenance message
    resp = jsonify({
        "error":       "maintenance",
        "message":     "Curabook PHI is currently undergoing maintenance. Please check back shortly.",
        "maintenance": True,
    })
    resp.status_code = 503
    return _apply_cors(resp)


@app.before_request
def check_rate_limit():
    track("requests_total")
    route        = request.path
    limit_config = RATE_LIMITS.get(route)
    if limit_config:
        limit, window = limit_config
        if not _limiter.is_allowed(get_client_key(route), limit, window):
            track("rate_limited")
            resp = jsonify({"error": "Too many requests. Please slow down.", "retry_after": window})
            resp.status_code = 429
            return _apply_cors(resp)

# ── Background workers ────────────────────────────────────────────────────────
from services.job_queue import init_workers
init_workers(num_workers=_worker_count)

# ── Blueprint registration ────────────────────────────────────────────────────
from api.auth_routes         import auth_bp
from api.chat_routes         import chat_bp
from api.document_routes     import document_bp
from api.health_routes       import health_bp
from api.compliance_routes   import compliance_bp
from api.profile_routes      import profile_bp
from api.intelligence_routes import intelligence_bp
from api.cron_routes         import cron_bp
from api.startup_routes      import startup_bp
from api.retention_routes    import retention_bp
from api.analytics_routes    import analytics_bp

app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(document_bp)
app.register_blueprint(health_bp)
app.register_blueprint(compliance_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(intelligence_bp)
app.register_blueprint(cron_bp)
app.register_blueprint(startup_bp)
app.register_blueprint(retention_bp)
app.register_blueprint(analytics_bp)

# Payment routes — PayPal (required, not optional)
from api.payment_routes import payment_bp
app.register_blueprint(payment_bp)
print("✅ PayPal payment routes registered")

# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(429)
def too_many_requests(e):
    resp = jsonify({"error": "Too many requests."})
    resp.status_code = 429
    return _apply_cors(resp)

@app.errorhandler(404)
def not_found(e):
    resp = jsonify({"error": "Not found"})
    resp.status_code = 404
    return _apply_cors(resp)

@app.errorhandler(413)
def too_large(e):
    resp = jsonify({"error": "File too large. Maximum 20MB."})
    resp.status_code = 413
    return _apply_cors(resp)

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    traceback.print_exc()
    track("errors_500")
    resp = jsonify({"error": "Something went wrong. Please try again."})
    resp.status_code = 500
    return _apply_cors(resp)

# ── Utility routes ────────────────────────────────────────────────────────────
@app.route("/debug/routes")
def debug_routes():
    routes = [(r.rule, sorted(r.methods - {"HEAD", "OPTIONS"})) for r in app.url_map.iter_rules()]
    return jsonify(sorted(routes, key=lambda x: x[0]))

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status":      "healthy",
        "openai":      bool(_openai_key),
        "groq":        bool(_groq_key),
        "paypal":      bool(_paypal_client_id),
        "workers":     _worker_count,
        "maintenance": _maintenance_cache.get("enabled", False),
    })

@app.route("/api/stats", methods=["GET"])
def api_stats():
    from services.job_queue import queue_stats
    return jsonify({
        **_stats,
        "uptime_seconds": int(time.time() - _stats["start_time"]),
        "queue":          queue_stats(),
        "maintenance":    _maintenance_cache.get("enabled", False),
    })

@app.route("/")
def home():
    return {"status": "Curabook PHI backend running 🚀"}

@app.route("/api/payment/config", methods=["GET"])
def payment_config_fallback():
    """
    Fallback for /api/payment/config in case payment_routes blueprint fails to load.
    Also handles the case where PAYPAL_CLIENT_ID is missing — returns paypal_configured:false
    so the frontend skips SDK load gracefully instead of crashing with JSON parse error.
    """
    client_id = os.getenv("PAYPAL_CLIENT_ID", "")
    return jsonify({
        "paypal_configured": bool(client_id),
        "paypal_client_id":  client_id,
        "paypal_env":        os.getenv("PAYPAL_ENV", "sandbox"),
        "plans": {
            "monthly":  {"amount": 49,  "currency": "USD", "label": "Shield — $49/mo",                   "interval": "monthly"},
            "annual":   {"amount": 468, "currency": "USD", "label": "Shield — $39/mo (billed annually)", "interval": "annual"},
            "clinical": {"amount": 99,  "currency": "USD", "label": "Shield Clinical — $99/mo",          "interval": "monthly"},
        },
        "trial_days": int(os.getenv("TRIAL_DAYS", "7")),
    })

class _CORSMiddleware:
    """
    WSGI middleware that stamps Access-Control-Allow-Origin on EVERY response,
    including gunicorn 502/503/timeout error pages that bypass Flask entirely.
    This is what makes CORS work even when a worker crashes mid-request.
    """
    def __init__(self, wsgi_app):
        self.app = wsgi_app

    def __call__(self, environ, start_response):
        origin = environ.get("HTTP_ORIGIN", "*")

        def _start(status, headers, exc_info=None):
            headers = [(k, v) for k, v in headers
                       if k.lower() != "access-control-allow-origin"]
            headers += [
                ("Access-Control-Allow-Origin",      origin),
                ("Access-Control-Allow-Credentials", "true"),
                ("Access-Control-Allow-Methods",     "GET, POST, PUT, DELETE, OPTIONS"),
                ("Access-Control-Allow-Headers",
                 "Content-Type, Authorization, X-Demo-Session, X-Founder-Secret"),
            ]
            return start_response(status, headers, exc_info)

        return self.app(environ, _start)

app.wsgi_app = _CORSMiddleware(app.wsgi_app)

if __name__ == "__main__":
    print("🚀 Curabook PHI starting…")
    app.run(port=5000, debug=False)