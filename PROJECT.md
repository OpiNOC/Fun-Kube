Fun-Kube — Kubernetes Cluster Provisioning Tool
================================================

## Obiettivo

Costruire un tool che permetta di creare cluster Kubernetes in modo completamente
automatizzato su macchine Ubuntu 24.04 pulite, senza alcun prerequisito manuale.

Il tool deve:
  • richiedere UN solo comando (./fun-kube up)
  • auto-installare tutte le proprie dipendenze al primo avvio
  • supportare topologie: mononodo, single control-plane, HA multi control-plane
  • installare sempre l'ultima versione stabile di Kubernetes (override possibile)
  • usare kubeadm + Calico CNI
  • supportare addon opzionali: MetalLB, Ingress (Traefik / NPM), Longhorn
  • essere idempotente (ogni riesecuzione converge senza danni)

Sistema target: Ubuntu 24.04 LTS (nodi e bootstrap)

---

## Come ottenere la versione aggiornata del progetto

**Su una macchina che ha già il repo (ma potrebbe avere file vecchi):**

```bash
cd /root/Fun-Kube
git fetch origin && git reset --hard origin/main
```

**Clone ex-novo:**

```bash
git clone https://github.com/OpiNOC/Fun-Kube /root/Fun-Kube
cd /root/Fun-Kube
```

Dopo aver ottenuto il codice aggiornato, eseguire direttamente:

```bash
./fun-kube up
```

Il tool si auto-configura da solo. Non servono altri comandi.

---

## Stato del progetto

| Componente                    | Stato                          |
|-------------------------------|--------------------------------|
| fun-kube (entry point)        | ✓ auto-bootstrap venv Python   |
| fun_kube/config.py            | ✓ parsing, validazione, topologia |
| fun_kube/preflight.py         | ✓ local + SSH checks           |
| fun_kube/runner.py            | ✓ inventory, sequenza playbook, Ctrl+C |
| fun_kube/cli.py               | ✓ up, check-deps, reset, diagnose, Ctrl+C |
| fun_kube/deps.py              | ✓ check + auto-install tools   |
| ansible/roles/common          | ✓ testato                      |
| ansible/roles/containerd      | ✓ testato (fix config v2.x)    |
| ansible/roles/kubeadm         | ✓ testato                      |
| ansible/roles/calico          | ✓ testato (server-side apply)  |
| ansible/roles/metrics-server  | ✓ testato                      |
| ansible/roles/cert-manager    | ✓ testato                      |
| ansible/roles/cert-renewal    | ✓ testato                      |
| ansible/roles/local-path-provisioner | ✓ testato              |
| ansible/roles/keepalived      | ✓ testato (Test 4+5)           |
| ansible/roles/metallb         | scaffolding — da completare (Test 6) |
| ansible/roles/traefik         | scaffolding — da completare (Test 7) |
| ansible/roles/nginx-proxy-manager | scaffolding — da completare (Test 8) |
| ansible/roles/longhorn        | ✓ testato (Test 9)                   |
| .env.example                  | ✓ 3 CP + 3 worker (placeholder)|
| bootstrap-setup.sh            | legacy — non più necessario    |

---

## Topologie supportate

| Topologia      | CP  | Worker | Keepalived | LOCAL_NODE |
|----------------|-----|--------|------------|------------|
| Mononodo       | 1   | 0      | no         | true (bootstrap=nodo) o false (SSH) |
| Single CP      | 1   | N      | no         | false      |
| HA multi CP    | 3+  | N      | sì         | false      |

Rilevamento automatico dal numero di nodi in .env:
- 1 CP, 0 worker → `single-node` (taint NoSchedule rimosso, local-path-provisioner)
- 1 CP, N worker → `single-cp`
- 3+ CP          → `ha` (keepalived obbligatorio)

**LOCAL_NODE=true** — la macchina bootstrap È il nodo (Ansible usa `ansible_connection=local`).
Utile per test e lab con risorse limitate.

---

## Primo avvio su macchina pulita (Ubuntu 24.04)

```bash
git clone https://github.com/OpiNOC/Fun-Kube /root/Fun-Kube
cd /root/Fun-Kube
cp .env.example .env
# editare .env
./fun-kube up
```

Al primo avvio lo script:
1. Installa `python3-venv` via apt se mancante
2. Crea `.venv/` locale con typer, rich, python-dotenv
3. Installa ansible via apt, kubectl via download, helm via get-helm-3
4. Installa la collection ansible `community.general`
5. Esegue il provisioning

Tutto questo avviene automaticamente senza intervento manuale.

---

## Piano di test

### Test 1 — Mononodo LOCAL_NODE ✓ COMPLETATO (2026-04-16)
**Configurazione:** bootstrap=nodo, LOCAL_NODE=true, 1 CP, 0 worker
**Macchina:** Fun-Kube-Bootstrap (172.30.232.70)
**Risultato:** PASS — cluster ready, Calico+metrics-server+cert-manager+local-path up

Bug trovati e fixati:
- `python3-venv` non preinstallato su Ubuntu 24 → auto-install via apt
- Inventory path relativo → ansible girava nella dir sbagliata → path assoluto
- containerd config aveva `disabled_plugins=["cri"]` (Ubuntu default) → rigenerazione idempotente
- `kubectl apply` fallisce su CRD grandi (>262KB) → `--server-side --force-conflicts`
- `set -o pipefail` non supportato da `/bin/sh` → `executable: /bin/bash`
- `when:` a livello play non è valido in Ansible → spostato su task/include_role
- `longhorn_enabled | bool` senza `default(false)` → undefined error
- Preflight fallisce al secondo run (porte occupate dal cluster) → skip se già inizializzato
- containerd riavviato ogni run per bug `'changed' in 'unchanged'` → usato "updated"/"ok"
- swap/sysctl sempre reported changed → check idempotente
- untaint fallisce se taint già rimosso → `failed_when` con 'not found'

### Test 1b — Mononodo LOCAL_NODE (macchina pulita, secondo run) — IN CORSO
**Bug trovato:** `Persistent=true` nel timer `k8s-cert-renew.timer` causa scatto immediato al primo
enable su macchina nuova (nessun record di ultima esecuzione). Lo script abbatte l'API server per
~50s (sposta manifest, sleep 20, ripristina, sleep 30) → `local-path-provisioner` fallisce con
`connection refused` su `6443`.
**Fix applicato:**
- Rimosso `Persistent=true` dal timer (causa radice)
- Aggiunto wait-for-API-server + retry in `local-path-provisioner/tasks/main.yml` (difesa in profondità)

### Test 2 — Mononodo con bootstrap esterna ✓ COMPLETATO (2026-04-16)
**Configurazione:** LOCAL_NODE=false, 1 CP, 0 worker, SSH da bootstrap separata
**Macchine:** Fun-Kube-Bootstrap (172.30.232.70) + 1 nodo separato
**Risultato:** PASS — cluster ready

Bug trovati e fixati:
- Preflight `kernel: br_netfilter` e `kernel: overlay` falliscono su macchina pulita → check
  cambiato da `lsmod` a `lsmod || modinfo` (modulo disponibile basta, `common` lo carica)

### Test 3 — Single CP con worker
**Configurazione:** 1 CP + N worker, SSH da bootstrap esterna
**Stato:** SALTATO — topologia coperta implicitamente dal Test 5 (HA funziona, single-cp è subset)

### Test 4 — HA multi CP (solo CP, senza worker) ✓ COMPLETATO (2026-04-17)
**Configurazione:** 3 CP, keepalived VIP, nessun worker
**Macchine:** bootstrap (.70) + 3 CP (.71-.73)
**Risultato:** PASS — cluster HA funzionante, Calico+metrics-server+cert-manager up

Bug trovati e fixati (pre-test):
- `bootstrap-kubeconfig.yml` fallisce con "connection refused" su VIP keepalived →
  aggiunto wait-for-API (`kubectl cluster-info`, retries: 30, delay: 10s) +
  `--validate=false` su tutti i `kubectl apply` per evitare download schema OpenAPI
- Ctrl+C non interrompeva il provisioning → `runner.py` usa `Popen` + trap
  `KeyboardInterrupt` per terminare `ansible-playbook`; `cli.py` intercetta in tutti
  i punti critici e stampa messaggio pulito (exit 130)

Nota: errore non bloccante su calico role (`kubectl wait` fallisce con "no matching
resources found" perché il Calico operator non ha ancora creato i pod al momento
del check). L'installazione va a buon fine, ma il task è ignorato con `ignore_errors`.
Da fixare: vedi sezione Note Tecniche.

### Test 5 — HA multi CP completo (3 CP + 3 worker) ✓ COMPLETATO (2026-04-17)

**Configurazione:** 3 CP + 3 worker, keepalived VIP
**Macchine:** bootstrap (.70) + 3 CP (.71-.73) + 3 worker (.74-.76)
**Risultato:** PASS — cluster HA completo funzionante, tutti e 6 i nodi Ready

Note:
- Worker nodes mostrano ROLES=`<none>`: comportamento standard Kubernetes (kubeadm non
  assegna label di ruolo ai worker). Opzionale: `kubectl label node worker{1,2,3} node-role.kubernetes.io/worker=`
- Kubernetes v1.35.4, containerd 2.2.3, Ubuntu 24.04.4 LTS

### Test 6 — MetalLB
**Configurazione:** cluster HA (3 CP + 3 worker), `METALLB_ENABLED=true`, IP pool su subnet lab
**Stato:** DA ESEGUIRE

### Test 7 — Ingress Traefik
**Configurazione:** cluster HA, `INGRESS_ENABLED=true`, `INGRESS_TYPE=traefik`
**Dipendenza:** MetalLB (Test 6) per LoadBalancer IP
**Stato:** DA ESEGUIRE

### Test 8 — Ingress Nginx Proxy Manager
**Configurazione:** cluster HA, `INGRESS_ENABLED=true`, `INGRESS_TYPE=nginx-proxy-manager`
**Dipendenza:** MetalLB (Test 6) per LoadBalancer IP
**Stato:** DA ESEGUIRE

### Test 9 — Longhorn ✓ COMPLETATO (2026-04-17)
**Configurazione:** cluster HA (3 CP + 3 worker), `LONGHORN_ENABLED=true`, `LONGHORN_UI_NODEPORT=30080`
**Macchine:** bootstrap (.70) + 3 CP (.71-.73) + 3 worker (.74-.76)
**Risultato:** PASS — tutti i pod Running, StorageClass longhorn (default), UI su NodePort 30080

Implementazione e bug trovati/fixati:
- nfs-common + open-iscsi auto-installati su tutti i nodi
- apply client-side (`kubectl apply --validate=false`): evita conflitti con i campi
  gestiti da longhorn-manager (es. conversion webhook CA bundle) che SSA con
  --force-conflicts avrebbe sovrascritto rompendo i CRD
- idempotenza: controlla versione installata prima dell'apply; skip se già
  alla versione target; fail con messaggio chiaro se versione incompatibile
- patch longhorn-frontend → NodePort idempotente (skip se già NodePort)
- versione risolta automaticamente da GitHub releases API se non impostata in .env
- Bug: default v1.7.2 hardcoded + fetch latest (v1.11.1) = upgrade non supportato
  da Longhorn (max 1 minor version alla volta) → rimosso default hardcoded,
  versione sempre risolta da Python (GitHub API)

---

## Struttura del progetto

```
Fun-Kube/
├── fun-kube                  # entry point (auto-bootstrap venv)
├── pyproject.toml
├── fun_kube/
│   ├── cli.py                # comandi: up, check-deps, reset, diagnose
│   ├── config.py             # parsing .env, validazione, topologia
│   ├── deps.py               # check + auto-install tool bootstrap
│   ├── preflight.py          # preflight checks (local + SSH)
│   └── runner.py             # inventory + sequenza playbook Ansible
├── .env.example              # template (committato)
├── .env                      # config locale (gitignored)
├── .venv/                    # venv Python (gitignored, generato al primo run)
├── ansible/
│   ├── ansible.cfg           # roles_path, pipelining, forks, timeout
│   ├── playbooks/
│   │   ├── bootstrap.yml           # common + containerd + kubeadm
│   │   ├── keepalived.yml          # HA only
│   │   ├── kubeadm-init.yml        # init primo CP
│   │   ├── control-plane-join.yml  # join CP aggiuntivi (HA)
│   │   ├── worker-join.yml         # join workers
│   │   ├── calico.yml
│   │   ├── untaint-cp.yml          # mononodo/single-cp senza worker
│   │   ├── metrics-server.yml
│   │   ├── cert-manager.yml
│   │   ├── cert-renewal.yml        # systemd timer rinnovo certificati
│   │   ├── local-path-provisioner.yml  # StorageClass default (single-node)
│   │   ├── bootstrap-kubeconfig.yml    # SA kubeconfig non-expiring
│   │   ├── metallb.yml
│   │   ├── ingress.yml
│   │   └── longhorn.yml
│   └── roles/
│       ├── common/                 # sysctl, moduli kernel, swap, chrony, iscsid
│       ├── containerd/             # containerd.io + SystemdCgroup (v2.x aware)
│       ├── kubeadm/                # kubelet + kubeadm + kubectl
│       ├── calico/
│       ├── metrics-server/
│       ├── cert-manager/
│       ├── cert-renewal/
│       ├── local-path-provisioner/
│       ├── keepalived/
│       ├── metallb/
│       ├── traefik/
│       ├── nginx-proxy-manager/
│       └── longhorn/
└── output/                         # gitignored — generato da fun-kube up
    ├── inventory.ini
    ├── cluster-info.txt
    └── kubeconfig-admin
```

---

## Configurazione (.env)

```bash
cp .env.example .env
```

Variabili principali:

```
CLUSTER_NAME=mio-cluster

# Nodi (ripetere per N nodi)
NODE_1_IP=192.168.1.10
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=cp1

# Modalità locale (bootstrap = nodo, no SSH)
LOCAL_NODE=false

# SSH (non usato se LOCAL_NODE=true)
SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

# Kubernetes
K8S_VERSION=latest          # o es. "1.31.0"
POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
CNI=calico

# Addon opzionali
METALLB_ENABLED=false
METALLB_IP_POOL=192.168.1.200-192.168.1.220
INGRESS_ENABLED=false
INGRESS_TYPE=traefik        # traefik | nginx-proxy-manager
LONGHORN_ENABLED=false
LONGHORN_RWX=false

# HA only
KEEPALIVED_ENABLED=false
KEEPALIVED_VIP=192.168.1.100
KEEPALIVED_INTERFACE=eth0
```

CIDR da tenere non sovrapposti: `POD_CIDR`, `SERVICE_CIDR`, `METALLB_IP_POOL`.
`config.py` lo verifica automaticamente all'avvio.

---

## CLI

```bash
./fun-kube up [.env]          # provisiona il cluster
  --dry-run                   # solo validazione, nessuna modifica
  --debug                     # output verboso (ansible -vv)
  --skip-checks               # salta preflight

./fun-kube check-deps         # verifica + installa tool bootstrap
  --verbose                   # mostra versioni

./fun-kube reset [.env]       # distrugge il cluster (kubeadm reset)
  --yes                       # salta conferma

./fun-kube diagnose [.env]    # stato nodi (kubelet, k8s, disk, ram)
```

---

## Flusso operativo (fun-kube up)

```
0. auto-install    python3-venv → .venv, ansible, kubectl, helm, community.general
1. check-deps      verifica che tutti i tool siano disponibili
2. config          parsing .env, validazione CIDR, rilevamento topologia
3. preflight       checks su tutti i nodi (skip se cluster già inizializzato)
4. provisioning    playbook Ansible in sequenza:
     bootstrap.yml → [keepalived.yml] → kubeadm-init.yml
     → [control-plane-join.yml] → [worker-join.yml]
     → calico.yml → [untaint-cp.yml]
     → metrics-server.yml → cert-manager.yml → cert-renewal.yml
     → [local-path-provisioner.yml]  (solo single-node)
     → bootstrap-kubeconfig.yml
5. output          fetch admin.conf, aggiorna ~/.bashrc, cluster-info.txt
```

---

## Kubeconfig prodotti

| File                           | Tipo              | Scadenza |
|--------------------------------|-------------------|----------|
| /root/.kube/<cluster>          | ServiceAccount token | non scade |
| /root/.kube/<cluster>-admin    | admin.conf backup | ~1 anno  |

Il kubeconfig primario (SA token) è quello da usare normalmente.
Quello admin è un backup di emergenza se il cluster è parzialmente rotto.

Dopo il provisioning:
```bash
source ~/.bashrc          # attiva KUBECONFIG e alias k=kubectl
kubectl get nodes
```

---

## Note tecniche

**containerd su Ubuntu 24.04**
Ubuntu preinstalla containerd con `disabled_plugins = ["cri"]`.
Il role containerd rigenera il config da `containerd config default` + `SystemdCgroup=true`
solo se il file esistente è diverso da quello atteso (idempotente).

**CRD di grandi dimensioni (Calico)**
`kubectl apply` fallisce su CRD > 262KB per limite annotation.
Soluzione: `kubectl apply --server-side --force-conflicts`.

**Versione Kubernetes "latest"**
Risolta una volta sola in Python da `https://dl.k8s.io/release/stable.txt`
e passata ad Ansible come `k8s_version_resolved`.

**Calico: "no matching resources found" al wait dei pod**
Il task `Wait for Calico pods to be running` esegue `kubectl wait --for=condition=Ready pod -l k8s-app=calico-node -n calico-system --timeout=300s` subito dopo l'apply del manifest dell'operatore Calico. In quel momento l'operatore non ha ancora riconciliato e i pod `calico-node` non esistono ancora → `kubectl wait` fallisce con `error: no matching resources found` (non "pod non ready", ma "nessun pod trovato").
Il task usa `ignore_errors: yes` quindi l'installazione prosegue e Calico si avvia correttamente pochi secondi dopo.

**Fix da applicare:** aggiungere prima del `kubectl wait` un task che attende la creazione effettiva dei pod (con retry), ad esempio:
```yaml
- name: Wait for Calico operator to create calico-node pods
  shell: kubectl get pods -l k8s-app=calico-node -n calico-system --no-headers 2>/dev/null | wc -l
  register: calico_pod_count
  retries: 30
  delay: 10
  until: calico_pod_count.stdout | int > 0
  changed_when: false
```
Oppure attendere che il DaemonSet `calico-node` esista: `kubectl wait daemonset/calico-node -n calico-system --for=jsonpath='{.status.desiredNumberScheduled}' --timeout=120s`.

**Idempotenza**
Ogni run può essere rieseguito senza danni:
- kubeadm init: skip se `/etc/kubernetes/admin.conf` esiste
- preflight: skip se cluster già inizializzato
- containerd: rigenera config solo se diverso
- swap: skip se già disabilitato
- untaint: ok se taint già rimosso
