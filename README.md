# Fun-Kube

Tool per installare cluster Kubernetes in modo automatizzato, partendo da una macchina bootstrap con accesso SSH ai nodi.

Un solo comando. Nessun prerequisito sui nodi oltre a Ubuntu e SSH.

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
- metrics-server
- cert-manager
- Rinnovo automatico certificati (systemd timer mensile su ogni CP)
- keepalived (solo HA)
- local-path-provisioner + StorageClass default (solo mononodo)

**Addon** (configurabili, installati separatamente con `fun-kube addons`):
- MetalLB
- Ingress (Traefik o Nginx Proxy Manager)
- Longhorn

---

## Requisiti

### Bootstrap machine
Ubuntu 22.04 o 24.04. Può essere il tuo laptop, una VM di management, o il nodo stesso (modalità local-node).

```bash
git clone https://github.com/OpiNOC/Fun-Kube
cd Fun-Kube
bash bootstrap-setup.sh
```

Questo installa: Python 3, Ansible, kubectl, Helm, e le dipendenze necessarie.

### Nodi del cluster
- Ubuntu 22.04 o 24.04
- CPU >= 2 core
- RAM >= 2 GB (worker), >= 4 GB (control-plane raccomandato)
- Disco >= 20 GB
- Swap disabilitato (o disabilitabile — Fun-Kube lo disabilita automaticamente)
- Accesso SSH con chiave dalla bootstrap machine
- Sudo senza password per l'utente SSH

**Non serve installare nulla sui nodi.** Fun-Kube ci pensa.

---

## Accesso SSH ai nodi

Fun-Kube accede ai nodi tramite chiave SSH dalla bootstrap machine. Passi da fare **una volta sola** prima di `fun-kube up`.

**1. Genera la chiave sulla bootstrap machine** (se non l'hai già):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_rsa -N ""
```

**2. Copia la chiave pubblica su ogni nodo del cluster:**

```bash
ssh-copy-id -i ~/.ssh/id_rsa.pub ubuntu@<ip-nodo>
```

Ripeti per ogni nodo (CP e worker). Se `ssh-copy-id` non è disponibile:

```bash
cat ~/.ssh/id_rsa.pub | ssh ubuntu@<ip-nodo> 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys'
```

**3. Verifica che l'utente possa fare sudo senza password** su ogni nodo:

```bash
ssh -i ~/.ssh/id_rsa ubuntu@<ip-nodo> 'sudo id'
```

Deve rispondere senza chiedere password. Su Ubuntu cloud images questo è già configurato di default per l'utente `ubuntu`.

**4. Imposta i valori nel `.env`:**

```ini
SSH_USER=ubuntu
SSH_KEY_PATH=~/.ssh/id_rsa
```

---

## Configurazione rapida

```bash
cp .env.example .env
# modifica .env con i tuoi parametri
./fun-kube up
```

Alla fine trovi:
- Kubeconfig in `/root/.kube/<cluster_name>` (già configurato in `~/.bashrc`)
- File di manutenzione in `/root/<cluster_name>-manutenzione.txt`

```bash
source ~/.bashrc
kubectl get nodes
```

---

## Configurazione per topologia

### Local-node (bootstrap = nodo)

```ini
CLUSTER_NAME=mylocal
LOCAL_NODE=true

NODE_1_IP=192.168.1.10      # IP reale della macchina
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

SSH_USER=ubuntu
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

SSH_USER=ubuntu
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

SSH_USER=ubuntu
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12

KEEPALIVED_ENABLED=true
KEEPALIVED_VIP=10.0.0.100   # IP libero sulla subnet, non assegnato ad alcun nodo
KEEPALIVED_INTERFACE=eth0
```

---

### CP che schedulano anche workload (nessun worker dedicato)

Basta non definire nodi con `ROLE=worker`. Fun-Kube rileva automaticamente l'assenza di worker e rimuove il taint `NoSchedule` da tutti i CP.

Funziona con qualsiasi topologia: mononodo, single CP da solo, HA con 3 CP senza worker.

---

## CIDR — regole di non sovrapposizione

Tre spazi di indirizzamento devono essere separati:

| Variabile | Uso | Esempio |
|---|---|---|
| `POD_CIDR` | Indirizzi dei pod | `172.16.0.0/16` |
| `SERVICE_CIDR` | ClusterIP dei service | `10.96.0.0/12` |
| `METALLB_IP_POOL` | IP esterni LoadBalancer | `10.0.0.200-10.0.0.220` |

Fun-Kube verifica le sovrapposizioni all'avvio e blocca se le trova.

Il pool MetalLB deve essere sulla **stessa subnet dei nodi** (per L2) ma fuori da POD_CIDR e SERVICE_CIDR.

---

## Comandi

```bash
./fun-kube up [.env]          # installa il cluster core
  --dry-run                   # valida la configurazione senza modificare nulla
  --debug                     # output verboso (Ansible -vv)
  --skip-checks               # salta i preflight check SSH

./fun-kube reset [.env]       # distrugge il cluster (kubeadm reset su tutti i nodi)
  --yes                       # salta la conferma interattiva

./fun-kube diagnose [.env]    # raccoglie stato da tutti i nodi (kubelet, containerd, disco, RAM)

./fun-kube check-deps         # verifica tool sulla bootstrap machine
  --verbose                   # mostra le versioni
```

---

## Output generato

Al termine dell'installazione:

| File | Contenuto |
|---|---|
| `/root/.kube/<cluster_name>` | Kubeconfig **primario** — ServiceAccount token, non scade |
| `/root/.kube/<cluster_name>-admin` | Kubeconfig di **emergenza** — admin.conf, scade ~1 anno |
| `/root/<cluster_name>-manutenzione.txt` | Guida manutenzione del cluster (nodi, certificati, comandi) |
| `output/cluster-info.txt` | Riepilogo del cluster (riferimento di progetto) |
| `output/inventory.ini` | Inventory Ansible generato |

`~/.bashrc` viene aggiornato automaticamente con:
```bash
export KUBECONFIG=/root/.kube/<cluster_name>
alias k=kubectl
source <(kubectl completion bash)
complete -F __start_kubectl k
```

Dopo l'installazione esegui `source ~/.bashrc` per attivare tutto.

---

## Certificati — rinnovo automatico

Fun-Kube installa un systemd timer su ogni nodo control-plane che rinnova i certificati ogni mese con `kubeadm certs renew all` e riavvia automaticamente i componenti del control-plane.

In modalità HA i nodi rinnovano in finestre diverse (fino a 1 ora di spread) per evitare restart simultanei.

Il kubeconfig primario (ServiceAccount token) **non è soggetto alla scadenza annuale** dei certificati kubeadm. Solo il kubeconfig di emergenza (`-admin`) richiede un aggiornamento manuale dopo ogni rinnovo certificati.

Tutte le istruzioni di manutenzione, con i comandi esatti per il tuo cluster, sono nel file `/root/<cluster_name>-manutenzione.txt`.

---

## Struttura del progetto

```
Fun-Kube/
├── fun-kube                      # CLI (eseguibile diretto)
├── bootstrap-setup.sh            # setup una tantum bootstrap machine
├── .env.example                  # template configurazione
├── fun_kube/
│   ├── cli.py                    # comandi: up, check-deps
│   ├── config.py                 # parsing .env, validazione, topologia
│   ├── preflight.py              # check pre-installazione sui nodi
│   ├── runner.py                 # generazione inventory + esecuzione Ansible
│   └── deps.py                   # verifica tool bootstrap machine
└── ansible/
    ├── playbooks/
    │   ├── bootstrap.yml         # common + containerd + kubeadm su tutti i nodi
    │   ├── keepalived.yml        # VIP keepalived (HA only)
    │   ├── kubeadm-init.yml      # init primo control-plane
    │   ├── control-plane-join.yml# join CP aggiuntivi (HA only)
    │   ├── worker-join.yml       # join worker nodes
    │   ├── calico.yml            # CNI
    │   ├── untaint-cp.yml        # rimozione taint (se nessun worker)
    │   ├── cert-manager.yml      # cert-manager
    │   ├── cert-renewal.yml      # systemd timer rinnovo certificati
    │   └── bootstrap-kubeconfig.yml # kubeconfig SA non-scadente
    └── roles/
        ├── common/               # sysctl, moduli kernel, swap, pacchetti base
        ├── containerd/           # container runtime
        ├── kubeadm/              # kubelet + kubeadm + kubectl
        ├── keepalived/           # VIP HA
        ├── calico/               # CNI
        ├── cert-manager/         # certificate manager
        └── cert-renewal/         # script + timer rinnovo certificati
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

**Stato del cluster dopo installazione:**
```bash
kubectl get nodes
kubectl get pods -A
```

**Log kubelet su un nodo:**
```bash
ssh ubuntu@<node-ip> 'journalctl -u kubelet -f'
```

**Stato certificati:**
```bash
ssh ubuntu@<cp-ip> 'sudo kubeadm certs check-expiration'
```

**Reinstallare da zero** (workflow tipico su Proxmox):
```bash
./fun-kube reset          # kubeadm reset + pulizia su tutti i nodi
# ripristina snapshot Proxmox oppure lascia i nodi puliti
./fun-kube up             # ricomincia da capo
```

`reset` non richiede snapshot — lascia i nodi in uno stato pulito pronto per un nuovo `up`.
Con gli snapshot Proxmox puoi fare rollback prima del reset se vuoi preservare lo stato pre-installazione.
