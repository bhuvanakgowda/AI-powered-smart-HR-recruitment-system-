# AI-Powered Smart HR Recruitment System

Flask + SQLite app: HR uploads a resume, the app parses it, generates a test, emails a one-time link, auto-grades the submission and shows results on a dashboard.

## Run locally
    pip install -r requirements.txt
    python app.py        # http://127.0.0.1:5000

Login: `hr@example.com` / `admin123` (set `HR_PASSWORD` to change).

## Environment variables
`SECRET_KEY`, `HR_PASSWORD`, `BASE_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`

## Deploy
Render: connect this repo; `render.yaml` sets the build and start commands.

## Notes
The coding-question runner is for local testing only. Replace `run_code()` with the Piston or JDoodle API before exposing it publicly.
