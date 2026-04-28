import os
import time
from collections import defaultdict
from threading import Lock
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv()
print("🚀 Using environment variables")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise EnvironmentError(
        "❌  ENCRYPTION_KEY is not set in .env\n"
        "    Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "    Then add to .env: ENCRYPTION_KEY=<your_generated_key>\n"
        "    Never use auto-generated keys — they change on every restart."
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
    print("⚠️  No service role key — falling back to anon key.")
    SUPABASE_SERVICE_KEY = SUPABASE_KEY

# ── AI clients ────────────────────────────────────────────────────────────────
_openai_key = os.getenv("OPENAI_API_KEY")

if _openai_key:
    print("✅  OpenAI configured (primary AI)")
else:
    print("⚠️  No AI key found. Add OPENAI_API_KEY to .env")

# ── Supabase singleton ────────────────────────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
print("✅  Supabase ready")

# ── Embedder — lazy loaded ────────────────────────────────────────────────────
# ── Embedder — DISABLED FOR SERVERLESS STABILITY ──────────────────────────────
def get_embedder():
    # In Vercel serverless environments, downloading 80MB models causes OOM crashes.
    # We disable it here to keep the API lightning fast.
    return None

embedder = None


# ── Rate limiter ──────────────────────────────────────────────────────────────
_worker_count = int(os.getenv("WORKER_COUNT", "1"))
if _worker_count > 1:
    print(
        f"⚠️  WARNING: Running with {_worker_count} workers but in-memory rate limiter.\n"
        f"   Rate limits will be {_worker_count}x higher than configured.\n"
        "   Set REDIS_URL for production."
    )

from services.rate_limiter import get_rate_limiter
_limiter = get_rate_limiter()

RATE_LIMITS = {
    "/chat":                       (20, 60),
    "/conversation/create":        (10, 60),
    "/history":                    (120, 60),
    "/analyze":                    (10, 60),
    "/demo/chat":                  (30, 60),
    "/demo/analyze":               (15, 60),
}

def get_client_key(route: str) -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    ip = ip.split(",")[0].strip()
    return f"{ip}:{route}"


# ── Monitoring stats ──────────────────────────────────────────────────────────
_stats: dict = {
    "requests_total": 0,
    "llm_calls":      0,
    "llm_errors":     0,
    "llm_timeouts":   0,
    "rate_limited":   0,
    "errors_500":     0,
    "errors_401":     0,
    "errors_403":     0,
    "safety_blocks":  0,
    "start_time":     time.time(),
}
_stats_lock = Lock()

def track(key: str, inc: int = 1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + inc


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

_allowed_origins = ["*"]
CORS(
    app,
    resources            = {r"/*": {"origins": _allowed_origins}},
    supports_credentials = True,
    allow_headers        = ["Content-Type", "Authorization", "X-Demo-Session"],
    methods              = ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
print(f"✅  CORS configured for: {_allowed_origins}")


def _apply_cors(response):
    origin = request.headers.get("Origin", "")
    if origin:
        response.headers["Access-Control-Allow-Origin"]      = origin
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
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]          = "DENY"
    response.headers["X-XSS-Protection"]         = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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
            resp = jsonify({
                "error":       "Too many requests. Please slow down.",
                "retry_after": window,
            })
            resp.status_code = 429
            _apply_cors(resp)
            return resp


from services.job_queue import init_workers
init_workers(num_workers=int(os.getenv("WORKER_COUNT", "1")))

from api.auth_routes         import auth_bp
from api.chat_routes         import chat_bp
from api.document_routes     import document_bp
from api.health_routes       import health_bp
from api.compliance_routes   import compliance_bp
from api.profile_routes      import profile_bp
from api.intelligence_routes import intelligence_bp
from api.cron_routes         import cron_bp
from api.startup_routes import startup_bp

app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(document_bp)
app.register_blueprint(health_bp)
app.register_blueprint(compliance_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(intelligence_bp)
app.register_blueprint(cron_bp)
app.register_blueprint(startup_bp)

try:
    from api.demo_routes import demo_bp
    app.register_blueprint(demo_bp)
    if os.getenv("DEMO_MODE", "false").lower() == "true":
        print("🧪  DEMO MODE ACTIVE — No real file uploads accepted in demo")
    else:
        print("ℹ️  Demo routes registered (DEMO_MODE=false)")
except Exception as _de:
    print(f"⚠️  Demo routes error: {_de}")

try:
    from api.payment_routes import payment_bp
    app.register_blueprint(payment_bp)
    print("✅  Payment routes ready")
except ImportError:
    print("ℹ️  Payment routes not active")

from api.retention_routes import retention_bp
app.register_blueprint(retention_bp)
print("✅  Retention routes ready")


@app.errorhandler(429)
def too_many_requests(e):
    resp = jsonify({"error": "Too many requests. Please slow down."})
    resp.status_code = 429
    _apply_cors(resp)
    return resp

@app.errorhandler(404)
def not_found(e):
    resp = jsonify({"error": "Not found"})
    resp.status_code = 404
    _apply_cors(resp)
    return resp

@app.errorhandler(413)
def too_large(e):
    resp = jsonify({"error": "File too large. Maximum 5MB."})
    resp.status_code = 413
    _apply_cors(resp)
    return resp

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    traceback.print_exc()
    track("errors_500")
    resp = jsonify({"error": "Something went wrong. Please try again."})
    resp.status_code = 500
    _apply_cors(resp)
    return resp


@app.route("/debug/routes")
def debug_routes():
    routes = [(r.rule, sorted(r.methods - {"HEAD", "OPTIONS"})) for r in app.url_map.iter_rules()]
    return jsonify(sorted(routes, key=lambda x: x[0]))


@app.route("/health", methods=["GET"])
def health_check():
    from services.compliance import check_baa_compliance
    return jsonify({
        "status":           "healthy",
        "openai":           bool(_openai_key),
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