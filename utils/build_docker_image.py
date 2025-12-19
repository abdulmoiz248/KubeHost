import subprocess
import os
import re

def get_minikube_docker_env():
    """Get Minikube's Docker environment variables with improved parsing"""
    try:
        result = subprocess.run(
            ["minikube", "docker-env", "--shell", "powershell"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print(f"Warning: minikube docker-env failed: {result.stderr}")
            return None
        
        env = os.environ.copy()
        # Parse PowerShell format: $Env:VAR = "value" or $Env:VAR="value"
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith('$Env:'):
                # Remove $Env: prefix
                line = line.replace('$Env:', '', 1)
                # Handle both formats: VAR = "value" and VAR="value"
                # Match: VAR = "value" or VAR="value"
                match = re.match(r'^(\w+)\s*=\s*["\']?([^"\']+)["\']?', line)
                if match:
                    var_name, var_value = match.groups()
                    env[var_name] = var_value.strip()
                else:
                    # Fallback: simple split
                    if '=' in line:
                        parts = line.split('=', 1)
                        if len(parts) == 2:
                            var_name = parts[0].strip()
                            var_value = parts[1].strip().strip('"').strip("'")
                            env[var_name] = var_value
        
        # Validate that we got the important variables
        if 'DOCKER_HOST' in env or 'DOCKER_TLS_VERIFY' in env:
            print(f"Successfully parsed Minikube Docker environment")
            return env
        else:
            print("Warning: Minikube Docker env parsed but missing key variables")
            return None
            
    except subprocess.TimeoutExpired:
        print("Warning: minikube docker-env command timed out")
        return None
    except Exception as e:
        print(f"Warning: Error getting Minikube Docker env: {e}")
        return None

def build_with_minikube_image(image_tag, app_path):
    """Try using minikube image build command (newer approach)"""
    try:
        print("Trying minikube image build...")
        # Convert Windows path to Unix-style for minikube
        app_path_unix = app_path.replace('\\', '/')
        result = subprocess.run(
            ["minikube", "image", "build", "-t", image_tag, app_path_unix],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            print(f"Successfully built image using minikube image build")
            return True
        else:
            print(f"minikube image build failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"minikube image build error: {e}")
        return False

def build_locally_and_load(image_tag, app_path):
    """Build image locally then load into Minikube"""
    try:
        print("Building image locally, then loading into Minikube...")
        # Build locally
        result = subprocess.run(
            ["docker", "build", "-t", image_tag, app_path],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            print(f"Local Docker build failed: {result.stderr}")
            return False
        
        # Load into Minikube
        print("Loading image into Minikube...")
        result = subprocess.run(
            ["minikube", "image", "load", image_tag],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(f"Successfully loaded image into Minikube")
            return True
        else:
            print(f"Failed to load image into Minikube: {result.stderr}")
            return False
    except Exception as e:
        print(f"Build and load error: {e}")
        return False

def ensure_minikube_running():
    """Check if Minikube is running"""
    try:
        result = subprocess.run(
            ["minikube", "status", "--format", "{{.Host}}"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "Running"
    except:
        return False

def build_docker_image(app_name, app_path):
    """Build Docker image for Minikube with multiple fallback strategies"""
    image_tag = f"gitdeploy/{app_name}:latest"
    
    print(f"Building Docker image: {image_tag}")
    
    # Check if Minikube is running
    minikube_running = ensure_minikube_running()
    
    # Strategy 1: Build locally and load into Minikube (most reliable)
    # This works regardless of Minikube Docker daemon connection issues
    print("Building image locally...")
    try:
        build_result = subprocess.run(
            ["docker", "build", "-t", image_tag, app_path],
            check=True,
            timeout=600,
            capture_output=True,
            text=True
        )
        print("✓ Image built successfully")
        
        # If Minikube is running, load the image
        if minikube_running:
            print("Loading image into Minikube...")
            load_result = subprocess.run(
                ["minikube", "image", "load", image_tag],
                check=False,
                timeout=120,
                capture_output=True,
                text=True
            )
            if load_result.returncode == 0:
                print("✓ Image loaded into Minikube successfully")
            else:
                print(f"Warning: Could not load image into Minikube: {load_result.stderr}")
                print("Image will be loaded during deployment")
        else:
            print("Minikube not running. Image will be loaded when Minikube starts during deployment.")
        
        return image_tag
        
    except subprocess.TimeoutExpired:
        raise Exception("Docker build timed out after 10 minutes")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if e.stderr else e.stdout if e.stdout else str(e)
        raise Exception(f"Docker build failed: {error_msg}")
    except Exception as e:
        raise Exception(f"Failed to build Docker image: {e}")