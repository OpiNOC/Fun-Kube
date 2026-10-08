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

# Versioni minor di Kubernetes supportate/testate dagli addon alle versioni
# fissate sopra: (min, max), max None = nessun limite dichiarato.
# Da aggiornare insieme alle versioni (fonti: pagine "requirements" upstream).
# `fun-kube upgrade` rifiuta un target fuori da questi intervalli.
K8S_COMPAT = {
    "calico":         (34, 36),    # docs.tigera.io/calico/3.32 — testato 1.34–1.36
    "cert-manager":   (33, 36),    # cert-manager.io/docs/releases — 1.21: 1.33–1.36
    "metrics-server": (34, None),  # README compatibility matrix — 0.9.x: 1.34+
    "longhorn":       (33, 36),    # longhorn.io/docs/1.12.1 — testato 1.33–1.36
    "metallb":        (None, None),  # nessuna matrice ufficiale
    "traefik":        (25, None),  # Chart.yaml kubeVersion >=1.25
}


def k8s_minor(version: str) -> int:
    """'v1.35.9' → 35. Solleva ValueError se il formato non è vX.Y[.Z]."""
    parts = version.lstrip("v").split(".")
    if len(parts) < 2 or parts[0] != "1":
        raise ValueError(version)
    return int(parts[1])


def incompatible_addons(target: str, addons) -> list:
    """Addon (nomi in K8S_COMPAT) che non supportano la versione target."""
    minor = k8s_minor(target)
    out = []
    for name in addons:
        lo, hi = K8S_COMPAT.get(name, (None, None))
        if (lo is not None and minor < lo) or (hi is not None and minor > hi):
            rng = f"1.{lo if lo is not None else '?'}–{'1.' + str(hi) if hi is not None else '…'}"
            out.append(f"{name} (supporta {rng})")
    return out


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
