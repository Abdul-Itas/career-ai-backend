from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
MODEL        = "llama-3.3-70b-versatile"

if not GROQ_API_KEY:
    print("⚠️  WARNING: GROQ_API_KEY not found in .env file!")
else:
    print(f"✅ GROQ_API_KEY loaded ({GROQ_API_KEY[:8]}...)")

HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
}


def call_groq(system_prompt: str, user_message: str, max_tokens: int = 800) -> str:
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
    }
    try:
        resp = requests.post(GROQ_URL, headers=HEADERS, json=payload, timeout=30)
        if not resp.ok:
            print(f"❌ Groq API Error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        print(f"Groq HTTP Error: {e.response.text if e.response else str(e)}")
        raise
    except Exception as e:
        print(f"Unexpected error in call_groq: {e}")
        raise


# ── /chat ──────────────────────────────────────────────────────
CAREER_SYSTEM_PROMPT = """You are a friendly, practical career advisor helping students and young professionals.
Give clear, step-by-step guidance. Keep replies concise (under 200 words unless asked for detail).
Use numbered lists for roadmaps. Be encouraging but realistic.
If the user mentions a specific career, give them:
  1. A short description of the role
  2. The top 3-5 skills they need
  3. A beginner action they can take TODAY
Always end with a follow-up question to keep the conversation going."""

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Missing or empty 'message' field"}), 400
    try:
        reply = call_groq(CAREER_SYSTEM_PROMPT, user_message)
        return jsonify({"reply": reply})
    except requests.exceptions.HTTPError:
        return jsonify({"error": "AI service error. Please try again later."}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── /cv-review ─────────────────────────────────────────────────
CV_SYSTEM_PROMPT = """You are a professional CV reviewer with 10+ years of recruiting experience.
Review the CV provided and give structured, actionable feedback.
Format your response exactly like this:

OVERALL SCORE: X/10

STRENGTHS:
- (list 2-3 genuine strengths)

IMPROVEMENTS NEEDED:
- (list 3-5 specific, actionable improvements)

QUICK WINS (do these today):
- (list 2-3 easy changes that will immediately improve the CV)

Keep the tone encouraging. Be specific."""

@app.route("/cv-review", methods=["POST"])
def cv_review():
    data = request.get_json(silent=True) or {}
    cv_text = data.get("cv_text", "").strip()
    if not cv_text:
        return jsonify({"error": "Missing 'cv_text' field"}), 400
    if len(cv_text) < 50:
        return jsonify({"error": "CV text is too short. Please paste the full CV."}), 400
    if len(cv_text) > 8000:
        return jsonify({"error": "CV text is too long. Please trim to under 8000 characters."}), 400
    try:
        feedback = call_groq(CV_SYSTEM_PROMPT, f"Please review this CV:\n\n{cv_text}", max_tokens=1000)
        return jsonify({"feedback": feedback})
    except requests.exceptions.HTTPError:
        return jsonify({"error": "AI service error. Please try again later."}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── /careers ───────────────────────────────────────────────────
CAREERS = [
    {
        "id": "data_analyst",
        "title": "Data Analyst",
        "emoji": "📊",
        "description": "Turn raw data into business insights using SQL, Python, and visualization tools.",
        "salary_range": "$50k – $110k",
        "demand": "High",
        "roadmap": [
            {"step": 1, "title": "Learn Excel & Google Sheets",             "duration": "2 weeks"},
            {"step": 2, "title": "Learn SQL basics",                        "duration": "3 weeks"},
            {"step": 3, "title": "Learn Python (pandas, numpy)",            "duration": "4 weeks"},
            {"step": 4, "title": "Data visualization (Tableau or Power BI)","duration": "3 weeks"},
            {"step": 5, "title": "Build 2–3 portfolio projects",            "duration": "4 weeks"},
            {"step": 6, "title": "Apply for internships / junior roles",    "duration": "Ongoing"},
        ],
        "top_skills": ["SQL", "Python", "Excel", "Tableau", "Statistics"],
        "free_resources": ["Kaggle Learn", "Mode SQL Tutorial", "Google Data Analytics Certificate"],
    },
    {
        "id": "web_developer",
        "title": "Web Developer",
        "emoji": "💻",
        "description": "Build websites and web applications using HTML, CSS, JavaScript, and frameworks.",
        "salary_range": "$45k – $120k",
        "demand": "Very High",
        "roadmap": [
            {"step": 1, "title": "HTML & CSS fundamentals",                 "duration": "2 weeks"},
            {"step": 2, "title": "JavaScript basics",                       "duration": "4 weeks"},
            {"step": 3, "title": "React or Vue.js",                         "duration": "5 weeks"},
            {"step": 4, "title": "Backend basics (Node.js or Python)",      "duration": "4 weeks"},
            {"step": 5, "title": "Deploy a project (Vercel/Netlify)",       "duration": "1 week"},
            {"step": 6, "title": "Build portfolio & apply",                 "duration": "Ongoing"},
        ],
        "top_skills": ["HTML/CSS", "JavaScript", "React", "Git", "REST APIs"],
        "free_resources": ["freeCodeCamp", "The Odin Project", "MDN Web Docs"],
    },
    {
        "id": "cybersecurity",
        "title": "Cybersecurity Analyst",
        "emoji": "🔐",
        "description": "Protect systems and networks from digital attacks and vulnerabilities.",
        "salary_range": "$60k – $130k",
        "demand": "Very High",
        "roadmap": [
            {"step": 1, "title": "Networking fundamentals (TCP/IP, DNS)",   "duration": "3 weeks"},
            {"step": 2, "title": "Linux command line",                      "duration": "2 weeks"},
            {"step": 3, "title": "CompTIA Security+ certification",         "duration": "8 weeks"},
            {"step": 4, "title": "Practice on TryHackMe / HackTheBox",     "duration": "Ongoing"},
            {"step": 5, "title": "Learn SIEM tools (Splunk basics)",        "duration": "3 weeks"},
            {"step": 6, "title": "Apply for SOC Analyst roles",             "duration": "Ongoing"},
        ],
        "top_skills": ["Networking", "Linux", "Python scripting", "SIEM", "Ethical hacking"],
        "free_resources": ["TryHackMe", "Cybrary", "SANS Cyber Aces"],
    },
    {
        "id": "mobile_developer",
        "title": "Mobile Developer",
        "emoji": "📱",
        "description": "Build iOS and Android apps using Flutter, React Native, or native tools.",
        "salary_range": "$55k – $125k",
        "demand": "High",
        "roadmap": [
            {"step": 1, "title": "Learn Dart programming language",         "duration": "2 weeks"},
            {"step": 2, "title": "Flutter basics (widgets, layout)",        "duration": "4 weeks"},
            {"step": 3, "title": "State management (Provider or Riverpod)", "duration": "3 weeks"},
            {"step": 4, "title": "Connect to APIs (http package)",          "duration": "2 weeks"},
            {"step": 5, "title": "Publish app to Play Store",               "duration": "1 week"},
            {"step": 6, "title": "Build portfolio & apply",                 "duration": "Ongoing"},
        ],
        "top_skills": ["Flutter", "Dart", "REST APIs", "Firebase", "Git"],
        "free_resources": ["Flutter.dev docs", "DartPad", "Flutter & Dart Udemy (free coupon)"],
    },
    {
        "id": "ui_ux_designer",
        "title": "UI/UX Designer",
        "emoji": "🎨",
        "description": "Design beautiful, user-friendly interfaces for apps and websites.",
        "salary_range": "$45k – $110k",
        "demand": "High",
        "roadmap": [
            {"step": 1, "title": "Design principles & color theory",        "duration": "2 weeks"},
            {"step": 2, "title": "Learn Figma (industry standard)",         "duration": "3 weeks"},
            {"step": 3, "title": "UX research & user testing basics",       "duration": "2 weeks"},
            {"step": 4, "title": "Build 3 case study projects",             "duration": "5 weeks"},
            {"step": 5, "title": "Create portfolio on Behance/Dribbble",    "duration": "1 week"},
            {"step": 6, "title": "Apply for junior designer roles",         "duration": "Ongoing"},
        ],
        "top_skills": ["Figma", "Prototyping", "User research", "Typography", "Design systems"],
        "free_resources": ["Figma Community", "Google UX Design Certificate (audit free)", "Dribbble"],
    },
]

@app.route("/careers", methods=["GET"])
def get_careers():
    search = request.args.get("search", "").lower()
    results = CAREERS
    if search:
        results = [c for c in CAREERS if search in c["title"].lower() or search in c["description"].lower()]
    return jsonify({"careers": results, "count": len(results)})

@app.route("/careers/<career_id>", methods=["GET"])
def get_career(career_id):
    career = next((c for c in CAREERS if c["id"] == career_id), None)
    if not career:
        return jsonify({"error": "Career not found"}), 404
    return jsonify(career)

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "model": MODEL})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)