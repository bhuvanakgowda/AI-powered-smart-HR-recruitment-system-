"""AI-Powered Smart HR Recruitment System - working prototype (Flask + SQLite).
Run:  pip install -r requirements.txt && python app.py   ->  http://127.0.0.1:5000
Login: hr@example.com / admin123  (set HR_PASSWORD, SECRET_KEY env vars for real use)
Email: set SMTP_HOST, SMTP_USER, SMTP_PASS (else the test link is printed + shown on screen).
"""
import os, re, json, sqlite3, secrets, smtplib, subprocess, sys, tempfile, random
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, redirect, session, g, render_template_string, flash, abort
from jinja2 import DictLoader
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # correct https links behind Render
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")
DB, UP = "hr.db", "uploads"
BASE_URL = os.getenv("BASE_URL", "")  # public URL, e.g. https://abc.ngrok-free.app
SKILLS = ["python", "java", "sql", "javascript", "react", "flask", "django", "c++", "html", "css", "machine learning", "aws", "docker"]

# ---------- question banks (swap/extend with an LLM call in generate_questions) ----------
APT = [
 {"text": "A train 120 m long passes a pole in 6 s. Its speed in km/h?", "options": ["60", "72", "80", "90"], "answer": 1},
 {"text": "Next in series: 2, 6, 12, 20, ?", "options": ["28", "30", "32", "26"], "answer": 1},
 {"text": "If 40% of x is 80, x = ?", "options": ["160", "200", "240", "320"], "answer": 1},
 {"text": "A can finish a job in 10 days, B in 15. Together they take?", "options": ["5", "6", "7", "8"], "answer": 1},
 {"text": "Cost price 500, sold at 20% profit. Selling price?", "options": ["550", "600", "620", "650"], "answer": 1},
]
TECH = {
 "python": [
  {"text": "Output of len({1, 2, 2, 3})?", "options": ["4", "3", "2", "Error"], "answer": 1},
  {"text": "Which is immutable in Python?", "options": ["list", "dict", "tuple", "set"], "answer": 2},
  {"text": "What does [x*x for x in range(3)] give?", "options": ["[0,1,4]", "[1,4,9]", "[0,1,2]", "[1,2,3]"], "answer": 0}],
 "java": [
  {"text": "Which keyword prevents method overriding?", "options": ["static", "final", "const", "private set"], "answer": 1},
  {"text": "Default value of an int instance field?", "options": ["null", "0", "undefined", "-1"], "answer": 1},
  {"text": "Which collection rejects duplicates?", "options": ["List", "ArrayList", "Set", "Vector"], "answer": 2}],
 "sql": [
  {"text": "Which clause filters grouped rows?", "options": ["WHERE", "HAVING", "ORDER BY", "LIMIT"], "answer": 1},
  {"text": "Which JOIN keeps all left-table rows?", "options": ["INNER", "LEFT", "CROSS", "SELF"], "answer": 1},
  {"text": "COUNT(*) counts?", "options": ["non-null values", "all rows", "distinct rows", "columns"], "answer": 1}],
}
CODING = [{"text": "Write solve(nums) that returns the sum of the even numbers in a list.",
           "starter": "def solve(nums):\n    pass\n",
           "tests": [[[[1, 2, 3, 4]], 6], [[[]], 0], [[[7, 9]], 0], [[[2, 2, 2]], 6]]}]

def generate_questions(skill, aptitude, coding):
    """Pick questions from banks. Hook: call an LLM here for dynamic questions."""
    qs = []
    if aptitude: qs += [dict(q, type="mcq", section="Aptitude") for q in random.sample(APT, 4)]
    if skill in TECH: qs += [dict(q, type="mcq", section=skill.title()) for q in TECH[skill]]
    if coding: qs += [dict(q, type="code", section="Coding") for q in CODING]
    for i, q in enumerate(qs): q["id"] = i
    return qs

# ---------- db ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    os.makedirs(UP, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, pw TEXT);
    CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, name TEXT, email TEXT, skills TEXT, experience TEXT, resume TEXT);
    CREATE TABLE IF NOT EXISTS tests(id INTEGER PRIMARY KEY, token TEXT UNIQUE, candidate_id INTEGER, label TEXT,
      questions TEXT, status TEXT DEFAULT 'Sent', score REAL, summary TEXT, expires TEXT);""")
    c.execute("INSERT OR IGNORE INTO users(email,pw) VALUES(?,?)",
              ("hr@example.com", generate_password_hash(os.getenv("HR_PASSWORD", "admin123"))))
    c.commit(); c.close()

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        return f(*a, **k) if session.get("hr") else redirect("/login")
    return w

# ---------- resume parsing ----------
def parse_resume(path):
    text = ""
    if path.lower().endswith(".pdf"):
        import fitz
        text = "\n".join(p.get_text() for p in fitz.open(path))
    else:
        import zipfile
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf8", "ignore")
        text = re.sub(r"<[^>]+>", " ", re.sub(r"</w:p>", "\n", xml))
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    exp = re.search(r"(\d+)\+?\s*(?:years|yrs)", text, re.I)
    low = text.lower()
    skills = [s for s in SKILLS if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", low)]
    name = next((l.strip() for l in text.splitlines() if l.strip()), "")[:60]
    return {"name": name, "email": email.group(0) if email else "", "skills": ", ".join(skills),
            "experience": f"{exp.group(1)} years" if exp else ""}

def send_mail(to, link, name):
    msg = EmailMessage()
    msg["Subject"], msg["To"] = "Your assessment link", to
    msg["From"] = os.getenv("SMTP_USER", "noreply@hrsmartrecruit.local")
    msg.set_content(f"Hi {name},\n\nPlease complete your assessment (valid 3 days, one attempt):\n{link}\n")
    host = os.getenv("SMTP_HOST")
    if not host:
        print("[DEV EMAIL]", to, link); return False
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587))) as s:
        s.starttls(); s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")); s.send_message(msg)
    return True

# ---------- grading ----------
def run_code(code, tests):
    """DEV ONLY: subprocess with timeout is NOT a real sandbox. Use Piston/JDoodle in production."""
    prog = code + "\nimport json\n_r=[]\nfor a,e in " + repr(tests) + ":\n    try: _r.append(solve(*a)==e)\n    except Exception: _r.append(False)\nprint(json.dumps(_r))\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(prog)
    try:
        out = subprocess.run([sys.executable, "-I", f.name], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        r = json.loads(out[-1]) if out else []
        return sum(r), len(tests)
    except Exception:
        return 0, len(tests)
    finally:
        os.unlink(f.name)

def grade(questions, form):
    sec = {}
    for q in questions:
        a = sec.setdefault(q["section"], [0, 0])
        if q["type"] == "mcq":
            a[1] += 1; a[0] += form.get(f"q{q['id']}") == str(q["answer"])
        else:
            p, t = run_code(form.get(f"q{q['id']}", ""), q["tests"]); a[0] += p; a[1] += t
    pct = {k: round(100 * v[0] / v[1]) for k, v in sec.items() if v[1]}
    total = round(sum(pct.values()) / len(pct)) if pct else 0
    strong = [k for k, v in pct.items() if v >= 70]; weak = [k for k, v in pct.items() if v < 50]
    return total, {"sections": pct, "strengths": strong, "weaknesses": weak}

# ---------- templates ----------
T = {"base.html": """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SmartRecruit</title><style>
:root{--ink:#16232e;--bg:#eef2f5;--card:#fff;--ac:#0f766e;--mu:#5d6b78;--ln:#d5dde4}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{background:var(--ink);color:#fff;padding:.9rem 1.5rem;display:flex;justify-content:space-between;align-items:center}
header a{color:#cfe;margin-left:1rem}main{max-width:900px;margin:1.5rem auto;padding:0 1rem}
.card{background:var(--card);border:1px solid var(--ln);border-radius:8px;padding:1.2rem;margin-bottom:1rem}
input,select,textarea{width:100%;padding:.55rem;border:1px solid var(--ln);border-radius:6px;font:inherit;margin:.2rem 0 .8rem}
textarea{font-family:ui-monospace,monospace;min-height:150px}label.i{display:flex;gap:.5rem;align-items:center;margin:.3rem 0}label.i input{width:auto;margin:0}
button,.btn{background:var(--ac);color:#fff;border:0;padding:.6rem 1.1rem;border-radius:6px;font:inherit;cursor:pointer;text-decoration:none}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:.5rem;border-bottom:1px solid var(--ln);font-size:.92rem}
.msg{background:#e0f2ef;border-left:4px solid var(--ac);padding:.6rem 1rem;margin-bottom:1rem;word-break:break-all}
:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}small{color:var(--mu)}
</style><header><b>SmartRecruit</b><nav>{% if session.hr %}<a href=/>Dashboard</a><a href=/upload>New candidate</a><a href=/logout>Log out</a>{% endif %}</nav></header>
<main>{% for m in get_flashed_messages() %}<div class=msg>{{m}}</div>{% endfor %}{% block body %}{% endblock %}</main>""",
"landing.html": """{% extends 'base.html' %}{% block body %}<div class=card style="padding:2.5rem 2rem"><h1 style="font-size:2.2rem;margin:0 0 .5rem">Screen candidates in minutes, not days</h1>
<p style="max-width:55ch;color:var(--mu)">Upload a resume, choose the skills to test, and the candidate gets a one-time assessment link by email. Scores reach your dashboard the moment they submit.</p>
<a class=btn href=/login>HR sign in</a></div>
<div class=card><b>How it works</b><p>Resume details are read automatically and you can fix them before sending. Tests mix aptitude, technical and coding questions. Links expire after 3 days and work once. You make the final hiring decision.</p></div>{% endblock %}""",
"login.html": """{% extends 'base.html' %}{% block body %}<form method=post class=card style="max-width:380px;margin:3rem auto">
<h2>HR sign in</h2><label>Email<input name=email type=email required></label><label>Password<input name=pw type=password required></label>
<button>Sign in</button></form>{% endblock %}""",
"dash.html": """{% extends 'base.html' %}{% block body %}<div class=card><h2>Candidates</h2>
{% if not rows %}<p>No tests yet. <a href=/upload>Upload a resume</a> to create the first one.</p>{% else %}
<table><tr><th>Name</th><th>Email</th><th>Test</th><th>Score</th><th>Status</th><th>Notes</th></tr>
{% for r in rows %}<tr><td>{{r.name}}</td><td>{{r.email}}</td><td>{{r.label}}</td><td>{{ '%d%%'|format(r.score) if r.score is not none else '-' }}</td>
<td>{{r.status}}</td><td><small>{% if r.summary %}{% set s=r.summary|fromjson %}{% for k,v in s.sections.items() %}{{k}} {{v}}%{{ ' | ' if not loop.last }}{% endfor %}<br>Strong: {{s.strengths|join(', ') or 'none'}}<br>Weak: {{s.weaknesses|join(', ') or 'none'}}{% else %}<a href="/test/{{r.token}}">link</a>{% endif %}</small></td></tr>{% endfor %}</table>{% endif %}</div>{% endblock %}""",
"upload.html": """{% extends 'base.html' %}{% block body %}<form method=post enctype=multipart/form-data class=card><h2>Upload resume</h2>
<input type=file name=resume accept=".pdf,.docx" required><button>Parse resume</button></form>{% endblock %}""",
"confirm.html": """{% extends 'base.html' %}{% block body %}<form method=post action=/create class=card><h2>Check details and create test</h2>
<small>Parsed automatically. Correct anything that looks wrong, especially the email.</small>
<input type=hidden name=path value="{{path}}">
<label>Name<input name=name value="{{d.name}}" required></label><label>Email<input name=email type=email value="{{d.email}}" required></label>
<label>Skills<input name=skills value="{{d.skills}}"></label><label>Experience<input name=experience value="{{d.experience}}"></label>
<label>Technical section<select name=skill><option value="">None</option>{% for s in tech %}<option {{'selected' if s in d.skills}}>{{s}}</option>{% endfor %}</select></label>
<label class=i><input type=checkbox name=apt checked> Aptitude (4 questions)</label><label class=i><input type=checkbox name=code> Python coding question</label><br>
<button>Create test and email link</button></form>{% endblock %}""",
"test.html": """{% extends 'base.html' %}{% block body %}<form method=post><div class=card><h2>Assessment for {{name}}</h2><small>One attempt. Submit when done.</small></div>
{% for q in qs %}<div class=card><b>{{loop.index}}. {{q.text}}</b> <small>({{q.section}})</small><br>
{% if q.type=='mcq' %}{% for o in q.options %}<label class=i><input type=radio name="q{{q.id}}" value="{{loop.index0}}"> {{o}}</label>{% endfor %}
{% else %}<textarea name="q{{q.id}}" spellcheck=false>{{q.starter}}</textarea>{% endif %}</div>{% endfor %}
<button>Submit answers</button></form>{% endblock %}""",
"done.html": """{% extends 'base.html' %}{% block body %}<div class=card><h2>{{title}}</h2><p>{{text}}</p></div>{% endblock %}"""}
app.jinja_loader = DictLoader(T)
app.jinja_env.filters["fromjson"] = json.loads

# ---------- routes ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE email=?", (request.form["email"],)).fetchone()
        if u and check_password_hash(u["pw"], request.form["pw"]):
            session["hr"] = u["id"]; return redirect("/")
        flash("Wrong email or password.")
    return render_template_string("{% extends 'login.html' %}")

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.route("/")
def dash():
    if not session.get("hr"):
        return render_template_string("{% extends 'landing.html' %}")
    rows = db().execute("""SELECT c.name,c.email,t.token,t.label,t.status,t.score,t.summary FROM tests t
                           JOIN candidates c ON c.id=t.candidate_id ORDER BY t.id DESC""").fetchall()
    return render_template_string("{% extends 'dash.html' %}", rows=rows)

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        f = request.files["resume"]
        if not f.filename.lower().endswith((".pdf", ".docx")):
            flash("Upload a PDF or DOCX file."); return redirect("/upload")
        path = os.path.join(UP, secrets.token_hex(8) + os.path.splitext(f.filename)[1].lower())
        f.save(path)
        return render_template_string("{% extends 'confirm.html' %}", d=parse_resume(path), path=path, tech=list(TECH))
    return render_template_string("{% extends 'upload.html' %}")

@app.route("/create", methods=["POST"])
@login_required
def create():
    f = request.form
    qs = generate_questions(f.get("skill", ""), "apt" in f, "code" in f)
    if not qs:
        flash("Pick at least one test section."); return redirect("/upload")
    cur = db().execute("INSERT INTO candidates(name,email,skills,experience,resume) VALUES(?,?,?,?,?)",
                       (f["name"], f["email"], f["skills"], f["experience"], f["path"]))
    token = secrets.token_urlsafe(16)
    label = " + ".join(filter(None, ["Aptitude" if "apt" in f else "", f.get("skill", "").title(), "Coding" if "code" in f else ""]))
    db().execute("INSERT INTO tests(token,candidate_id,label,questions,expires) VALUES(?,?,?,?,?)",
                 (token, cur.lastrowid, label, json.dumps(qs), (datetime.utcnow() + timedelta(days=3)).isoformat()))
    db().commit()
    link = f"{(BASE_URL or request.host_url).rstrip('/')}/test/{token}"
    sent = send_mail(f["email"], link, f["name"])
    flash(("Email sent. " if sent else "Email not configured; share this link: ") + link)
    return redirect("/")

@app.route("/test/<token>", methods=["GET", "POST"])
def take(token):
    t = db().execute("SELECT t.*,c.name FROM tests t JOIN candidates c ON c.id=t.candidate_id WHERE token=?", (token,)).fetchone()
    if not t: abort(404)
    show = lambda title, text: render_template_string("{% extends 'done.html' %}", title=title, text=text)
    if t["status"] == "Completed": return show("Already submitted", "This link can only be used once.")
    if datetime.utcnow() > datetime.fromisoformat(t["expires"]): return show("Link expired", "Ask the recruiter for a new link.")
    qs = json.loads(t["questions"])
    if request.method == "POST":
        score, summary = grade(qs, request.form)
        db().execute("UPDATE tests SET status='Completed',score=?,summary=? WHERE id=?", (score, json.dumps(summary), t["id"]))
        db().commit()
        return show("Thanks, you're done", "Your answers were submitted. The recruiter will contact you.")
    return render_template_string("{% extends 'test.html' %}", qs=qs, name=t["name"])

init_db()  # also runs under `flask run` / gunicorn

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 5000)), debug=True)
