# app.py
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess
import os
import json
import shutil

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DATA_FILE = "apps.json"
CLONE_DIR = "deployments"

# Ensure deployments folder exists
os.makedirs(CLONE_DIR, exist_ok=True)

# Load existing apps from JSON
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        apps_data = json.load(f)
else:
    apps_data = []

def save_apps():
    with open(DATA_FILE, "w") as f:
        json.dump(apps_data, f, indent=2)

def detect_app_type(path):
    if os.path.exists(os.path.join(path, "package.json")):
        return "nodejs"
    elif os.path.exists(os.path.join(path, "requirements.txt")) or os.path.exists(os.path.join(path, "pyproject.toml")):
        return "python"
    elif os.path.exists(os.path.join(path, "index.html")):
        return "static"
    else:
        return "unknown"

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "apps": apps_data})

@app.post("/deploy")
def deploy(request: Request, gitUrl: str = Form(...), branch: str = Form("main"), appName: str = Form(...)):
    # Clean app folder if exists
    app_path = os.path.join(CLONE_DIR, appName)
    if os.path.exists(app_path):
        shutil.rmtree(app_path)

    try:
        # Clone repo
        subprocess.run(["git", "clone", "-b", branch, gitUrl, app_path], check=True)

        # Detect type
        app_type = detect_app_type(app_path)

        # Placeholder URL (we’ll hook real deployment later)
        deployed_url = f"http://localhost:8000/{appName}"

        # Save app info
        app_info = {
            "appName": appName,
            "gitUrl": gitUrl,
            "branch": branch,
            "type": app_type,
            "status": "cloned",
            "url": deployed_url
        }
        apps_data.append(app_info)
        save_apps()

    except subprocess.CalledProcessError:
        return {"status": "error", "message": "Failed to clone repository"}

    return RedirectResponse(url="/", status_code=303)
