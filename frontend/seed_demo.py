"""
seed_demo.py
─────────────────────────────────────────────────────────────────────────────
Run this script ONCE to seed a demo user with realistic health data.

Usage:
    python seed_demo.py

Requirements:
  - .env file with SUPABASE_URL and SUPABASE_SERVICE_KEY (service role key)
  - pip install supabase python-dotenv

What this creates:
  - Demo user account (email: demo@curabook.ai, password: DemoUser2025!)
  - 3 report cycles with realistic health markers showing trends
  - Conversation memories simulating past PHI interactions
  - Consents pre-accepted
  - Profile set up

After running this, log in as demo@curabook.ai and every feature works.
─────────────────────────────────────────────────────────────────────────────
"""

import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
# Must use SERVICE ROLE KEY to create auth users and bypass RLS
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
    sys.exit(1)

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

DEMO_EMAIL    = "demo@curabook.ai"
DEMO_PASSWORD = "DemoUser2025!"

# ── Realistic demo health data (3 report cycles showing trends) ──────────────
# Report dates: 9 months ago, 5 months ago, 6 weeks ago
today = date.today()
DATE_1 = (today - timedelta(days=270)).isoformat()  # 9 months ago
DATE_2 = (today - timedelta(days=150)).isoformat()  # 5 months ago
DATE_3 = (today - timedelta(days=45)).isoformat()   # 6 weeks ago

# Complete marker dataset showing a real clinical story:
# - LDL rising over time (concerning trend)
# - HbA1c borderline then improving (positive response to intervention)
# - Vitamin D deficient then recovering
# - Hemoglobin low (iron deficiency)
# - TSH, Creatinine stable normal

DEMO_MARKERS = [
    # ── Report 1 (9 months ago) ──────────────────────────────────────────────
    {"marker_name": "LDL Cholesterol",      "value": 142,  "unit": "mg/dL", "reference_range": "<100",      "status": "HIGH",    "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "HDL Cholesterol",      "value": 38,   "unit": "mg/dL", "reference_range": ">40",       "status": "LOW",     "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Total Cholesterol",    "value": 198,  "unit": "mg/dL", "reference_range": "<200",      "status": "NORMAL",  "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Triglycerides",        "value": 165,  "unit": "mg/dL", "reference_range": "<150",      "status": "HIGH",    "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "HbA1c",               "value": 6.1,  "unit": "%",     "reference_range": "<5.7",      "status": "HIGH",    "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Fasting Blood Glucose","value": 112,  "unit": "mg/dL", "reference_range": "70-100",    "status": "HIGH",    "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Vitamin D (25-OH)",    "value": 14,   "unit": "ng/mL", "reference_range": "30-100",    "status": "LOW",     "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Vitamin B12",          "value": 310,  "unit": "pg/mL", "reference_range": "200-900",   "status": "NORMAL",  "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Hemoglobin",           "value": 10.8, "unit": "g/dL",  "reference_range": "12.0-15.5", "status": "LOW",    "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Ferritin",             "value": 8,    "unit": "ng/mL", "reference_range": "15-150",    "status": "LOW",     "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "TSH",                  "value": 2.8,  "unit": "mIU/L", "reference_range": "0.4-4.0",   "status": "NORMAL", "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "Creatinine",           "value": 0.82, "unit": "mg/dL", "reference_range": "0.6-1.2",   "status": "NORMAL", "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "eGFR",                 "value": 94,   "unit": "mL/min","reference_range": ">60",       "status": "NORMAL",  "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "ALT",                  "value": 35,   "unit": "U/L",   "reference_range": "7-40",      "status": "NORMAL",  "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},
    {"marker_name": "AST",                  "value": 28,   "unit": "U/L",   "reference_range": "10-40",     "status": "NORMAL",  "date": DATE_1, "source_document": "SRL_Labs_Report_Jan.pdf"},

    # ── Report 2 (5 months ago) — HbA1c improving, LDL still rising ──────────
    {"marker_name": "LDL Cholesterol",      "value": 158,  "unit": "mg/dL", "reference_range": "<100",      "status": "HIGH",    "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "HDL Cholesterol",      "value": 41,   "unit": "mg/dL", "reference_range": ">40",       "status": "NORMAL",  "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Total Cholesterol",    "value": 218,  "unit": "mg/dL", "reference_range": "<200",      "status": "HIGH",    "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Triglycerides",        "value": 148,  "unit": "mg/dL", "reference_range": "<150",      "status": "NORMAL",  "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "HbA1c",               "value": 5.9,  "unit": "%",     "reference_range": "<5.7",      "status": "HIGH",    "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Fasting Blood Glucose","value": 104,  "unit": "mg/dL", "reference_range": "70-100",    "status": "HIGH",    "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Vitamin D (25-OH)",    "value": 22,   "unit": "ng/mL", "reference_range": "30-100",    "status": "LOW",     "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Hemoglobin",           "value": 11.4, "unit": "g/dL",  "reference_range": "12.0-15.5", "status": "LOW",    "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Ferritin",             "value": 12,   "unit": "ng/mL", "reference_range": "15-150",    "status": "LOW",     "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "TSH",                  "value": 3.1,  "unit": "mIU/L", "reference_range": "0.4-4.0",   "status": "NORMAL", "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},
    {"marker_name": "Creatinine",           "value": 0.79, "unit": "mg/dL", "reference_range": "0.6-1.2",   "status": "NORMAL", "date": DATE_2, "source_document": "Thyrocare_Report_May.pdf"},

    # ── Report 3 (6 weeks ago — most recent) ─────────────────────────────────
    # Story: LDL still high and rising (needs intervention), Vitamin D improving on supplements,
    #        HbA1c coming down (diet working), Hemoglobin recovering, Iron almost normal
    {"marker_name": "LDL Cholesterol",      "value": 172,  "unit": "mg/dL", "reference_range": "<100",      "status": "HIGH",    "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "HDL Cholesterol",      "value": 44,   "unit": "mg/dL", "reference_range": ">40",       "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Total Cholesterol",    "value": 228,  "unit": "mg/dL", "reference_range": "<200",      "status": "HIGH",    "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Triglycerides",        "value": 138,  "unit": "mg/dL", "reference_range": "<150",      "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "HbA1c",               "value": 5.6,  "unit": "%",     "reference_range": "<5.7",      "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Fasting Blood Glucose","value": 96,   "unit": "mg/dL", "reference_range": "70-100",    "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Vitamin D (25-OH)",    "value": 32,   "unit": "ng/mL", "reference_range": "30-100",    "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Vitamin B12",          "value": 420,  "unit": "pg/mL", "reference_range": "200-900",   "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Hemoglobin",           "value": 11.9, "unit": "g/dL",  "reference_range": "12.0-15.5", "status": "LOW",    "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Ferritin",             "value": 18,   "unit": "ng/mL", "reference_range": "15-150",    "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "TSH",                  "value": 2.6,  "unit": "mIU/L", "reference_range": "0.4-4.0",   "status": "NORMAL", "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Creatinine",           "value": 0.81, "unit": "mg/dL", "reference_range": "0.6-1.2",   "status": "NORMAL", "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "eGFR",                 "value": 96,   "unit": "mL/min","reference_range": ">60",       "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "ALT",                  "value": 38,   "unit": "U/L",   "reference_range": "7-40",      "status": "NORMAL",  "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "CRP",                  "value": 3.2,  "unit": "mg/L",  "reference_range": "<3.0",      "status": "HIGH",    "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
    {"marker_name": "Uric Acid",            "value": 5.8,  "unit": "mg/dL", "reference_range": "2.4-6.0",   "status": "NORMAL", "date": DATE_3, "source_document": "Apollo_Diagnostics_Report_Recent.pdf"},
]

# Conversation memories — simulate facts PHI learned from past conversations
DEMO_MEMORIES = [
    "User started Vitamin D 2000 IU supplements 5 months ago on doctor's advice",
    "User has family history of heart disease — father had bypass surgery at 58",
    "User reports fatigue and occasional dizziness, especially in the afternoons",
    "User reduced refined carbs and sugar 4 months ago to control blood sugar",
    "User has upcoming cardiology appointment scheduled next month for LDL review",
    "User is vegetarian — no red meat, some dairy and eggs",
    "User exercises 3 times a week (walking 30 min), wants to increase intensity",
    "User is concerned about cholesterol trajectory and asked about statins",
]


def seed():
    print("🌱 Seeding Curabook PHI demo user...")

    # ── Step 1: Create demo auth user ─────────────────────────────────────────
    demo_user_id = None
    try:
        res = sb.auth.admin.create_user({
            "email":           DEMO_EMAIL,
            "password":        DEMO_PASSWORD,
            "email_confirm":   True,
            "user_metadata":   {"first_name": "Demo", "last_name": "User"},
        })
        demo_user_id = res.user.id
        print(f"✅ Demo user created: {DEMO_EMAIL} (id: {demo_user_id[:8]}…)")
    except Exception as e:
        if "already been registered" in str(e) or "already exists" in str(e).lower():
            # User exists — fetch their ID
            try:
                users = sb.auth.admin.list_users()
                for u in users:
                    if u.email == DEMO_EMAIL:
                        demo_user_id = u.id
                        print(f"ℹ️  Demo user already exists: {DEMO_EMAIL} (id: {demo_user_id[:8]}…)")
                        break
            except Exception as e2:
                print(f"❌ Could not fetch existing demo user: {e2}")
                return
        else:
            print(f"❌ Error creating demo user: {e}")
            return

    if not demo_user_id:
        print("❌ Could not determine demo user ID")
        return

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    # ── Step 2: User profile ──────────────────────────────────────────────────
    try:
        sb.table("user_profiles").upsert({
            "user_id":    demo_user_id,
            "first_name": "Demo",
            "last_name":  "User",
            "age":        34,
            "gender":     "female",
            "plan":       "pro",
            "reports_remaining": 9999,
            "updated_at": now,
        }, on_conflict="user_id").execute()
        print("✅ Profile created")
    except Exception as e:
        print(f"⚠️  Profile: {e}")

    # ── Step 3: Consents ──────────────────────────────────────────────────────
    for consent_type in ["data_processing", "ai_processing", "document_processing"]:
        try:
            sb.table("user_consents").upsert({
                "user_id":        demo_user_id,
                "consent_type":   consent_type,
                "consent_version":"v2.0",
                "is_active":      True,
                "granted_at":     now,
            }, on_conflict="user_id,consent_type").execute()
        except Exception as e:
            print(f"⚠️  Consent {consent_type}: {e}")
    print("✅ Consents set")

    # ── Step 4: Health markers (clear existing demo data first) ───────────────
    try:
        sb.table("health_markers").delete().eq("user_id", demo_user_id).execute()
    except Exception:
        pass

    inserted = 0
    for m in DEMO_MARKERS:
        try:
            sb.table("health_markers").insert({
                "user_id":         demo_user_id,
                "marker_name":     m["marker_name"],
                "value":           m["value"],
                "unit":            m["unit"],
                "reference_range": m["reference_range"],
                "status":          m["status"],
                "date":            m["date"],
                "source_document": m["source_document"],
                "created_at":      now,
            }).execute()
            inserted += 1
        except Exception as e:
            print(f"⚠️  Marker {m['marker_name']} {m['date']}: {e}")
    print(f"✅ {inserted}/{len(DEMO_MARKERS)} health markers inserted")

    # ── Step 5: Conversation memories ─────────────────────────────────────────
    try:
        sb.table("conversation_memories").delete().eq("user_id", demo_user_id).execute()
    except Exception:
        pass

    mem_inserted = 0
    for fact in DEMO_MEMORIES:
        try:
            sb.table("conversation_memories").insert({
                "user_id":    demo_user_id,
                "fact":       fact,
                "category":   "general",
                "is_active":  True,
                "created_at": now,
            }).execute()
            mem_inserted += 1
        except Exception as e:
            print(f"⚠️  Memory: {e}")
    print(f"✅ {mem_inserted}/{len(DEMO_MEMORIES)} conversation memories inserted")

    # ── Step 6: Seed a demo conversation ─────────────────────────────────────
    try:
        conv = sb.table("conversations").insert({
            "user_id":    demo_user_id,
            "title":      "📄 Apollo Diagnostics Report — LDL & Cholesterol Review",
            "created_at": now,
        }).execute()
        conv_id = conv.data[0]["id"]

        sb.table("chats").insert([
            {
                "user_id":         demo_user_id,
                "conversation_id": conv_id,
                "role":            "user",
                "content":         "Can you explain my latest cholesterol report?",
                "created_at":      now,
            },
            {
                "user_id":         demo_user_id,
                "conversation_id": conv_id,
                "role":            "assistant",
                "content": (
                    "**What this means for you**\n"
                    "Your LDL has risen from 142 in January to 172 mg/dL now — "
                    "a 21% increase over 9 months. This is the most important finding.\n\n"
                    "**What stands out**\n"
                    "• LDL: 172 mg/dL — HIGH (target <100). Rising trend across 3 reports.\n"
                    "• CRP: 3.2 mg/L — slightly elevated, suggests low-grade inflammation.\n"
                    "• Hemoglobin: 11.9 g/dL — still below normal, improving from 10.8.\n\n"
                    "**What to do next**\n"
                    "1. Your cardiology appointment next month is exactly the right step.\n"
                    "2. The rising LDL trend with your family history makes this a priority discussion.\n"
                    "3. Continue the low-carb diet — your HbA1c improved from 6.1% to 5.6%, that's working.\n\n"
                    "**One thing to know**\n"
                    "Your HbA1c and Vitamin D are both now normal — those interventions worked. "
                    "The LDL is the remaining focus.\n\n"
                    "---\n"
                    "⚕️ *PHI provides health information only — not medical advice.*"
                ),
                "created_at": now,
            },
        ]).execute()
        print("✅ Demo conversation seeded")
    except Exception as e:
        print(f"⚠️  Demo conversation: {e}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("🎉 DEMO USER READY")
    print("="*60)
    print(f"   Email:    {DEMO_EMAIL}")
    print(f"   Password: {DEMO_PASSWORD}")
    print(f"   User ID:  {demo_user_id}")
    print("\n   Health data loaded:")
    print(f"   • {inserted} markers across 3 reports (9 months of history)")
    print(f"   • {mem_inserted} conversation memories")
    print( "   • Rising LDL trend (142→158→172 mg/dL)")
    print( "   • HbA1c improved (6.1%→5.9%→5.6% — now normal)")
    print( "   • Vitamin D recovered (14→22→32 ng/mL — now normal)")
    print( "   • Hemoglobin improving (10.8→11.4→11.9 g/dL — still low)")
    print("\n   PHI will show full intelligence immediately on login.")
    print("="*60)


if __name__ == "__main__":
    seed()