Fun-Kube — Kubernetes Cluster Provisioning Tool

Obiettivo

Costruire un tool che permetta di creare cluster Kubernetes in modo automatizzato partendo da una macchina bootstrap con accesso SSH ai nodi.

Il tool deve:
  • richiedere il minimo numero di comandi all'utente (idealmente 1 comando)
  • permettere di definire ruoli dei nodi (control-plane / worker)
  • installare sempre l'ultima versione stabile di Kubernetes (override possibile)
  • usare kubeadm come backend
  • installare Calico come CNI
  • supportare topologie flessibili: cluster mononodo, single control-plane, multi control-plane HA
  • supportare addon: MetalLB, Ingress (Traefik o Nginx Proxy Manager), Longhorn
  • eseguire controlli di prerequisiti prima del deploy
  • produrre output utile per troubleshooting e scaling

Sistema target: Ubuntu (22.04 / 24.04)

⸻

Filosofia
  • Configurazione dichiarativa (.env = source of truth)
  • Automazione idempotente (Ansible)
  • CLI semplice (Python)
  • Modularità (addon attivabili/disattivabili)
  • Separazione tra cluster base e addon

⸻

Topologie supportate

  1. Mononodo — un singolo nodo fa sia control-plane che worker (utile per lab/dev)
  2. Single control-plane — 1 control-plane + N worker (senza HA)
  3. Multi control-plane HA — 3+ control-plane con keepalived (VIP) + N worker

Il tool rileva automaticamente la topologia dal numero di control-plane definiti in cluster.yaml:
  • 1 control-plane, 0 worker   → mononodo (taint rimosso automaticamente)
  • 1 control-plane, N worker   → single control-plane
  • 3+ control-plane, N worker  → HA con keepalived

⸻

Struttura del progetto

Fun-Kube/
├── fun-kube                  # CLI entry point (Python)
├── fun_kube/                 # package Python
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py             # parsing e validazione .env
│   ├── preflight.py          # preflight checks via SSH
│   └── runner.py             # esecuzione playbook Ansible
├── .env.example              # template con tutte le variabili (committato)
├── .env                      # configurazione locale (NON committato)
├── ansible/
│   ├── inventory.py          # dynamic inventory da cluster.yaml
│   ├── playbooks/
│   │   ├── bootstrap.yml
│   │   ├── keepalived.yml
│   │   ├── kubeadm-init.yml
│   │   ├── control-plane-join.yml
│   │   ├── worker-join.yml
│   │   ├── calico.yml
│   │   ├── metallb.yml
│   │   ├── ingress.yml
│   │   └── longhorn.yml
│   └── roles/
│       ├── common/
│       ├── containerd/
│       ├── kubeadm/
│       ├── keepalived/
│       ├── calico/
│       ├── metallb/
│       ├── traefik/
│       ├── nginx-proxy-manager/
│       ├── longhorn/
│       └── nfs/
├── output/
│   └── cluster-info.txt
└── README.md

⸻

Configurazione cluster (.env)

Il file .env è l'unica source of truth. Si parte sempre da .env.example:

  cp .env.example .env
  # editare .env con i propri valori

Esempio (HA con 3 control-plane — vedere .env.example per tutte le opzioni):

  CLUSTER_NAME=mycluster

  NODE_1_IP=10.0.0.1
  NODE_1_ROLE=control-plane
  NODE_1_HOSTNAME=cp1

  NODE_2_IP=10.0.0.2
  NODE_2_ROLE=control-plane
  NODE_2_HOSTNAME=cp2

  NODE_3_IP=10.0.0.3
  NODE_3_ROLE=control-plane
  NODE_3_HOSTNAME=cp3

  NODE_4_IP=10.0.0.4
  NODE_4_ROLE=worker
  NODE_4_HOSTNAME=worker1

  SSH_USER=ubuntu
  SSH_KEY_PATH=~/.ssh/id_rsa

  K8S_VERSION=latest
  POD_CIDR=172.16.0.0/16
  SERVICE_CIDR=10.96.0.0/12
  CNI=calico

  KEEPALIVED_ENABLED=true
  KEEPALIVED_VIP=10.0.0.100
  KEEPALIVED_INTERFACE=eth0

  METALLB_ENABLED=true
  METALLB_IP_POOL=10.0.0.200-10.0.0.220

  INGRESS_ENABLED=true
  INGRESS_TYPE=traefik

  LONGHORN_ENABLED=true
  LONGHORN_RWX=true

⸻

CLI

Comando principale:

  fun-kube up            # legge .env nella directory corrente
  fun-kube up /path/.env # .env alternativo

Opzioni:

  --dry-run        esegue solo validazioni
  --debug          output verboso
  --skip-checks    salta preflight (non consigliato)

⸻

Flusso operativo

  1. Validazione configurazione
     • YAML valido
     • topologia coerente (regole per mononodo / single / HA)
     • almeno un control-plane
     • IP e hostname duplicati
     • campi obbligatori presenti
     • keepalived.vip nella stessa subnet dei nodi (se HA)
     • MetalLB ip_pool non sovrapposto a pod_cidr / service_cidr

  2. Prompt interattivo (solo se necessario)
     • ssh key mancante
     • utente non specificato

  3. Preflight checks via SSH su tutti i nodi
     • accesso SSH funzionante
     • sudo senza password
     • swap disabilitato
     • moduli kernel (br_netfilter, overlay)
     • porte libere
     • hostname univoci
     • connettività tra nodi
     • container runtime presente o installabile

  4. Bootstrap nodi (Ansible)
     • install containerd
     • configurazione sysctl
     • disable swap
     • install nfs-common (sempre)

  5. Keepalived (solo se HA)
     • install keepalived su tutti i control-plane
     • configurazione MASTER sul primo, BACKUP sugli altri
     • il VIP diventa il --control-plane-endpoint di kubeadm

  6. Install Kubernetes
     • recupero versione stabile (se version=latest) via API release
     • install kubeadm, kubelet, kubectl

  7. Init primo control plane
     • kubeadm init con --control-plane-endpoint (VIP se HA, IP nodo se single)
     • salvataggio join token e certificate key
     • recupero kubeconfig

  8. Join control plane aggiuntivi (solo se HA)

  9. Join worker

  10. Install CNI (Calico)

  11. Mononodo: rimozione taint control-plane per schedulare workload

  12. Install addon (se abilitati)
      • MetalLB
      • Ingress
      • Longhorn

  13. Generazione output finale

⸻

Addon

MetalLB
  • deploy manifest ufficiale
  • configurazione IPAddressPool
  • validazione:
    • IP nella stessa subnet dei nodi
    • IP non sovrapposti a pod_cidr o service_cidr

⸻

Ingress

Opzioni:
  1. Traefik (default)
     • install via Helm
     • esposizione LoadBalancer
  2. Nginx Proxy Manager
     • deploy via manifest o Helm
     • UI disponibile

⸻

Longhorn
  • install via manifest ufficiale
  • prerequisiti:
    • nfs-common installato
    • spazio disco disponibile
  • RWX tramite share manager (NFS interno)

⸻

Output finale (output/cluster-info.txt)

Deve contenere:
  1. kubeconfig
       export KUBECONFIG=./kubeconfig

  2. stato cluster
       kubectl get nodes
       kubectl get pods -A

  3. comandi join
       worker:        kubeadm join <API_SERVER> --token ...
       control-plane: kubeadm join ... --control-plane --certificate-key ...

  4. troubleshooting
       kubectl describe node <name>
       journalctl -u kubelet -f
       crictl ps

  5. networking
       MetalLB IP pool configurato
       Keepalived VIP (se HA)

  6. ingress
       URL dashboard (Traefik o NPM)

  7. storage
       URL Longhorn UI

⸻

Requisiti nodi
  • Ubuntu 22.04 o 24.04
  • CPU >= 2 core
  • RAM >= 2GB (worker), 4GB (control-plane consigliato)
  • swap disabilitato
  • accesso SSH
  • sudo senza password

⸻

Considerazioni importanti
  • Kubernetes version "latest" risolta dinamicamente ma salvata in output
  • Il sistema deve essere idempotente (rilanciabile senza effetti collaterali)
  • Logging su file + stdout
  • Errori chiari e azionabili
  • Separare sempre cluster base e addon
  • Keepalived VIP deve essere un IP libero sulla subnet dei nodi (non assegnato ad alcun nodo)
