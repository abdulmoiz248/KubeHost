import os
import subprocess
import tempfile
import time
import re

INGRESS_PORT = 80  # Single port for all apps via Ingress

def sanitize_name(name):
    """Sanitize name for Kubernetes (lowercase, alphanumeric, hyphens only)"""
    name = name.lower()
    name = re.sub(r'[^a-z0-9-]', '-', name)
    name = re.sub(r'-+', '-', name)
    name = name.strip('-')
    return name[:63]  # K8s name limit

def ensure_ingress_controller():
    """Install nginx ingress controller if not present"""
    # Check if ingress-nginx namespace exists
    result = subprocess.run(
        ["kubectl", "get", "namespace", "ingress-nginx"],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        # Install nginx ingress controller for Kind
        subprocess.run([
            "kubectl", "apply", "-f",
            "https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml"
        ], check=True)
        
        # Wait for ingress controller to be ready
        print("Waiting for ingress controller to be ready...")
        subprocess.run([
            "kubectl", "wait", "--namespace", "ingress-nginx",
            "--for=condition=ready", "pod",
            "--selector=app.kubernetes.io/component=controller",
            "--timeout=180s"
        ], check=False)

def load_image_to_kind(image_tag, cluster_name="kubehost"):
    """Load Docker image into Kind cluster"""
    subprocess.run(
        ["kind", "load", "docker-image", image_tag, "--name", cluster_name],
        check=True
    )

def wait_for_deployment(app_name, namespace, timeout=120):
    """Wait for deployment to be ready"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = subprocess.run(
            ["kubectl", "get", "deployment", app_name, "-n", namespace,
             "-o", "jsonpath={.status.readyReplicas}"],
            capture_output=True, text=True
        )
        ready = result.stdout.strip()
        if ready and int(ready) >= 1:
            return True
        time.sleep(5)
    return False

def deploy_to_k8s(app_name, image_tag, app_type):
    # Sanitize names for K8s compatibility
    app_name = sanitize_name(app_name)
    namespace = f"app-{app_name}"
    
    # Determine port based on app type
    port = 3000 if app_type in ["nodejs", "nextjs"] else 8000 if app_type == "python" else 80
    
    # Ensure ingress controller is installed
    ensure_ingress_controller()
    
    # Load Docker image into Kind cluster
    load_image_to_kind(image_tag)
    
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

    # Deployment YAML with 2 replicas, resource limits, and health checks
    deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app_name}
  namespace: {namespace}
  labels:
    app: {app_name}
spec:
  replicas: 2
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
          protocol: TCP
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        readinessProbe:
          httpGet:
            path: /
            port: {port}
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 3
        livenessProbe:
          httpGet:
            path: /
            port: {port}
          initialDelaySeconds: 30
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

    # HorizontalPodAutoscaler for auto-scaling
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
  minReplicas: 2
  maxReplicas: 10
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
        with open(filepath, "w") as f:
            f.write(content)

    # Apply to Kind cluster in order
    subprocess.run(["kubectl", "apply", "-f", files["namespace"][0]], check=True)
    subprocess.run(["kubectl", "apply", "-f", files["quota"][0]], check=True)
    subprocess.run(["kubectl", "apply", "-f", files["limits"][0]], check=True)
    subprocess.run(["kubectl", "apply", "-f", files["deployment"][0]], check=True)
    subprocess.run(["kubectl", "apply", "-f", files["service"][0]], check=True)
    subprocess.run(["kubectl", "apply", "-f", files["ingress"][0]], check=True)
    
    # These may fail without metrics-server, that's okay
    subprocess.run(["kubectl", "apply", "-f", files["hpa"][0]], check=False)
    subprocess.run(["kubectl", "apply", "-f", files["network"][0]], check=True)

    # Wait for deployment to be ready
    wait_for_deployment(app_name, namespace)
    
    # Return subdomain-based URL (works with all frameworks)
    return f"http://{app_name}.localhost"


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