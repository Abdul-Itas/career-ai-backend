from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import random
import string
import hashlib
import hmac
import base64
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import psycopg


load_dotenv()

app = Flask(__name__)
CORS(app)

# ── Config ─────────────────────────────────────────────────────
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY")
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
MODEL         = "llama-3.3-70b-versatile"
DATABASE_URL  = os.environ.get("DATABASE_URL")
MAIL_EMAIL    = os.environ.get("MAIL_EMAIL")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
JWT_SECRET    = os.environ.get("JWT_SECRET", "change-this-secret-in-production")

if not GROQ_API_KEY:
    print("⚠️  WARNING: GROQ_API_KEY not found!")
else:
    print(f"✅ GROQ_API_KEY loaded ({GROQ_API_KEY[:8]}...)")

if not DATABASE_URL:
    print("⚠️  WARNING: DATABASE_URL not found!")
else:
    print("✅ DATABASE_URL loaded")

GROQ_HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
}


# ── Database ───────────────────────────────────────────────────
def get_db():
    conn = psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row)
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          SERIAL PRIMARY KEY,
            name        VARCHAR(100) NOT NULL,
            email       VARCHAR(255) UNIQUE NOT NULL,
            password    VARCHAR(255) NOT NULL,
            is_verified BOOLEAN DEFAULT FALSE,
            otp_code    VARCHAR(6),
            otp_expires TIMESTAMPTZ,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
            role       VARCHAR(10) NOT NULL,
            message    TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database tables ready")


# Run on startup
try:
    init_db()
except Exception as e:
    print(f"⚠️  DB init error: {e}")


# ── JWT (manual, no extra library) ────────────────────────────
def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

def _b64decode(s: str) -> bytes:
    s += '=' * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

def create_jwt(user_id: int, email: str) -> str:
    header  = _b64encode(json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    payload = _b64encode(json.dumps({
        "user_id": user_id,
        "email":   email,
        "exp":     (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
    }).encode())
    sig = _b64encode(hmac.new(
        JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest())
    return f"{header}.{payload}.{sig}"

def verify_jwt(token: str) -> dict | None:
    try:
        header, payload, sig = token.split('.')
        expected = _b64encode(hmac.new(
            JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64decode(payload))
        if data.get("exp", 0) < datetime.now(timezone.utc).timestamp():
            return None
        return data
    except Exception:
        return None

def get_current_user():
    """Extract user from Authorization header. Returns user dict or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1]
    return verify_jwt(token)


# ── Password hashing ───────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password: str, hashed: str) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == hashed


# ── Email OTP ──────────────────────────────────────────────────
def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=6))

def send_otp_email(to_email: str, name: str, otp: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{otp} is your Career AI verification code"
        msg["From"]    = MAIL_EMAIL
        msg["To"]      = to_email

        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
          <div style="background:linear-gradient(135deg,#6C63FF,#9C8FFF);
                      border-radius:16px;padding:28px;text-align:center;margin-bottom:24px">
            <h1 style="color:white;margin:0;font-size:28px">Career AI</h1>
            <p style="color:rgba(255,255,255,0.85);margin:8px 0 0">Your AI Career Advisor</p>
          </div>
          <h2 style="color:#1A1A2E">Hi {name}! 👋</h2>
          <p style="color:#4A4A6A;line-height:1.6">
            Here is your verification code to complete your sign up:
          </p>
          <div style="background:#F6F5FF;border:2px solid #6C63FF;border-radius:12px;
                      padding:24px;text-align:center;margin:24px 0">
            <span style="font-size:42px;font-weight:800;letter-spacing:10px;color:#6C63FF">
              {otp}
            </span>
          </div>
          <p style="color:#9E9EBF;font-size:13px">
            This code expires in <strong>10 minutes</strong>.<br>
            If you did not request this, you can safely ignore this email.
          </p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(MAIL_EMAIL, MAIL_PASSWORD)
            server.sendmail(MAIL_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False


# ── Groq helper ────────────────────────────────────────────────
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
    resp = requests.post(GROQ_URL, headers=GROQ_HEADERS, json=payload, timeout=30)
    if not resp.ok:
        print(f"❌ Groq error {resp.status_code}: {resp.text}")
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ════════════════════════════════════════════════════════════════

# ── POST /auth/signup ──────────────────────────────────────────
@app.route("/auth/signup", methods=["POST"])
def signup():
    data     = request.get_json(silent=True) or {}
    name     = data.get("name", "").strip()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    if not name or not email or not password:
        return jsonify({"error": "Name, email and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if "@" not in email:
        return jsonify({"error": "Invalid email address"}), 400

    otp     = generate_otp()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    conn = get_db()
    cur  = conn.cursor()
    try:
        # Check if email exists and is verified
        cur.execute("SELECT id, is_verified FROM users WHERE email = %s", (email,))
        existing = cur.fetchone()

        if existing and existing["is_verified"]:
            return jsonify({"error": "An account with this email already exists"}), 409

        hashed = hash_password(password)

        if existing:
            # Resend OTP to unverified account
            cur.execute("""
                UPDATE users SET name=%s, password=%s, otp_code=%s, otp_expires=%s
                WHERE email=%s
            """, (name, hashed, otp, expires, email))
        else:
            cur.execute("""
                INSERT INTO users (name, email, password, otp_code, otp_expires)
                VALUES (%s, %s, %s, %s, %s)
            """, (name, email, hashed, otp, expires))

        conn.commit()

        # Send OTP email
        if not send_otp_email(email, name, otp):
            return jsonify({"error": "Failed to send OTP email. Check your email address."}), 500

        return jsonify({
            "message": f"OTP sent to {email}. Please check your inbox.",
            "email": email
        }), 201

    except Exception as e:
        conn.rollback()
        print(f"Signup error: {e}")
        return jsonify({"error": "Server error during signup"}), 500
    finally:
        cur.close()
        conn.close()


# ── POST /auth/verify-otp ──────────────────────────────────────
@app.route("/auth/verify-otp", methods=["POST"])
def verify_otp():
    data  = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    otp   = data.get("otp", "").strip()

    if not email or not otp:
        return jsonify({"error": "Email and OTP are required"}), 400

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, otp_code, otp_expires
            FROM users WHERE email = %s
        """, (email,))
        user = cur.fetchone()

        if not user:
            return jsonify({"error": "Account not found"}), 404
        if user["otp_code"] != otp:
            return jsonify({"error": "Incorrect OTP code"}), 400
        if datetime.now(timezone.utc) > user["otp_expires"]:
            return jsonify({"error": "OTP has expired. Please sign up again."}), 400

        # Mark as verified and clear OTP
        cur.execute("""
            UPDATE users SET is_verified=TRUE, otp_code=NULL, otp_expires=NULL
            WHERE email=%s
        """, (email,))
        conn.commit()

        token = create_jwt(user["id"], email)
        return jsonify({
            "message": "Email verified successfully!",
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "email": email}
        })

    except Exception as e:
        conn.rollback()
        print(f"OTP verify error: {e}")
        return jsonify({"error": "Server error"}), 500
    finally:
        cur.close()
        conn.close()


# ── POST /auth/login ───────────────────────────────────────────
@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, password, is_verified FROM users WHERE email = %s
        """, (email,))
        user = cur.fetchone()

        if not user:
            return jsonify({"error": "No account found with this email"}), 404
        if not user["is_verified"]:
            return jsonify({"error": "Please verify your email first"}), 403
        if not check_password(password, user["password"]):
            return jsonify({"error": "Incorrect password"}), 401

        token = create_jwt(user["id"], email)
        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "email": email}
        })

    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({"error": "Server error"}), 500
    finally:
        cur.close()
        conn.close()


# ── POST /auth/resend-otp ──────────────────────────────────────
@app.route("/auth/resend-otp", methods=["POST"])
def resend_otp():
    data  = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT name FROM users WHERE email=%s AND is_verified=FALSE", (email,))
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "Account not found or already verified"}), 404

        otp     = generate_otp()
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        cur.execute("UPDATE users SET otp_code=%s, otp_expires=%s WHERE email=%s",
                    (otp, expires, email))
        conn.commit()

        if not send_otp_email(email, user["name"], otp):
            return jsonify({"error": "Failed to send email"}), 500

        return jsonify({"message": "New OTP sent to your email"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ════════════════════════════════════════════════════════════════
# CHAT ENDPOINTS (updated with history)
# ════════════════════════════════════════════════════════════════

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
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Missing or empty 'message' field"}), 400

    try:
        reply = call_groq(CAREER_SYSTEM_PROMPT, user_message)

        # Save to DB if user is logged in
        if user:
            conn = get_db()
            cur  = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO chat_messages (user_id, role, message) VALUES (%s, %s, %s)",
                    (user["user_id"], "user", user_message)
                )
                cur.execute(
                    "INSERT INTO chat_messages (user_id, role, message) VALUES (%s, %s, %s)",
                    (user["user_id"], "assistant", reply)
                )
                conn.commit()
            finally:
                cur.close()
                conn.close()

        return jsonify({"reply": reply})

    except requests.exceptions.HTTPError:
        return jsonify({"error": "AI service error. Please try again later."}), 502
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/chat/history", methods=["GET"])
def chat_history():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    limit = min(int(request.args.get("limit", 50)), 100)

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT role, message, created_at
            FROM chat_messages
            WHERE user_id = %s
            ORDER BY created_at ASC
            LIMIT %s
        """, (user["user_id"], limit))
        messages = cur.fetchall()
        return jsonify({
            "messages": [
                {
                    "role":    m["role"],
                    "message": m["message"],
                    "time":    m["created_at"].isoformat()
                } for m in messages
            ]
        })
    except Exception as e:
        print(f"History error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route("/chat/history", methods=["DELETE"])
def clear_history():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("DELETE FROM chat_messages WHERE user_id = %s", (user["user_id"],))
        conn.commit()
        return jsonify({"message": "Chat history cleared"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ════════════════════════════════════════════════════════════════
# CV REVIEW
# ════════════════════════════════════════════════════════════════

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
    data    = request.get_json(silent=True) or {}
    cv_text = data.get("cv_text", "").strip()
    if not cv_text:
        return jsonify({"error": "Missing 'cv_text' field"}), 400
    if len(cv_text) < 50:
        return jsonify({"error": "CV text is too short."}), 400
    if len(cv_text) > 8000:
        return jsonify({"error": "CV text is too long. Trim to under 8000 characters."}), 400
    try:
        feedback = call_groq(CV_SYSTEM_PROMPT, f"Please review this CV:\n\n{cv_text}", max_tokens=1000)
        return jsonify({"feedback": feedback})
    except requests.exceptions.HTTPError:
        return jsonify({"error": "AI service error. Please try again later."}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# CAREERS
# ════════════════════════════════════════════════════════════════

CAREERS = [
    {
        "id": "data_analyst", "title": "Data Analyst", "emoji": "📊",
        "description": "Turn raw data into business insights using SQL, Python, and visualization tools.",
        "salary_range": "$50k – $110k", "demand": "High",
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
        "id": "web_developer", "title": "Web Developer", "emoji": "💻",
        "description": "Build websites and web applications using HTML, CSS, JavaScript, and frameworks.",
        "salary_range": "$45k – $120k", "demand": "Very High",
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
        "id": "cybersecurity", "title": "Cybersecurity Analyst", "emoji": "🔐",
        "description": "Protect systems and networks from digital attacks and vulnerabilities.",
        "salary_range": "$60k – $130k", "demand": "Very High",
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
        "id": "mobile_developer", "title": "Mobile Developer", "emoji": "📱",
        "description": "Build iOS and Android apps using Flutter, React Native, or native tools.",
        "salary_range": "$55k – $125k", "demand": "High",
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
        "id": "ui_ux_designer", "title": "UI/UX Designer", "emoji": "🎨",
        "description": "Design beautiful, user-friendly interfaces for apps and websites.",
        "salary_range": "$45k – $110k", "demand": "High",
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
    search  = request.args.get("search", "").lower()
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


# ── Health check ───────────────────────────────────────────────
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "model": MODEL})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)