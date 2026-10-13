"""Wizard texts (English): step intros and per-field label / hint / help."""
from .. import versions

STEP_INTRO = {
    "Cluster": (
        "Name and Kubernetes version of the cluster. The name identifies the cluster on this "
        "machine (kubeconfig /root/.kube/<name>) and cannot be changed later."
    ),
    "Nodes": (
        "Choose the topology, then list the nodes. Fun-Kube installs everything on clean "
        "Ubuntu 24.04 machines reachable over SSH. Without dedicated workers, control-planes "
        "also run your workloads."
    ),
    "Access & network": (
        "How this machine reaches the nodes, and the internal networks of the cluster. "
        "The defaults are fine unless they overlap with your LAN."
    ),
    "Add-ons": (
        "Optional components. Options that depend on others appear only when they make sense: "
        "e.g. LoadBalancer services need MetalLB, Nginx Proxy Manager on more nodes needs Longhorn."
    ),
    "Monitoring": (
        "Metrics, pod logs, Kubernetes events and alerting (Prometheus, Grafana, Loki). "
        "By default Grafana is reachable only on the nodes' network, not published."
    ),
    "Review": (
        "Check the result. The configuration is validated with the same rules used by "
        "`fun-kube up`. Save only, run a dry-run here (configuration + SSH checks, no changes; "
        "then you can go back, run it for real or quit), "
        "or save and provision. With a Kubernetes upgrade selected (existing clusters), the last two "
        "run `fun-kube upgrade` instead."
    ),
}

TOPOLOGY_HELP = {
    "single": "1 node runs everything. For development, tests and small services. No failover.",
    "single-cp": "1 control-plane + dedicated workers. Workloads survive a worker failure, "
                 "the API does not survive a control-plane failure.",
    "ha": "3 or 5 control-planes behind a virtual IP (keepalived) + optional workers. "
          "The cluster survives the loss of one control-plane. Recommended for production.",
}

# key: (label, hint shown under the field, help shown in the bottom panel when focused, advanced)
FIELDS = {
    "CLUSTER_NAME": ("Cluster name", "e.g. prod-k8s-01 — lowercase, digits and '-'",
                     "Unique on this machine. Used for the kubeconfig (/root/.kube/<name>) and the "
                     "maintenance file. Cannot be changed after the cluster is created.", False),
    "K8S_VERSION": ("Kubernetes version", f"Tested: {versions.K8S_VERSION}",
                    f"Leave {versions.K8S_VERSION} (tested with all add-ons) unless you need a specific "
                    "version. 'latest' = newest upstream stable. Existing clusters: use the upgrade choice "
                    "in this step.", False),
    "CLUSTER_TIMEZONE": ("Timezone", "e.g. Europe/Rome", "Applied to all nodes.", True),
    "LOCAL_NODE": ("Run on this machine", "Install the cluster on this same machine (no SSH)",
                   "Turn on if this machine IS the node. Useful for labs with limited resources.", False),
    "SSH_USER": ("SSH user", "root, or a user with passwordless sudo",
                 "User Fun-Kube connects with. A non-root user needs passwordless sudo (NOPASSWD).", False),
    "SSH_KEY_PATH": ("SSH private key", "Its public key must be in authorized_keys on every node",
                     "Private key on this machine. Add the matching .pub to ~/.ssh/authorized_keys on each "
                     "node (or via cloud-init). Use the button below to test all nodes.", False),
    "POD_CIDR": ("Pod network", "Default 172.16.0.0/16",
                 "Internal network for pod IPs. Must not overlap with your LAN, the service network or "
                 "the MetalLB pool. Cannot be changed later.", True),
    "SERVICE_CIDR": ("Service network", "Default 10.96.0.0/12",
                     "Internal network for Service IPs. Must not overlap with the pod network. "
                     "Cannot be changed later.", True),
    "API_SERVER_EXTRA_SANS": ("Extra API certificate names", "Comma separated, e.g. k8s.example.com,203.0.113.10",
                              "Additional names or IPs accepted by the API server certificate (e.g. a public "
                              "IP or DNS name). Node IPs and the VIP are included automatically.", True),
    "KEEPALIVED_VIP": ("Virtual IP (VIP)", "Free IP on the nodes' subnet, not assigned to any node",
                       "The cluster API endpoint. It moves to a healthy control-plane if one fails. "
                       "Cannot be changed later.", False),
    "KEEPALIVED_INTERFACE": ("VIP network interface", "Empty = auto-detect (recommended)",
                             "Interface carrying the VIP on control-planes. Auto-detect picks the interface "
                             "with the node IP.", True),
    "METALLB_ENABLED": ("MetalLB", "IP load balancer for on-premise clusters",
                        "Gives LoadBalancer services a real IP from a pool on your LAN (layer 2). Needed for "
                        "ingress or Grafana on a dedicated IP.", False),
    "METALLB_IP_POOL": ("MetalLB IP pool", "Free IPs on the nodes' subnet: 10.0.0.200-10.0.0.220 or 10.0.0.192/28",
                        "Addresses MetalLB can assign. They must be free and outside DHCP ranges.", False),
    "INGRESS_ENABLED": ("Ingress controller", "Publish web applications on HTTP/HTTPS",
                        "Receives HTTP/HTTPS traffic and routes it to your applications by host name.", False),
    "INGRESS_TYPE": ("Ingress type", "", "Traefik: Kubernetes-native (Ingress resources, Let's Encrypt). "
                     "Nginx Proxy Manager: web UI to manage proxy hosts and certificates.", False),
    "INGRESS_SERVICE_TYPE": ("How the ingress is reachable", "", "LoadBalancer = one dedicated IP (MetalLB). "
                             "NodePort = fixed ports on every node.", False),
    "TRAEFIK_LB_IP": ("Traefik IP", "Empty = assigned by MetalLB", "Fixed IP from the MetalLB pool.", True),
    "TRAEFIK_HTTP_NODEPORT": ("HTTP NodePort", "30000-32767", "", True),
    "TRAEFIK_HTTPS_NODEPORT": ("HTTPS NodePort", "30000-32767", "", True),
    "TRAEFIK_IS_DEFAULT_CLASS": ("Default IngressClass", "Ingress resources without a class use Traefik", "", True),
    "TRAEFIK_DASHBOARD_HOST": ("Dashboard host", "Empty = dashboard via port-forward only",
                               "Host name for the Traefik dashboard.", True),
    "TRAEFIK_ACME_EMAIL": ("Let's Encrypt e-mail", "Empty = no automatic certificates",
                           "Creates letsencrypt-staging / letsencrypt-prod issuers (cert-manager).", False),
    "NPM_LB_IP": ("Nginx Proxy Manager IP", "Empty = assigned by MetalLB", "Fixed IP from the MetalLB pool.", True),
    "NPM_HTTP_NODEPORT": ("HTTP NodePort", "30000-32767", "", True),
    "NPM_HTTPS_NODEPORT": ("HTTPS NodePort", "30000-32767", "", True),
    "NPM_ADMIN_NODEPORT": ("Admin UI NodePort", "30000-32767", "", True),
    "NPM_DB_PASSWORD": ("Database password", "Generated automatically — keep it",
                        "MariaDB password. It cannot be changed from here after installation.", True),
    "NPM_DB_STORAGE": ("Database volume", "Default 1Gi", "Proxy host configuration (a few MB).", True),
    "NPM_DATA_STORAGE": ("Data volume", "Default 2Gi", "nginx configs, certificates and access logs. "
                         "Increase for very busy proxies. Never shrunk later.", True),
    "LONGHORN_ENABLED": ("Longhorn", "Replicated persistent storage", "Volumes replicated on up to 3 nodes; "
                         "survives a node failure. On a single node local-path is usually enough.", False),
    "LONGHORN_RWX": ("ReadWriteMany volumes", "Volumes shared by several pods (via NFS)", "", False),
    "LONGHORN_UI_NODEPORT": ("Longhorn UI NodePort", "0 = not exposed", "", True),
    "DN_ESSENCE_ENABLED": ("DN-essence", "DNS rewrite manager for CoreDNS", "Rewrites DNS names inside the "
                           "cluster (hairpin NAT, split-horizon DNS). Web UI included.", False),
    "DN_ESSENCE_UI_NODEPORT": ("DN-essence UI NodePort", "0 = port-forward only", "", True),
    "MONITORING_ENABLED": ("Monitoring", "Prometheus, Grafana, Loki, alerts",
                           "Metrics, pod logs and Kubernetes events with ready-made dashboards and alerts. "
                           "Needs ~2-3 GB RAM and 22Gi of storage by default.", False),
    "GRAFANA_EXPOSE": ("How Grafana is reachable", "", "None of these publish Grafana on the Internet by "
                       "itself: that needs a proxy (NPM/Traefik) or NAT.", False),
    "GRAFANA_LB_IP": ("Grafana IP", "'auto' or a free IP of the MetalLB pool",
                      "With 'auto' the IP assigned by MetalLB is shown at the end of the installation.", False),
    "GRAFANA_ALLOWED_CIDRS": ("Allowed networks", "Comma separated, no spaces: 10.0.0.0/24,192.168.10.0/24",
                              "Only these networks can reach Grafana's IP. Empty = no restriction. "
                              "A single host: /32.", False),
    "GRAFANA_NODEPORT": ("Grafana NodePort", "30300 = Grafana's usual 3000 in the NodePort range",
                         "Grafana answers on http://<any node IP>:<port> (HTTP).", False),
    "GRAFANA_TRAEFIK_INGRESS": ("Traefik virtual host", "Publish Grafana on the domain below through Traefik",
                                "Creates the Traefik route for the Grafana domain (HTTPS with Let's Encrypt if "
                                "the e-mail is set). Off = configure your proxy (NPM) by hand.", False),
    "GRAFANA_DOMAIN": ("Grafana domain", "ONLY if a proxy publishes Grafana with this name",
                       "Becomes Grafana's root_url. Leave empty if Grafana is used directly by IP, otherwise "
                       "redirects and login break.", False),
    "GRAFANA_ADMIN_PASSWORD": ("Grafana admin password", "Empty = generated at first installation",
                               "Grafana keeps it in its database: after installation change it from Grafana.", True),
    "MONITORING_STORAGE_CLASS": ("StorageClass", "auto = longhorn-monitoring or local-path",
                                 "auto creates longhorn-monitoring (2 replicas, Retain) with Longhorn.", True),
    "PROM_RETENTION": ("Metrics retention", "e.g. 15d", "", True),
    "PROM_RETENTION_SIZE": ("Metrics max size", "Below the Prometheus volume (~80%)", "", True),
    "PROM_STORAGE": ("Prometheus volume", "Cannot be changed after installation",
                     "~0.3-0.5 GB/day every 100k series.", True),
    "LOKI_RETENTION": ("Logs retention", "e.g. 168h = 7 days", "", True),
    "LOKI_STORAGE": ("Loki volume", "Cannot be changed after installation",
                     "Retention is time-based only: increase for very verbose applications.", True),
    "GRAFANA_STORAGE": ("Grafana volume", "Grafana database (users, dashboards)", "", True),
    "ALERTMANAGER_STORAGE": ("Alertmanager volume", "Silences and notification state", "", True),
    "ALERTMANAGER_SLACK_WEBHOOK": ("Slack webhook", "Empty = no notifications",
                                   "Incoming webhook URL for alert notifications.", True),
    "ALERTMANAGER_SLACK_CHANNEL": ("Slack channel", "e.g. #alert-k8s", "", True),
}

CHOICES = {
    "INGRESS_TYPE": [("traefik", "Traefik"), ("nginx-proxy-manager", "Nginx Proxy Manager")],
}

UPGRADE_HELP = (
    "Runs `fun-kube upgrade`: checks first (cluster health, add-on compatibility, Longhorn volumes), "
    "etcd snapshot, then one node at a time. Workloads stop only if you confirm it explicitly "
    "(single node, 1-replica volumes). Choose the dry-run first to see the plan."
)

LOCKED_HINT = "Locked: the cluster already exists — changing this requires a reinstall."
