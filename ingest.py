import os
from dotenv import load_dotenv
from supabase import create_client
from sentence_transformers import SentenceTransformer
from pypdf import PdfReader

# 1. LOAD KEYS AUTOMATICALLY
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Error: Missing keys in .env file")
    exit()

# 2. SETUP
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
model = SentenceTransformer('all-MiniLM-L6-v2')

def run_ingest():
    folder = "data" # Make sure this folder exists
    if not os.path.exists(folder):
        os.makedirs(folder)
        print(f"📂 Created '{folder}' folder. Put PDF files there and run this again.")
        return

    print(f"📂 Scanning '{folder}'...")
    files = os.listdir(folder)
    
    if not files:
        print("⚠️ No files found in 'data' folder.")
        return

    for filename in files:
        filepath = os.path.join(folder, filename)
        content = ""
        
        # Read PDF
        if filename.endswith(".pdf"):
            try:
                reader = PdfReader(filepath)
                for page in reader.pages: content += page.extract_text() + "\n"
            except: 
                print(f"❌ Failed to read {filename}")
                continue
        elif filename.endswith(".txt"):
            with open(filepath, "r", encoding="utf-8") as f: content = f.read()
        else:
            continue

        # Upload
        print(f"   Processing: {filename}...")
        chunks = content.split("\n\n")
        for chunk in chunks:
            if len(chunk) < 20: continue
            vector = model.encode(chunk).tolist()
            supabase.table("documents").insert({
                "content": chunk, 
                "metadata": {"source": filename}, 
                "embedding": vector
            }).execute()
    
    print("✅ Knowledge Upload Complete!")

if __name__ == "__main__":
    run_ingest()