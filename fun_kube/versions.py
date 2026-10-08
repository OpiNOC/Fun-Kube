"""
Versioni dei componenti installati da Fun-Kube — unica fonte di verità.

I manifest dei componenti applicati con kubectl sono vendored in
ansible/roles/<role>/files/: per aggiornarne uno cambiare la versione qui
ed eseguire  scripts/vendor-manifests.py  (riscarica i file e li verifica).
"""

# Kubernetes (default se K8S_VERSION non è impostato in .env)
K8S_VERSION = "v1.35.9"

# Pacchetto apt containerd.io dal repo Docker (prefisso di versione)
CONTAINERD_VERSION = "2.3.6"

# Manifest vendored
CALICO_VERSION = "v3.32.2"
CERT_MANAGER_VERSION = "v1.21.2"
METRICS_SERVER_VERSION = "v0.9.0"
LOCAL_PATH_VERSION = "v0.0.37"
METALLB_VERSION = "v0.16.0"
LONGHORN_VERSION = "v1.12.1"

# Helm chart (default se TRAEFIK_CHART_VERSION non è impostato in .env)
TRAEFIK_CHART_VERSION = "41.6.1"

# Immagini Nginx Proxy Manager
NPM_IMAGE_VERSION = "2.16.0"
MARIADB_IMAGE_VERSION = "11.4"

# Manifest vendored: (file di destinazione, URL)
VENDORED_MANIFESTS = [
    ("calico/files/operator-crds.yaml",
     f"https://raw.githubusercontent.com/projectcalico/calico/{CALICO_VERSION}/manifests/operator-crds.yaml"),
    ("calico/files/tigera-operator.yaml",
     f"https://raw.githubusercontent.com/projectcalico/calico/{CALICO_VERSION}/manifests/tigera-operator.yaml"),
    ("cert-manager/files/cert-manager.yaml",
     f"https://github.com/cert-manager/cert-manager/releases/download/{CERT_MANAGER_VERSION}/cert-manager.yaml"),
    ("metrics-server/files/components.yaml",
     f"https://github.com/kubernetes-sigs/metrics-server/releases/download/{METRICS_SERVER_VERSION}/components.yaml"),
    ("local-path-provisioner/files/local-path-storage.yaml",
     f"https://raw.githubusercontent.com/rancher/local-path-provisioner/{LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"),
    ("metallb/files/metallb-native.yaml",
     f"https://raw.githubusercontent.com/metallb/metallb/{METALLB_VERSION}/config/manifests/metallb-native.yaml"),
    ("longhorn/files/longhorn.yaml",
     f"https://raw.githubusercontent.com/longhorn/longhorn/{LONGHORN_VERSION}/deploy/longhorn.yaml"),
]


def ansible_vars() -> dict:
    """Versioni passate ad Ansible come extra-vars."""
    return {
        "containerd_version": CONTAINERD_VERSION,
        "calico_version": CALICO_VERSION,
        "cert_manager_version": CERT_MANAGER_VERSION,
        "metrics_server_version": METRICS_SERVER_VERSION,
        "local_path_provisioner_version": LOCAL_PATH_VERSION,
        "metallb_version": METALLB_VERSION,
        "longhorn_version": LONGHORN_VERSION,
        "npm_image_version": NPM_IMAGE_VERSION,
        "mariadb_image_version": MARIADB_IMAGE_VERSION,
    }
