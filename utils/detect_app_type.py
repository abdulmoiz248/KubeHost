import os

def detect_app_type(path):
    if os.path.exists(os.path.join(path, "package.json")):
            return "nodejs"
    elif os.path.exists(os.path.join(path, "requirements.txt")) or os.path.exists(os.path.join(path, "pyproject.toml")):
        return "python"
    elif os.path.exists(os.path.join(path, "index.html")):
        return "static"
    else:
        return "unknown"
