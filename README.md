# Fun-Kube

Tool per installare cluster Kubernetes in modo completamente automatizzato, partendo da macchine Ubuntu 24.04 pulite.

Un solo comando. Nessun prerequisito sui nodi oltre a Ubuntu e SSH.

```bash
git clone https://github.com/OpiNOC/Fun-Kube /root/Fun-Kube
cd /root/Fun-Kube
cp .env.example .env
# modifica .env
./fun-kube up
```

---

## Topologie supportate

| Topologia | Control-plane | Worker | Note |
|---|---|---|---|
| **Local-node** | 1 (la bootstrap stessa) | 0 | Tutto gira sulla stessa macchina |
| **Mononodo** | 1 remoto | 0 | VM singola, fa tutto |
| **Single CP** | 1 | N | CP dedicato + worker separati |
| **HA** | 3+ | N | Alta disponibilità con keepalived VIP |

Se non definisci worker, i nodi control-plane vengono detaintati automaticamente e schedulano anche i workload applicativi.

---

## Cosa installa

**Cluster core** (sempre):
- containerd, kubelet, kubeadm, kubectl
- Calico CNI
- metrics-server (`kubectl top nodes/pods` funziona subito)
- cert-manager
- Rinnovo automatico certificati (systemd timer mensile su ogni CP)
- keepalived (solo HA)
- local-path-provisioner + StorageClass default (solo mononodo)
- `alias k=kubectl` e autocompletamento kubectl configurati in `~/.bashrc` sulla bootstrap machine

**Addon opzionali** (abilitati via `.env`):

| Addon | Variabile | Note |
|---|---|---|
| MetalLB | `METALLB_ENABLED=true` | Load balancer per IP on-premise |
| Traefik | `INGRESS_TYPE=traefik` | Ingress controller, DaemonSet, LB o NodePort |
| Nginx Proxy Manager | `INGRESS_TYPE=nginx-proxy-manager` | Ingress con UI web, DaemonSet, LB o NodePort |
| Longhorn | `LONGHORN_ENABLED=true` | Storage distribuito, RWO e RWX |

> Solo uno tra Traefik e Nginx Proxy Manager può essere attivo alla volta.

---

## Requisiti

### Bootstrap machine
Ubuntu 22.04 o 24.04. Può essere il tuo laptop, una VM di management, o il nodo stesso (modalità local-node).

Tutte le dipendenze (Python, Ansible, kubectl, Helm) vengono **installate automaticamente** al primo `./fun-kube up`.

### Nodi del cluster
- Ubuntu 22.04 o 24.04
- CPU ≥ 2 core
- RAM ≥ 2 GB (worker), ≥ 4 GB (control-plane raccomandato)
- Disco ≥ 20 GB
- Accesso SSH con chiave dalla bootstrap machine
- Sudo senza password per l'utente SSH

**Non serve installare nulla sui nodi.** Fun-Kube ci pensa.

---

## Accesso SSH ai nodi

**1. Genera la chiave sulla bootstrap machine** (se non l'hai già):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_rsa -N ""
```

**2. Aggiungi la chiave pubblica su ogni nodo.**

Copia il contenuto di `~/.ssh/id_rsa.pub` e appendilo a `/root/.ssh/authorized_keys` su ciascun nodo. Puoi farlo in fase di creazione della VM tramite console (Proxmox, ecc.), cloud-init, o qualsiasi sistema di provisioning — senza bisogno di accesso root interattivo.

**3. Verifica che la connessione funzioni:**

```bash
ssh -i ~/.ssh/id_rsa root@<ip-nodo> 'id'
```

**4. Imposta nel `.env`:**

```ini
SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa
```

---

## Configurazione per topologia

### Local-node (bootstrap = nodo)

```ini
CLUSTER_NAME=mylocal
LOCAL_NODE=true

NODE_1_IP=192.168.1.10
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=mylocal

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
```

SSH_USER e SSH_KEY_PATH non servono. Ansible gira in locale.

---

### Mononodo (VM singola remota)

```ini
CLUSTER_NAME=mynode
NODE_1_IP=10.0.0.10
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=mynode

SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
```

---

### Single CP + worker

```ini
CLUSTER_NAME=mycluster
NODE_1_IP=10.0.0.1
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=cp1

NODE_2_IP=10.0.0.11
NODE_2_ROLE=worker
NODE_2_HOSTNAME=worker1

NODE_3_IP=10.0.0.12
NODE_3_ROLE=worker
NODE_3_HOSTNAME=worker2

SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
```

---

### HA (3 CP + worker)

```ini
CLUSTER_NAME=mycluster-ha
NODE_1_IP=10.0.0.1
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=cp1

NODE_2_IP=10.0.0.2
NODE_2_ROLE=control-plane
NODE_2_HOSTNAME=cp2

NODE_3_IP=10.0.0.3
NODE_3_ROLE=control-plane
NODE_3_HOSTNAME=cp3

NODE_4_IP=10.0.0.11
NODE_4_ROLE=worker
NODE_4_HOSTNAME=worker1

SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12

KEEPALIVED_ENABLED=true
KEEPALIVED_VIP=10.0.0.100        # IP libero sulla subnet, non assegnato ad alcun nodo
KEEPALIVED_INTERFACE=eth0
```

Il `KEEPALIVED_VIP` diventa l'**endpoint del cluster**: è l'IP usato da kubeadm come `--control-plane-endpoint` e presente nel kubeconfig. In caso di failover del CP attivo, il VIP si sposta automaticamente su un altro nodo e il cluster rimane raggiungibile.

```bash
export KUBECONFIG=/root/.kube/mycluster-ha
kubectl get nodes   # raggiungibile tramite VIP 10.0.0.100
```

Per verificare quale CP detiene il VIP in un dato momento:
```bash
./fun-kube diagnose   # sezione Keepalived mostra MASTER/BACKUP per ogni CP
```

---

## Configurazione addon

### MetalLB

```ini
METALLB_ENABLED=true
METALLB_IP_POOL=10.0.0.200-10.0.0.220   # range IP liberi sulla subnet dei nodi
```

Il pool deve essere sulla stessa subnet dei nodi (L2) e non sovrapporsi a `POD_CIDR` o `SERVICE_CIDR`. Fun-Kube verifica automaticamente le sovrapposizioni.

---

### Ingress — Traefik

```ini
INGRESS_ENABLED=true
INGRESS_TYPE=traefik
INGRESS_SERVICE_TYPE=auto         # auto = LoadBalancer se MetalLB attivo, altrimenti NodePort

TRAEFIK_LB_IP=                    # IP specifico dal pool MetalLB (vuoto = auto-assign)
TRAEFIK_HTTP_NODEPORT=30080       # usato solo se service type = NodePort
TRAEFIK_HTTPS_NODEPORT=30443
TRAEFIK_IS_DEFAULT_CLASS=true
TRAEFIK_DASHBOARD_HOST=           # es. traefik.miodominio.com — vuoto = solo port-forward
TRAEFIK_ACME_EMAIL=               # es. admin@miodominio.com — vuoto = guida in output/
```

Traefik viene installato come **DaemonSet** (un pod per nodo worker). Se `TRAEFIK_ACME_EMAIL` è impostato, vengono creati automaticamente i ClusterIssuer `letsencrypt-staging` e `letsencrypt-prod`. Altrimenti la guida per configurarli manualmente viene scritta in `output/traefik-letsencrypt-guide.txt`.

Dashboard: se `TRAEFIK_DASHBOARD_HOST` non è impostato, la dashboard è accessibile via:
```bash
kubectl port-forward -n traefik svc/traefik 9000:9000
# poi: http://localhost:9000/dashboard/
```

---

### Ingress — Nginx Proxy Manager

```ini
INGRESS_ENABLED=true
INGRESS_TYPE=nginx-proxy-manager
INGRESS_SERVICE_TYPE=auto

NPM_LB_IP=                        # IP specifico dal pool MetalLB (vuoto = auto-assign)
NPM_HTTP_NODEPORT=30080
NPM_HTTPS_NODEPORT=30443
NPM_ADMIN_NODEPORT=30081
NPM_DB_PASSWORD=cambia-questa-password
```

NPM viene installato come **DaemonSet** (un pod per nodo). La UI di amministrazione è accessibile sulla porta 81 (LB) o sulla `NPM_ADMIN_NODEPORT` (NodePort).

Login di default UI: `admin@example.com` / `changeme` — **cambiare al primo accesso.**

Su cluster multi-nodo richiede `LONGHORN_ENABLED=true` per lo storage RWX condiviso tra i pod. Su mononodo usa local-path.

---

### Longhorn

```ini
LONGHORN_ENABLED=true
LONGHORN_RWX=true                 # abilita StorageClass ReadWriteMany (longhorn-rwx)
LONGHORN_UI_NODEPORT=31080        # 0 = UI non esposta
```

Longhorn richiede almeno 3 nodi worker per la replica dei volumi. Su mononodo funziona ma senza ridondanza. Prerequisiti (`nfs-common`, `open-iscsi`) installati automaticamente su tutti i nodi.

---

## CIDR — regole di non sovrapposizione

| Variabile | Uso | Esempio |
|---|---|---|
| `POD_CIDR` | Indirizzi dei pod | `172.16.0.0/16` |
| `SERVICE_CIDR` | ClusterIP dei service | `10.96.0.0/12` |
| `METALLB_IP_POOL` | IP esterni LoadBalancer | `10.0.0.200-10.0.0.220` |

Fun-Kube verifica le sovrapposizioni all'avvio e blocca con un errore chiaro se le trova.

---

## Comandi

```bash
./fun-kube up [.env]          # provisiona il cluster
  --dry-run                   # valida la configurazione senza modificare nulla
  --debug                     # output verboso (Ansible -vv)
  --skip-checks               # salta i preflight check SSH
  --yes / -y                  # salta la conferma interattiva

./fun-kube reset [.env]       # distrugge il cluster (kubeadm reset su tutti i nodi)
  --yes                       # salta la conferma interattiva
                              # Nota: agisce solo sui nodi. Sulla bootstrap machine
                              # rimangono kubeconfig, output/ e venv (non interferiscono con un nuovo up)

./fun-kube diagnose [.env]    # stato nodi: kubelet, containerd, disco, RAM, versioni

./fun-kube check-deps         # verifica tool sulla bootstrap machine
  --verbose                   # mostra le versioni
```

Ogni sottocomando accetta `--help` per la lista completa delle opzioni.

---

## Output generato

Al termine dell'installazione:

| File | Contenuto |
|---|---|
| `/root/.kube/<cluster_name>` | Kubeconfig **primario** — ServiceAccount token, non scade |
| `/root/.kube/<cluster_name>-admin` | Kubeconfig di **emergenza** — admin.conf, scade ~1 anno |
| `/root/<cluster_name>-manutenzione.txt` | Guida manutenzione (nodi, certificati, addon, comandi) |
| `output/cluster-info.txt` | Riepilogo rapido del cluster |
| `output/inventory.ini` | Inventory Ansible generato |
| `output/traefik-letsencrypt-guide.txt` | Guida LE (solo se Traefik senza ACME email) |

`~/.bashrc` viene aggiornato automaticamente con `KUBECONFIG`, `alias k=kubectl` e autocompletamento.

---

## Certificati — rinnovo automatico

Fun-Kube installa un systemd timer su ogni nodo control-plane che rinnova i certificati ogni mese con `kubeadm certs renew all` e riavvia automaticamente i componenti del control-plane.

In modalità HA i nodi rinnovano in finestre diverse (fino a 1 ora di spread) per evitare restart simultanei.

Il kubeconfig primario (ServiceAccount token) **non è soggetto alla scadenza annuale** dei certificati kubeadm. Solo il kubeconfig di emergenza (`-admin`) richiede un aggiornamento manuale dopo ogni rinnovo.

---

## Struttura del progetto

```
Fun-Kube/
├── fun-kube                      # CLI (eseguibile diretto, auto-bootstrap venv)
├── .env.example                  # template configurazione
├── fun_kube/
│   ├── cli.py                    # comandi: up, check-deps, reset, diagnose
│   ├── config.py                 # parsing .env, validazione, topologia
│   ├── preflight.py              # check pre-installazione sui nodi
│   ├── runner.py                 # inventory + sequenza playbook + output
│   └── deps.py                   # verifica e auto-install tool bootstrap
└── ansible/
    ├── playbooks/
    │   ├── bootstrap.yml              # common + containerd + kubeadm su tutti i nodi
    │   ├── keepalived.yml             # VIP keepalived (HA only)
    │   ├── kubeadm-init.yml           # init primo control-plane
    │   ├── control-plane-join.yml     # join CP aggiuntivi (HA only)
    │   ├── worker-join.yml            # join worker nodes
    │   ├── calico.yml                 # CNI
    │   ├── untaint-cp.yml             # rimozione taint (se nessun worker)
    │   ├── metrics-server.yml
    │   ├── cert-manager.yml
    │   ├── cert-renewal.yml           # systemd timer rinnovo certificati
    │   ├── local-path-provisioner.yml # StorageClass default (mononodo only)
    │   ├── bootstrap-kubeconfig.yml   # kubeconfig SA non-scadente
    │   ├── metallb.yml
    │   ├── ingress.yml                # Traefik o Nginx Proxy Manager
    │   └── longhorn.yml
    └── roles/
        ├── common/                    # sysctl, kernel, swap, chrony
        ├── containerd/                # container runtime
        ├── kubeadm/                   # kubelet + kubeadm + kubectl
        ├── calico/                    # CNI
        ├── metrics-server/
        ├── cert-manager/
        ├── cert-renewal/              # script + timer rinnovo certificati
        ├── local-path-provisioner/
        ├── keepalived/
        ├── metallb/
        ├── traefik/                   # DaemonSet, Helm, LB/NodePort, LE
        ├── nginx-proxy-manager/       # DaemonSet, LB/NodePort, multi/single-node
        └── longhorn/                  # storage distribuito, RWO + RWX
```

---

## Troubleshooting

**Panoramica rapida di tutti i nodi:**
```bash
./fun-kube diagnose
```

**Verifica dipendenze bootstrap:**
```bash
./fun-kube check-deps --verbose
```

**Stato del cluster:**
```bash
kubectl get nodes
kubectl get pods -A
```

**Log kubelet su un nodo:**
```bash
ssh root@<node-ip> 'journalctl -u kubelet -f'
```

**Stato certificati:**
```bash
ssh root@<cp-ip> 'sudo kubeadm certs check-expiration'
```

**Reinstallare da zero:**
```bash
./fun-kube reset --yes
./fun-kube up
```

`reset` lascia i nodi in uno stato pulito pronto per un nuovo `up`.
