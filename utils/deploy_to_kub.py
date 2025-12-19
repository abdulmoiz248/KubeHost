import os
import subprocess
import tempfile
import time
import re
import json

INGRESS_PORT = 80  # Single port for all apps via Ingress

def sanitize_name(name):
    """Sanitize name for Kubernetes (lowercase, alphanumeric, hyphens only)"""
    name = name.lower()
    name = re.sub(r'[^a-z0-9-]', '-', name)
    name = re.sub(r'-+', '-', name)
    name = name.strip('-')
    return name[:63]  # K8s name limit

def ensure_minikube_running():
    """Ensure Minikube is running"""
    result = subprocess.run(
        ["minikube", "status", "--format", "{{.Host}}"],
        capture_output=True, text=True
    )
    if result.stdout.strip() != "Running":
        print("Starting Minikube...")
        subprocess.run(["minikube", "start", "--driver=docker"], check=True)

def ensure_ingress_controller():
    """Enable Minikube ingress addon if not already enabled"""
    result = subprocess.run(
        ["minikube", "addons", "list", "-o", "json"],
        capture_output=True, text=True
    )
    
    if '"ingress":' not in result.stdout or '"Status":"enabled"' not in result.stdout:
        subprocess.run(["minikube", "addons", "enable", "ingress"], check=True)
        print("Waiting for ingress controller to be ready...")
        subprocess.run([
            "kubectl", "wait", "--namespace", "ingress-nginx",
            "--for=condition=ready", "pod",
            "--selector=app.kubernetes.io/component=controller",
            "--timeout=180s"
        ], check=False)

def extract_port_from_dockerfile(app_path):
    """Extract port from Dockerfile EXPOSE directive or return None"""
    dockerfile_path = os.path.join(app_path, "Dockerfile")
    if os.path.exists(dockerfile_path):
        with open(dockerfile_path, "r", encoding='utf-8') as f:
            content = f.read()
            # Look for EXPOSE directive
            match = re.search(r'EXPOSE\s+(\d+)', content, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None

def extract_env_vars(app_path):
    """Extract environment variables from .env file and convert to K8s format"""
    env_file_path = os.path.join(app_path, ".env")
    env_vars = []
    
    if os.path.exists(env_file_path):
        with open(env_file_path, "r", encoding='utf-8') as env_file:
            for line in env_file:
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # Skip empty values
                    if key and value:
                        env_vars.append({"name": key, "value": value})
    
    return env_vars

def validate_image_exists(image_tag):
    """Validate that the Docker image exists in Minikube"""
    try:
        # Get Minikube docker env
        result = subprocess.run(
            ["minikube", "docker-env", "--shell", "powershell"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("Warning: Could not get Minikube docker env for image validation")
            return True  # Assume exists if we can't check
        
        # Check if image exists
        result = subprocess.run(
            ["docker", "images", image_tag, "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True
        )
        if result.stdout.strip() == image_tag:
            return True
        else:
            print(f"Warning: Image {image_tag} not found. Deployment may fail.")
            return False
    except Exception as e:
        print(f"Warning: Could not validate image existence: {e}")
        return True  # Assume exists if validation fails

def get_pod_errors(namespace, app_name):
    """Get error messages from pods for debugging"""
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-l", f"app={app_name}", "-o", "json"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            pods_data = json.loads(result.stdout)
            errors = []
            for pod in pods_data.get("items", []):
                pod_name = pod.get("metadata", {}).get("name", "unknown")
                status = pod.get("status", {})
                
                # Check container statuses
                for container_status in status.get("containerStatuses", []):
                    state = container_status.get("state", {})
                    if "waiting" in state:
                        reason = state["waiting"].get("reason", "")
                        message = state["waiting"].get("message", "")
                        errors.append(f"Pod {pod_name}: Waiting - {reason}: {message}")
                    elif "terminated" in state:
                        reason = state["terminated"].get("reason", "")
                        message = state["terminated"].get("message", "")
                        errors.append(f"Pod {pod_name}: Terminated - {reason}: {message}")
                
                # Check pod conditions
                for condition in status.get("conditions", []):
                    if condition.get("status") == "False" and condition.get("type") in ["Ready", "PodScheduled"]:
                        errors.append(f"Pod {pod_name}: {condition.get('type')} - {condition.get('message', '')}")
            
            return errors
    except Exception as e:
        return [f"Could not fetch pod errors: {e}"]
    return []

def wait_for_deployment(app_name, namespace, timeout=180):
    """Wait for deployment to be ready with better error reporting"""
    print(f"Waiting for deployment {app_name} in namespace {namespace} to be ready...")
    start_time = time.time()
    last_status = None
    
    while time.time() - start_time < timeout:
        # Check deployment status
        result = subprocess.run(
            ["kubectl", "get", "deployment", app_name, "-n", namespace,
             "-o", "jsonpath={.status.readyReplicas}"],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            ready = result.stdout.strip()
            desired = subprocess.run(
                ["kubectl", "get", "deployment", app_name, "-n", namespace,
                 "-o", "jsonpath={.spec.replicas}"],
                capture_output=True, text=True
            ).stdout.strip()
            
            if ready and desired:
                ready_int = int(ready)
                desired_int = int(desired)
                status = f"Ready: {ready_int}/{desired_int}"
                if status != last_status:
                    print(f"  {status}")
                    last_status = status
                
                if ready_int >= desired_int and ready_int >= 1:
                    print(f"Deployment {app_name} is ready!")
                    return True
        
        # Check for pod errors periodically
        if int(time.time() - start_time) % 30 == 0:  # Every 30 seconds
            errors = get_pod_errors(namespace, app_name)
            if errors:
                print(f"  Pod errors detected: {errors[0]}")
        
        time.sleep(5)
    
    # Final error check
    errors = get_pod_errors(namespace, app_name)
    if errors:
        print(f"Deployment failed. Pod errors: {errors}")
    
    return False

def deploy_to_k8s(app_name, image_tag, app_type, app_path=None, env_vars=None):
    """
    Deploy application to Kubernetes
    
    Args:
        app_name: Name of the application
        image_tag: Docker image tag
        app_type: Type of application (nodejs, python, static)
        app_path: Path to application directory (for port detection and env vars)
        env_vars: Optional environment variables string (if not provided, will read from .env)
    """
    try:
        # Sanitize names for K8s compatibility
        app_name = sanitize_name(app_name)
        namespace = f"app-{app_name}"
        
        print(f"Deploying {app_name} to Kubernetes...")
        
        # Determine port - try to extract from Dockerfile first
        port = None
        if app_path:
            port = extract_port_from_dockerfile(app_path)
        
        # Fallback to default ports based on app type
        if port is None:
            port = 3000 if app_type in ["nodejs", "nextjs"] else 8000 if app_type == "python" else 80
        
        print(f"Using port: {port}")
        
        # Extract environment variables
        env_list = []
        if app_path:
            env_list = extract_env_vars(app_path)
        elif env_vars:
            # Parse env_vars string if provided directly
            for line in env_vars.split('\n'):
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        env_list.append({"name": key, "value": value})
        
        if env_list:
            print(f"Found {len(env_list)} environment variables to inject")
        
        # Ensure Minikube is running
        print("Checking Minikube status...")
        ensure_minikube_running()
        
        # Ensure ingress controller is installed
        print("Checking ingress controller...")
        ensure_ingress_controller()
        
        # Ensure image is loaded into Minikube
        print(f"Ensuring image {image_tag} is available in Minikube...")
        try:
            # Try to load the image if it exists locally
            load_result = subprocess.run(
                ["minikube", "image", "load", image_tag],
                check=False,
                timeout=120,
                capture_output=True,
                text=True
            )
            if load_result.returncode == 0:
                print("✓ Image loaded into Minikube")
            else:
                # Image might already be there, or load failed - continue anyway
                print(f"Note: Image load result: {load_result.stdout if load_result.stdout else 'already exists or load skipped'}")
        except Exception as e:
            print(f"Warning: Could not load image into Minikube: {e}")
            print("Continuing with deployment - image may already exist in Minikube")
        
        # Validate image exists (optional check)
        validate_image_exists(image_tag)
        
        # Determine health check path based on framework
        health_check_path = "/"
        if app_type == "python":
            # FastAPI and Django often have /health or /healthz endpoints
            health_check_path = "/health"
        elif app_type in ["nodejs", "nextjs"]:
            # Next.js and NestJS might have /api/health
            health_check_path = "/"
        
        # Build environment variables YAML
        env_yaml = ""
        if env_list:
            env_yaml = "\n        env:"
            for env_var in env_list:
                env_yaml += f'\n        - name: {env_var["name"]}\n          value: "{env_var["value"]}"'
        
        # Namespace YAML - isolated namespace per app/user
        namespace_yaml = f"""
apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    app: {app_name}
    managed-by: kubehost
"""

        # Deployment YAML with 3 replicas max, resource limits, and health checks
        deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app_name}
  namespace: {namespace}
  labels:
    app: {app_name}
spec:
  replicas: 3
  selector:
    matchLabels:
      app: {app_name}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: {app_name}
    spec:
      containers:
      - name: {app_name}
        image: {image_tag}
        imagePullPolicy: Never
        ports:
        - containerPort: {port}
          protocol: TCP{env_yaml}
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        readinessProbe:
          httpGet:
            path: {health_check_path}
            port: {port}
          initialDelaySeconds: 15
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 5
        livenessProbe:
          httpGet:
            path: {health_check_path}
            port: {port}
          initialDelaySeconds: 40
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 3
"""

        # ClusterIP Service (internal only, Ingress handles external)
        service_yaml = f"""
apiVersion: v1
kind: Service
metadata:
  name: {app_name}-svc
  namespace: {namespace}
  labels:
    app: {app_name}
spec:
  selector:
    app: {app_name}
  ports:
    - name: http
      protocol: TCP
      port: 80
      targetPort: {port}
  type: ClusterIP
"""

        # Ingress for subdomain-based routing - works with all frameworks
        ingress_yaml = f"""
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {app_name}-ingress
  namespace: {namespace}
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "60"
spec:
  ingressClassName: nginx
  rules:
  - host: {app_name}.localhost
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {app_name}-svc
            port:
              number: 80
"""

        # HorizontalPodAutoscaler for auto-scaling (max 3 replicas as requested)
        hpa_yaml = f"""
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {app_name}-hpa
  namespace: {namespace}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {app_name}
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
"""

        # NetworkPolicy for namespace isolation
        network_policy_yaml = f"""
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {app_name}-network-policy
  namespace: {namespace}
spec:
  podSelector:
    matchLabels:
      app: {app_name}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: ingress-nginx
    ports:
    - protocol: TCP
      port: {port}
  egress:
  - to: []
"""

        # ResourceQuota to limit namespace resources
        quota_yaml = f"""
apiVersion: v1
kind: ResourceQuota
metadata:
  name: {app_name}-quota
  namespace: {namespace}
spec:
  hard:
    requests.cpu: "2"
    requests.memory: 2Gi
    limits.cpu: "4"
    limits.memory: 4Gi
    pods: "20"
"""

        # LimitRange for default container limits
        limit_range_yaml = f"""
apiVersion: v1
kind: LimitRange
metadata:
  name: {app_name}-limits
  namespace: {namespace}
spec:
  limits:
  - default:
      memory: 512Mi
      cpu: 500m
    defaultRequest:
      memory: 128Mi
      cpu: 100m
    type: Container
"""

        # Write temp files
        tmp_dir = tempfile.gettempdir()
        files = {
            "namespace": (os.path.join(tmp_dir, f"{app_name}-namespace.yaml"), namespace_yaml),
            "quota": (os.path.join(tmp_dir, f"{app_name}-quota.yaml"), quota_yaml),
            "limits": (os.path.join(tmp_dir, f"{app_name}-limits.yaml"), limit_range_yaml),
            "deployment": (os.path.join(tmp_dir, f"{app_name}-deployment.yaml"), deployment_yaml),
            "service": (os.path.join(tmp_dir, f"{app_name}-service.yaml"), service_yaml),
            "ingress": (os.path.join(tmp_dir, f"{app_name}-ingress.yaml"), ingress_yaml),
            "hpa": (os.path.join(tmp_dir, f"{app_name}-hpa.yaml"), hpa_yaml),
            "network": (os.path.join(tmp_dir, f"{app_name}-network-policy.yaml"), network_policy_yaml),
        }
        
        for name, (filepath, content) in files.items():
            with open(filepath, "w", encoding='utf-8') as f:
                f.write(content)

        # Apply to Kubernetes cluster in order with error handling
        print("Applying Kubernetes manifests...")
        
        apply_commands = [
            ("namespace", True),
            ("quota", True),
            ("limits", True),
            ("deployment", True),
            ("service", True),
            ("ingress", True),
            ("hpa", False),  # May fail without metrics-server
            ("network", True),
        ]
        
        for resource_name, should_check in apply_commands:
            filepath = files[resource_name][0]
            print(f"  Applying {resource_name}...")
            result = subprocess.run(
                ["kubectl", "apply", "-f", filepath],
                capture_output=True,
                text=True,
                check=should_check
            )
            
            if result.returncode != 0:
                error_msg = f"Failed to apply {resource_name}: {result.stderr}"
                if should_check:
                    raise Exception(error_msg)
                else:
                    print(f"  Warning: {error_msg}")

        # Wait for deployment to be ready
        print("Waiting for deployment to be ready...")
        deployment_ready = wait_for_deployment(app_name, namespace, timeout=180)
        
        if not deployment_ready:
            # Get detailed error information
            errors = get_pod_errors(namespace, app_name)
            error_details = "\n".join(errors) if errors else "Unknown error"
            
            # Get deployment status
            status_result = subprocess.run(
                ["kubectl", "describe", "deployment", app_name, "-n", namespace],
                capture_output=True, text=True
            )
            
            raise Exception(
                f"Deployment failed to become ready after 180 seconds.\n"
                f"Pod errors:\n{error_details}\n\n"
                f"Deployment status:\n{status_result.stdout}"
            )
        
        print(f"Deployment successful! App available at http://{app_name}.localhost")
        
        # Return subdomain-based URL (works with all frameworks)
        return f"http://{app_name}.localhost"
    
    except subprocess.CalledProcessError as e:
        error_msg = f"Kubernetes deployment failed: {e.stderr if e.stderr else str(e)}"
        print(f"ERROR: {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Deployment error: {str(e)}"
        print(f"ERROR: {error_msg}")
        raise Exception(error_msg)


def delete_app(app_name):
    """Delete an app and its namespace (cleans up everything)"""
    app_name = sanitize_name(app_name)
    namespace = f"app-{app_name}"
    subprocess.run(["kubectl", "delete", "namespace", namespace], check=False)


def get_app_status(app_name):
    """Get deployment status for an app"""
    app_name = sanitize_name(app_name)
    namespace = f"app-{app_name}"
    
    result = subprocess.run(
        ["kubectl", "get", "all", "-n", namespace],
        capture_output=True, text=True
    )
    return result.stdout


def list_all_apps():
    """List all deployed apps"""
    result = subprocess.run(
        ["kubectl", "get", "namespaces", "-l", "managed-by=kubehost", "-o", "name"],
        capture_output=True, text=True
    )
    namespaces = result.stdout.strip().split('\n')
    return [ns.replace('namespace/app-', '') for ns in namespaces if ns]


def scale_app(app_name, replicas):
    """Scale an app to specified replicas"""
    app_name = sanitize_name(app_name)
    namespace = f"app-{app_name}"
    subprocess.run([
        "kubectl", "scale", "deployment", app_name,
        f"--replicas={replicas}", "-n", namespace
    ], check=True)