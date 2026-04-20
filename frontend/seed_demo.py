"""
seed_demo.py  —  GLP-1 Cliff Edition
─────────────────────────────────────────────────────────────────────────────
Run this ONCE to seed a demo user with a realistic US GLP-1 cliff scenario.

Usage:
    python seed_demo.py

Requirements:
  - .env with SUPABASE_URL and SUPABASE_SERVICE_KEY (service role key)
  - pip install supabase python-dotenv

Demo Patient: Sarah M., 41-year-old US patient
Story:
  - Was on Zepbound 10mg weekly from May 2025 to December 2025 (7 months)
  - Lost 34 lbs — peak results in September 2025
  - Stopped in December 2025: insurance prior auth expired, denial
  - 8 weeks post-cessation (Feb 2026): classic cliff signals emerging
    • Glucose rebounding +23% from personal baseline
    • HbA1c back in prediabetes range (0.4% rise in 8 weeks)
    • LDL rising, CRP elevated, weight regaining
    • Food noise returned intensely
  - Goal: rebuild her case for insurance appeal, manage the cliff

This scenario triggers:
  ✓ Glucose rebound alert (>15% threshold in insights/engine.py)
  ✓ HbA1c rebound alert (≥0.25% threshold)
  ✓ High-intensity emotional layer (food noise + insurance anxiety)
  ✓ Advocacy brief generation (PA support packet)
  ✓ Muscle Defense Protocol (protein target calculation)
─────────────────────────────────────────────────────────────────────────────
"""

import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
    sys.exit(1)

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

DEMO_EMAIL    = "demo@curabook.com"
DEMO_PASSWORD = "DemoUser2025!"

# ── Timeline ──────────────────────────────────────────────────────────────────
# Sarah was on Zepbound May–December 2025, stopped 8 weeks ago
DATE_PEAK    = "2025-09-15"   # Best results while on Zepbound
DATE_LAST_ON = "2025-12-08"   # Final reading on medication
DATE_REBOUND = "2026-02-10"   # 8 weeks post-cessation — cliff is here

# ── Lab markers: three reports showing the complete clinical story ─────────────
#
# Report 1 (Sept 2025): Peak results on Zepbound — everything normalised
# Report 2 (Dec 2025):  Last reading on medication — held ground
# Report 3 (Feb 2026):  8 weeks post-cessation — rebound in every marker
#
# This is the exact cliff pattern documented in SURMOUNT-4 and STEP-10:
# glucose rises first (within 2-4 weeks), then HbA1c follows (2-3 months lag),
# then LDL and weight — and the cardiometabolic gains fully reverse in 1.4 years.

DEMO_MARKERS = [

    # ── Report 1: Peak on Zepbound (September 2025) ───────────────────────────
    {"marker_name": "Fasting Blood Glucose", "value": 91,   "unit": "mg/dL", "reference_range": "70-100",   "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "HbA1c",                 "value": 5.4,  "unit": "%",     "reference_range": "<5.7",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "LDL Cholesterol",       "value": 92,   "unit": "mg/dL", "reference_range": "<100",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "HDL Cholesterol",       "value": 54,   "unit": "mg/dL", "reference_range": ">40",      "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "Total Cholesterol",     "value": 178,  "unit": "mg/dL", "reference_range": "<200",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "Triglycerides",         "value": 108,  "unit": "mg/dL", "reference_range": "<150",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "CRP",                   "value": 0.8,  "unit": "mg/L",  "reference_range": "<3.0",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "Hemoglobin A1c",        "value": 5.4,  "unit": "%",     "reference_range": "<5.7",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "Insulin (Fasting)",     "value": 6.2,  "unit": "µIU/mL","reference_range": "2.6-24.9", "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "eGFR",                  "value": 94,   "unit": "mL/min","reference_range": ">60",      "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "ALT",                   "value": 22,   "unit": "U/L",   "reference_range": "7-40",     "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},
    {"marker_name": "TSH",                   "value": 2.1,  "unit": "mIU/L", "reference_range": "0.4-4.0",  "status": "NORMAL", "date": DATE_PEAK,    "source_document": "Quest_Diagnostics_Sep2025.pdf"},

    # ── Report 2: Last reading on Zepbound (December 2025) ────────────────────
    {"marker_name": "Fasting Blood Glucose", "value": 94,   "unit": "mg/dL", "reference_range": "70-100",   "status": "NORMAL", "date": DATE_LAST_ON, "source_document": "LabCorp_Dec2025.pdf"},
    {"marker_name": "HbA1c",                 "value": 5.5,  "unit": "%",     "reference_range": "<5.7",     "status": "NORMAL", "date": DATE_LAST_ON, "source_document": "LabCorp_Dec2025.pdf"},
    {"marker_name": "LDL Cholesterol",       "value": 98,   "unit": "mg/dL", "reference_range": "<100",     "status": "NORMAL", "date": DATE_LAST_ON, "source_document": "LabCorp_Dec2025.pdf"},
    {"marker_name": "Triglycerides",         "value": 122,  "unit": "mg/dL", "reference_range": "<150",     "status": "NORMAL", "date": DATE_LAST_ON, "source_document": "LabCorp_Dec2025.pdf"},
    {"marker_name": "CRP",                   "value": 1.1,  "unit": "mg/L",  "reference_range": "<3.0",     "status": "NORMAL", "date": DATE_LAST_ON, "source_document": "LabCorp_Dec2025.pdf"},
    {"marker_name": "Total Cholesterol",     "value": 182,  "unit": "mg/dL", "reference_range": "<200",     "status": "NORMAL", "date": DATE_LAST_ON, "source_document": "LabCorp_Dec2025.pdf"},

    # ── Report 3: 8 weeks post-cessation (February 2026) ─────────────────────
    # THE CLIFF IS HERE:
    # Glucose: 91 → 112 mg/dL = +23% (threshold is 15%) → 🚨 RED ALERT
    # HbA1c: 5.5% → 5.9% = +0.4% rise (threshold is 0.25%) → 🚨 RED ALERT
    # LDL: 98 → 124 mg/dL = back above optimal
    # Triglycerides: 122 → 171 mg/dL = crossing into HIGH
    # CRP: 1.1 → 4.2 mg/L = crossing into HIGH (inflammation rebounding)
    {"marker_name": "Fasting Blood Glucose", "value": 112,  "unit": "mg/dL", "reference_range": "70-100",   "status": "HIGH",   "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "HbA1c",                 "value": 5.9,  "unit": "%",     "reference_range": "<5.7",     "status": "HIGH",   "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "LDL Cholesterol",       "value": 124,  "unit": "mg/dL", "reference_range": "<100",     "status": "HIGH",   "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "HDL Cholesterol",       "value": 48,   "unit": "mg/dL", "reference_range": ">40",      "status": "NORMAL", "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "Total Cholesterol",     "value": 214,  "unit": "mg/dL", "reference_range": "<200",     "status": "HIGH",   "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "Triglycerides",         "value": 171,  "unit": "mg/dL", "reference_range": "<150",     "status": "HIGH",   "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "CRP",                   "value": 4.2,  "unit": "mg/L",  "reference_range": "<3.0",     "status": "HIGH",   "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "Insulin (Fasting)",     "value": 14.8, "unit": "µIU/mL","reference_range": "2.6-24.9", "status": "NORMAL", "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "eGFR",                  "value": 91,   "unit": "mL/min","reference_range": ">60",      "status": "NORMAL", "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "ALT",                   "value": 31,   "unit": "U/L",   "reference_range": "7-40",     "status": "NORMAL", "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "TSH",                   "value": 2.4,  "unit": "mIU/L", "reference_range": "0.4-4.0",  "status": "NORMAL", "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
    {"marker_name": "Vitamin D (25-OH)",     "value": 28,   "unit": "ng/mL", "reference_range": "30-100",   "status": "LOW",    "date": DATE_REBOUND, "source_document": "Quest_Diagnostics_Feb2026.pdf"},
]

# ── Conversation memories: facts PHI has learned from previous chats ──────────
DEMO_MEMORIES = [
    "User was on Zepbound 10mg weekly from May 2025 to December 2025 (7 months)",
    "User stopped Zepbound in December 2025 — insurance prior authorization expired and was denied renewal",
    "User lost 34 lbs on Zepbound: starting weight ~206 lbs, lowest weight ~172 lbs",
    "User has regained approximately 6 lbs since stopping — current weight ~178 lbs",
    "User's goal weight is 158 lbs — using that for protein target calculation",
    "User reports intense food noise since stopping — 'I can't stop thinking about food, it's relentless'",
    "User's daily protein intake is approximately 45-55g — well below the Muscle Defense target of 86g",
    "User does not have a resistance training routine — currently walks 20 minutes daily",
    "User has a cardiology appointment in 3 weeks and is anxious about cholesterol and glucose numbers",
    "User exploring insurance appeal — prior auth was denied citing 'not medically necessary'",
    "Family history: father diagnosed with Type 2 diabetes at age 52, paternal grandfather had heart disease",
    "User is 41 years old, female, lives in Austin TX, works full-time in marketing",
    "User tried diet and exercise for 18 months before starting Zepbound with minimal results",
    "User's endocrinologist supports continuing Zepbound but insurance is blocking it",
]

# ── GLP-1 medication record ───────────────────────────────────────────────────
DEMO_MEDICATION = {
    "medication_name": "Zepbound",
    "dose_mg":         10.0,
    "frequency":       "weekly",
    "start_date":      "2025-05-01",
    "end_date":        "2025-12-15",
    "status":          "stopped",
    "stop_reason":     "Insurance prior authorization denied — cost barrier",
    "notes":           "Lost 34 lbs. No significant side effects. Endocrinologist recommends continuation.",
}

# ── GLP-1 onboarding record ───────────────────────────────────────────────────
DEMO_ONBOARDING = {
    "glp1_status":     "stopped",
    "medication_name": "Zepbound",
    "goal_weight_lbs": 158.0,
    "primary_concern": "insurance",
}

# ── Seed demo conversation ────────────────────────────────────────────────────
DEMO_CONVERSATION_TITLE = "🚨 Feb 2026 Labs — GLP-1 Cliff Alert"
DEMO_CHAT_USER  = "I just got my labs back and everything looks worse than before I started Zepbound. My glucose is 112 and my A1c went up. I stopped Zepbound 8 weeks ago because my insurance denied the prior auth. The food noise is unbearable. I don't know what to do."
DEMO_CHAT_REPLY = """\
**What you're experiencing has a name — and it's not failure.**

What you're describing is textbook post-GLP-1 metabolic rebound, and your labs confirm it's happening in real time. This is biology, not a character flaw.

**What your data is showing:**

🚨 **Glucose rebound: +23% from your personal baseline**
Your fasting glucose was 91 mg/dL when Zepbound was working best. It's now 112 mg/dL — a 23% increase in 8 weeks. The clinical threshold for an urgent rebound signal is 15%. You're past it.

🚨 **HbA1c rebound: +0.4% in 8 weeks**
Your HbA1c rose from 5.5% to 5.9% — a 0.4% increase. The research threshold for active glycemic rebound is 0.25%. This is fast. HbA1c reflects your average glucose over 2–3 months, meaning the rebound started almost immediately after you stopped.

⚠️ **LDL and CRP both rising**
LDL: 98 → 124 mg/dL. CRP: 1.1 → 4.2 mg/L. This combination — rising cholesterol alongside elevated inflammation — is the cardiovascular risk pattern your cardiologist needs to see together, not separately.

**The food noise is ghrelin surge — not willpower.**
When Zepbound was removed, your hypothalamus lost exogenous incretin suppression. Ghrelin — your primary hunger hormone — surged back immediately. This is documented in STEP-10 and SURMOUNT-4. The relentless food thoughts are your body executing a survival program. The intensity you're feeling at roughly 8 weeks post-cessation is clinically expected.

**Three things to do before your cardiology appointment:**

1. **Bring these three numbers together:** Your glucose trajectory (91 → 94 → 112), HbA1c trajectory (5.4% → 5.5% → 5.9%), and the 8-week timeframe since stopping Zepbound. This is your strongest PA argument — documented metabolic deterioration with a specific, insurable cause.

2. **Start the protein target today.** Your goal weight is 158 lbs. Your Muscle Defense target is **158 × 0.545 = 86g protein/day**. Your current 45-55g is about half that. High-protein meals (35g+ per meal) blunt ghrelin by approximately 25% through CCK and PYY release. This is the fastest non-pharmacological intervention for food noise.

3. **Ask your cardiologist specifically:** *"My glucose rose 23% and my HbA1c increased 0.4% in 8 weeks after stopping Zepbound due to an insurance denial — would you document this metabolic deterioration in my chart to support a PA appeal?"*

**Your insurance appeal has a strong clinical basis.** I can help you build the full prior authorization support packet from your lab data. Just ask.

---
⚕️ *PHI is an educational wellness tool. Always consult your healthcare provider before making any medical decisions.*"""


def seed():
    print("🌱 Seeding Curabook PHI — GLP-1 Cliff demo user...")
    print("   Patient: Sarah M., 41F, Austin TX")
    print("   Scenario: 8 weeks post-Zepbound, insurance denied, cliff in progress")
    print()

    # ── Step 1: Create auth user ──────────────────────────────────────────────
    demo_user_id = None
    try:
        res = sb.auth.admin.create_user({
            "email":         DEMO_EMAIL,
            "password":      DEMO_PASSWORD,
            "email_confirm": True,
            "user_metadata": {"first_name": "Sarah", "last_name": "M."},
        })
        demo_user_id = res.user.id
        print(f"✅ Demo user created: {DEMO_EMAIL} (id: {demo_user_id[:8]}…)")
    except Exception as e:
        if "already been registered" in str(e) or "already exists" in str(e).lower():
            try:
                users = sb.auth.admin.list_users()
                for u in users:
                    if u.email == DEMO_EMAIL:
                        demo_user_id = u.id
                        print(f"ℹ️  Demo user already exists (id: {demo_user_id[:8]}…)")
                        break
            except Exception as e2:
                print(f"❌ Could not fetch existing user: {e2}")
                return
        else:
            print(f"❌ Error creating user: {e}")
            return

    if not demo_user_id:
        print("❌ Could not determine demo user ID")
        return

    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    # ── Step 2: User profile ──────────────────────────────────────────────────
    try:
        sb.table("user_profiles").upsert({
            "user_id":           demo_user_id,
            "first_name":        "Sarah",
            "last_name":         "M.",
            "age":               41,
            "gender":            "female",
            "plan":              "pro",
            "reports_remaining": 9999,
            "goal_weight_lbs":   158.0,
            "glp1_status":       "stopped",
            "updated_at":        now,
        }, on_conflict="user_id").execute()
        print("✅ Profile created (Sarah, 41F, goal 158 lbs)")
    except Exception as e:
        print(f"⚠️  Profile: {e}")

    # ── Step 3: Consents ──────────────────────────────────────────────────────
    for ct in ["data_processing", "ai_processing", "document_processing"]:
        try:
            sb.table("user_consents").upsert({
                "user_id": demo_user_id, "consent_type": ct,
                "consent_version": "v2.0", "is_active": True, "granted_at": now,
            }, on_conflict="user_id,consent_type").execute()
        except Exception as e:
            print(f"⚠️  Consent {ct}: {e}")
    print("✅ Consents set")

    # ── Step 4: GLP-1 medication record ──────────────────────────────────────
    try:
        sb.table("glp1_medications").upsert({
            "user_id": demo_user_id,
            **DEMO_MEDICATION,
            "created_at": now,
            "updated_at": now,
        }, on_conflict="user_id,medication_name,start_date").execute()
        print(f"✅ Medication record: {DEMO_MEDICATION['medication_name']} {DEMO_MEDICATION['dose_mg']}mg")
    except Exception as e:
        try:
            sb.table("glp1_medications").insert({
                "user_id": demo_user_id,
                **DEMO_MEDICATION,
                "created_at": now,
                "updated_at": now,
            }).execute()
            print(f"✅ Medication record inserted (fallback)")
        except Exception as e2:
            print(f"⚠️  Medication: {e2} — run missing_tables.sql first")

    # ── Step 5: Onboarding record ─────────────────────────────────────────────
    try:
        sb.table("glp1_onboarding").upsert({
            "user_id": demo_user_id,
            **DEMO_ONBOARDING,
            "completed_at": now,
        }, on_conflict="user_id").execute()
        print("✅ Onboarding: stopped Zepbound, goal 158 lbs, insurance concern")
    except Exception as e:
        print(f"⚠️  Onboarding: {e} — run missing_tables.sql first")

    # ── Step 6: Health markers ────────────────────────────────────────────────
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
    print(f"✅ {inserted}/{len(DEMO_MARKERS)} health markers inserted (3 reports)")

    # ── Step 7: Conversation memories ────────────────────────────────────────
    try:
        sb.table("conversation_memories").delete().eq("user_id", demo_user_id).execute()
    except Exception:
        pass

    mem_ok = 0
    for fact in DEMO_MEMORIES:
        try:
            sb.table("conversation_memories").insert({
                "user_id": demo_user_id, "fact": fact,
                "category": "general", "is_active": True, "created_at": now,
            }).execute()
            mem_ok += 1
        except Exception as e:
            print(f"⚠️  Memory: {e}")
    print(f"✅ {mem_ok}/{len(DEMO_MEMORIES)} conversation memories inserted")

    # ── Step 8: Behavioral logs — recent low protein/steps showing the problem ─
    import datetime as dt
    behavioral_entries = [
        {"date": "2026-02-08", "metric_name": "protein",    "value": 48,   "unit": "g",     "notes": "Logged via cockpit"},
        {"date": "2026-02-09", "metric_name": "protein",    "value": 52,   "unit": "g",     "notes": "Logged via cockpit"},
        {"date": "2026-02-10", "metric_name": "protein",    "value": 44,   "unit": "g",     "notes": "Logged via cockpit"},
        {"date": "2026-02-08", "metric_name": "steps",      "value": 3200, "unit": "steps", "notes": "Logged via cockpit"},
        {"date": "2026-02-09", "metric_name": "steps",      "value": 4100, "unit": "steps", "notes": "Logged via cockpit"},
        {"date": "2026-02-10", "metric_name": "steps",      "value": 2800, "unit": "steps", "notes": "Logged via cockpit"},
        {"date": "2026-02-08", "metric_name": "sleep",      "value": 6.0,  "unit": "hours", "notes": "Restless, woke up thinking about food"},
        {"date": "2026-02-09", "metric_name": "sleep",      "value": 5.5,  "unit": "hours", "notes": "Logged via cockpit"},
        {"date": "2026-02-10", "metric_name": "food_noise", "value": 8,    "unit": "1-10",  "notes": "Ghrelin surge level — relentless"},
        {"date": "2026-02-09", "metric_name": "food_noise", "value": 7,    "unit": "1-10",  "notes": "Ghrelin surge level — high"},
    ]
    bl_ok = 0
    for entry in behavioral_entries:
        try:
            sb.table("behavioral_logs").insert({
                "user_id":   demo_user_id,
                "created_at": now,
                **entry,
            }).execute()
            bl_ok += 1
        except Exception as e:
            print(f"⚠️  Behavioral log: {e}")
    print(f"✅ {bl_ok}/{len(behavioral_entries)} behavioral log entries (low protein, high food noise)")

    # ── Step 9: Demo conversation ─────────────────────────────────────────────
    try:
        sb.table("chats").delete().eq("user_id", demo_user_id).execute()
        sb.table("conversations").delete().eq("user_id", demo_user_id).execute()
    except Exception:
        pass

    try:
        conv = sb.table("conversations").insert({
            "user_id": demo_user_id,
            "title":   DEMO_CONVERSATION_TITLE,
            "created_at": now,
        }).execute()
        conv_id = conv.data[0]["id"]
        sb.table("chats").insert([
            {"user_id": demo_user_id, "conversation_id": conv_id,
             "role": "user",      "content": DEMO_CHAT_USER,  "created_at": now},
            {"user_id": demo_user_id, "conversation_id": conv_id,
             "role": "assistant", "content": DEMO_CHAT_REPLY, "created_at": now},
        ]).execute()
        print("✅ Demo conversation seeded with cliff alert exchange")
    except Exception as e:
        print(f"⚠️  Demo conversation: {e}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print("🎉 GLP-1 CLIFF DEMO USER READY")
    print("=" * 62)
    print(f"   Email:    {DEMO_EMAIL}")
    print(f"   Password: {DEMO_PASSWORD}")
    print(f"   Patient:  Sarah M., 41F, Austin TX")
    print()
    print("   What PHI will show immediately on login:")
    print()
    print("   🚨 Cliff Alert #1:")
    print("      Glucose rebound +23% (91→112 mg/dL)")
    print("      Threshold: 15% | Status: EXCEEDED")
    print()
    print("   🚨 Cliff Alert #2:")
    print("      HbA1c rebound +0.4% (5.5%→5.9%)")
    print("      Threshold: 0.25% | Status: EXCEEDED")
    print()
    print("   ⚠️  Metabolic Shield:")
    print("      Protein: 48g/day vs target 86g/day (56% — red)")
    print("      Steps: ~3,400/day vs 8,000 target (43% — amber)")
    print("      Sleep: 5.8h vs 7h target (below ghrelin threshold)")
    print()
    print("   📋 Insurance advocacy brief ready to generate")
    print("   💬 Demo conversation showing full cliff analysis")
    print("=" * 62)


if __name__ == "__main__":
    seed()