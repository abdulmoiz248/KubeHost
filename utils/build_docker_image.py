import subprocess
def build_docker_image(app_name, app_path):
    image_tag = f"gitdeploy/{app_name}:latest"
    subprocess.run(["docker", "build", "-t", image_tag, app_path], check=True)
    return image_tag