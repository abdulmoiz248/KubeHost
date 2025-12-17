# app.py
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess
import os
import json
import shutil
from utils.detect_app_type import detect_app_type
from utils.generate_docker_file import  generate_dockerfile
from utils.build_docker_image import build_docker_image
from utils.deploy_to_kub import deploy_to_k8s


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DATA_FILE = "apps.json"
CLONE_DIR = "deployments"
os.makedirs(CLONE_DIR, exist_ok=True)

# load apps
if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 0:
    with open(DATA_FILE, "r") as f:
        apps_data = json.load(f)
else:
    apps_data = []

def save_apps():
    with open(DATA_FILE, "w") as f:
        json.dump(apps_data, f, indent=2)



@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "apps": apps_data})

@app.post("/deploy")
def deploy(request: Request, gitUrl: str = Form(...), branch: str = Form("main"), appName: str = Form(...), envVars: str = Form("")):
    app_path = os.path.join(CLONE_DIR, appName)
    if os.path.exists(app_path):
        shutil.rmtree(app_path)

    try:
        subprocess.run(["git", "clone", "-b", branch, gitUrl, app_path], check=True)
        app_type = detect_app_type(app_path)

        # Create .env file if environment variables are provided
        if envVars.strip():
            env_file_path = os.path.join(app_path, ".env")
            with open(env_file_path, "w") as env_file:
                env_file.write(envVars.strip())

        generate_dockerfile(app_path, app_type)
        image_tag = build_docker_image(appName, app_path)
        deployed_url = deploy_to_k8s(appName, image_tag, app_type)

        app_info = {
            "appName": appName,
            "gitUrl": gitUrl,
            "branch": branch,
            "type": app_type,
            "status": "deployed",
            "url": deployed_url,
            "envVars": envVars
        }
        apps_data.append(app_info)
        with open(DATA_FILE, "w") as f:
            json.dump(apps_data, f, indent=2)

    except subprocess.CalledProcessError:
        return {"status": "error", "message": "Failed deployment"}

    return RedirectResponse(url="/", status_code=303)
