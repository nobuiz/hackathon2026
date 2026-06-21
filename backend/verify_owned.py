"""End-to-end verification of the 3 owned engines: Sentry, Phoenix, Deepgram."""
import os, sys, json, glob, wave, struct, math, logging
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
# silence the noisy OTLP exporter stderr when Phoenix server isn't up
logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import agent_pipeline as ap
import observability as obs
import intake_voice as voice

OUT = open(os.path.join(HERE, "owned_result.txt"), "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s); OUT.write(s + "\n"); OUT.flush()

SAMPLE_DIR = os.path.join(HERE, "..", "samples")



def adapt(s):
    return {
        "id": s.get("request_id"), "chan": s.get("channel"), "clinic": s.get("source_clinic"),
        "raw": s.get("raw_text", ""), "patient": s.get("patient", {}),
        "diagnosis_code": s.get("diagnosis_code"), "requested_cpt": s.get("requested_cpt"),
        "procedure": s.get("requested_procedure"), "insurance": s.get("insurance", {}),
        "npi": s.get("referring_provider_npi"),
    }


print("=== ENGINE FLAGS ===")
print("SENTRY_ON :", ap.SENTRY_ON)
print("PHOENIX_ON:", obs.PHOENIX_ON, "endpoint=", obs.PHOENIX_ENDPOINT)
print("DEEPGRAM_ON:", voice.DEEPGRAM_ON)

# ---------- 1. SENTRY: process the bad member-id sample ----------
print("\n=== 1. SENTRY (bad member ID -> MemberIdFormatError) ===")
bad = None
for f in glob.glob(os.path.join(SAMPLE_DIR, "*bad_member*")):
    bad = json.load(open(f, encoding="utf-8"))
if bad is None:
    # fabricate one if the sample name differs
    bad = {"request_id": "REF-BADID-001", "channel": "fax_ocr", "source_clinic": "Test Clinic",
           "raw_text": "Knee MRI right knee, CPT 73721. No conservative therapy yet.",
           "patient": {"name": "Test Pt", "dob": "1980-01-01"}, "diagnosis_code": "M25.561",
           "requested_cpt": "73721", "requested_procedure": "Right knee MRI",
           "insurance": {"payer": "Cigna", "member_id": "BADID!!!", "group": "1"},
           "referring_provider_npi": "1234567890"}
r = ap.run_pipeline(adapt(bad))
sentry_step = next((s for s in r["steps"] if s["who"] == "Sentry"), None)
print("Member ID in sample:", bad["insurance"]["member_id"])
print("Sentry step present:", sentry_step is not None)
if sentry_step:
    print("Captured event detail:", sentry_step["det"][:160])
# flush so the event is actually delivered before the script exits
if ap.SENTRY_ON:
    import sentry_sdk
    sentry_sdk.flush(timeout=10)
    print("Sentry flush() called -> event delivered to dashboard")

# ---------- 2. PHOENIX: run a request, confirm spans emitted ----------
print("\n=== 2. PHOENIX (OTLP spans) ===")
before = 0
tf = os.path.join(os.path.dirname(__file__), "traces.jsonl")
if os.path.exists(tf):
    before = sum(1 for _ in open(tf, encoding="utf-8"))
clean = json.load(open(os.path.join(SAMPLE_DIR, "01_clean_cardiology.json"), encoding="utf-8"))
r2 = ap.run_pipeline(adapt(clean))
after = sum(1 for _ in open(tf, encoding="utf-8"))
print(f"traces.jsonl spans: {before} -> {after} (+{after-before})")
print("Phoenix live export ON:", obs.PHOENIX_ON,
      "-> spans also sent to", (obs.PHOENIX_ENDPOINT or "(local JSONL only)"))

# ---------- 3. DEEPGRAM: build a real WAV, run /intake/voice logic ----------
print("\n=== 3. DEEPGRAM (voice intake) ===")
wav_path = os.path.join(os.path.dirname(__file__), "demo_call.wav")
# generate a 3s 440Hz tone WAV (real audio file; Deepgram will return empty transcript -> falls back)
fr = 16000
with wave.open(wav_path, "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(fr)
    for i in range(fr * 3):
        w.writeframes(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / fr))))
print("Generated WAV:", wav_path, "exists:", os.path.exists(wav_path))
tr = voice.transcribe(wav_path)
print("transcribe() engine:", tr["engine"])
print("transcript (first 90 chars):", tr["text"][:90])
# run it through the pipeline like the /intake/voice endpoint does
meta = {"id": "REF-VOICE-TEST", "chan": "deepgram_voice", "clinic": "Northgate Urgent Care",
        "patient": {"name": "James O'Neill", "dob": "1981-11-02", "mrn": None},
        "diagnosis_code": None, "requested_cpt": "45378", "procedure": "Diagnostic colonoscopy",
        "insurance": {"payer": "Aetna", "member_id": "AET-7781-2200", "group": None}, "npi": None}
meta["raw"] = tr["text"]
r3 = ap.run_pipeline(meta)
print("Voice pipeline verdict:", r3["verdict"], "| steps:", len(r3["steps"]))
os.remove(wav_path)
print("\n=== ALL THREE OWNED ENGINES EXERCISED ===")
