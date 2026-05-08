"""
app.py — Safety-hardened & Route-Synchronized
CHANGES FROM PREVIOUS VERSION:
  - Razorpay payment blueprint registered (replaces Stripe)
  - payment_routes now always loaded (was optional try/except)
  - retention_bp registered (was missing, caused /api/appointment-prep 404)
  - demo_routes registered conditionally (DEMO_MODE env var)
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

SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
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

# Razorpay config check
_razorpay_key = os.getenv("RAZORPAY_KEY_ID")
if _razorpay_key:
    print(f"✅ Razorpay configured ({'live' if 'live' in _razorpay_key else 'test'} mode)")
else:
    print("⚠️ RAZORPAY_KEY_ID not set — payment endpoints will return 503")

try:
    from groq import Groq
    groq_client = Groq(api_key=_groq_key) if _groq_key else None
except Exception:
    groq_client = None

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
print("✅ Supabase ready")

# ── Rate limiter ──────────────────────────────────────────────────────────────
_worker_count = min(int(os.getenv("WORKER_COUNT", "1")), 4)  # cap at 4
from services.rate_limiter import get_rate_limiter
_limiter = get_rate_limiter()

RATE_LIMITS = {
    "/chat":                (20, 60),
    "/conversation/create": (10, 60),
    "/history":             (120, 60),
    "/analyze":             (10, 60),
    "/api/payment/razorpay/order": (5, 60),
}

def get_client_key(route: str) -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    return f"{ip.split(',')[0].strip()}:{route}"

_stats = {"requests_total": 0, "rate_limited": 0, "errors_500": 0, "start_time": time.time()}
_stats_lock = Lock()

def track(key: str, inc: int = 1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + inc

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
_allowed_origins = ["*"]
CORS(
    app,
    resources={r"/*": {"origins": _allowed_origins}},
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization", "X-Demo-Session"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

def _apply_cors(response):
    origin = request.headers.get("Origin", "")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    else:
        response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Demo-Session"
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
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

@app.before_request
def check_rate_limit():
    track("requests_total")
    route = request.path
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
from api.retention_routes    import retention_bp  # was missing — caused 404 on appointment prep
from api.analytics_routes import analytics_bp

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


# Payment routes — Razorpay (required, not optional)
from api.payment_routes import payment_bp
app.register_blueprint(payment_bp)
print("✅ Razorpay payment routes registered")

# Demo routes — only if DEMO_MODE=true
if os.getenv("DEMO_MODE", "false").lower() == "true":
    try:
        from api.demo_routes import demo_bp
        app.register_blueprint(demo_bp)
        print("✅ Demo mode enabled")
    except ImportError:
        print("⚠️ Demo routes not found")

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
        "status":   "healthy",
        "openai":   bool(_openai_key),
        "groq":     bool(_groq_key),
        "razorpay": bool(_razorpay_key),
        "workers":  _worker_count,
    })

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Internal stats endpoint for monitoring."""
    from services.job_queue import queue_stats
    return jsonify({
        **_stats,
        "uptime_seconds": int(time.time() - _stats["start_time"]),
        "queue": queue_stats(),
    })

@app.route("/")
def home():
    return {"status": "Curabook PHI backend running 🚀"}

if __name__ == "__main__":
    print("🚀 Curabook PHI starting…")
    app.run(port=5000, debug=False)