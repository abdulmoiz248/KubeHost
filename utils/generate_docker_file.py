import os
from utils.call_ai import call_ai

def generate_dockerfile(app_path, app_type):
    dockerfile_path = os.path.join(app_path, "Dockerfile")
    if os.path.exists(dockerfile_path):
        os.remove(dockerfile_path)
    
    # Determine dependency file
    if app_type in ["nodejs"]:
        file_path = os.path.join(app_path, "package.json")
    elif app_type == "python":
        file_path = os.path.join(app_path, "requirements.txt") if os.path.exists(os.path.join(app_path, "requirements.txt")) else os.path.join(app_path, "pyproject.toml")
    elif app_type == "static":
        file_path = None
    else:
        raise Exception("Unknown app type")
    
    # Read dependency file
    if file_path:
        with open(file_path, "r") as f:
            content = f.read()
    else:
        content = "static html"
    
    # Check for .env file and extract port information
    env_file_path = os.path.join(app_path, ".env")
    port_info = ""
    if os.path.exists(env_file_path):
        with open(env_file_path, "r") as env_file:
            env_lines = env_file.readlines()
            for line in env_lines:
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # Check if this looks like a port variable
                    if 'PORT' in key.upper():
                        port_info += f"\n\nIMPORTANT: The user has set {key}={value} in their environment variables.le."
    
    # Generate multistage Dockerfile
    system_prompt = "You are a Dockerfile expert. Generate ONLY a production-ready multistage Dockerfile. Return ONLY the Dockerfile content with no explanations, no markdown code blocks, no extra text."
    messages = [{"role": "user", "content": f"Create a multistage Dockerfile for {app_type}:\n\n{content}{port_info}"}]
    
    dockerfile_content = call_ai(messages, system_prompt=system_prompt)
    
    with open(dockerfile_path, "w") as f:
        f.write(dockerfile_content)
