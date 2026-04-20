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
| fun_kube/config.py            | ✓ parsing, validazione, topologia, gap detection NODE_N |
| fun_kube/preflight.py         | ✓ local + SSH checks           |
| fun_kube/runner.py            | ✓ inventory, sequenza playbook, Ctrl+C; output addon (MetalLB, Longhorn) |
| fun_kube/cli.py               | ✓ up (--yes), check-deps, reset, diagnose; riepilogo nodi + addon + conferma |
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
| ansible/roles/metallb         | ✓ testato (Test 6)                   |
| ansible/roles/traefik         | ✓ implementato — DaemonSet, LB/NodePort, dashboard, LE (Test 7) |
| ansible/roles/nginx-proxy-manager | ✓ implementato — DaemonSet, LB/NodePort, multi/single-node (Test 8) |
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
**Macchina:** Fun-Kube-Bootstrap (10.0.0.10)
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
**Macchine:** Fun-Kube-Bootstrap (10.0.0.10) + 1 nodo separato
**Risultato:** PASS — cluster ready

Bug trovati e fixati:
- Preflight `kernel: br_netfilter` e `kernel: overlay` falliscono su macchina pulita → check
  cambiato da `lsmod` a `lsmod || modinfo` (modulo disponibile basta, `common` lo carica)

### Test 3 — Single CP con worker
**Configurazione:** 1 CP + N worker, SSH da bootstrap esterna
**Stato:** SALTATO — topologia coperta implicitamente dal Test 5 (HA funziona, single-cp è subset)

### Test 4 — HA multi CP (solo CP, senza worker) ✓ COMPLETATO (2026-04-17)
**Configurazione:** 3 CP, keepalived VIP, nessun worker
**Macchine:** bootstrap (.10) + 3 CP (.11-.13)
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
**Macchine:** bootstrap (.10) + 3 CP (.11-.13) + 3 worker (.21-.23)
**Risultato:** PASS — cluster HA completo funzionante, tutti e 6 i nodi Ready

Note:
- Worker nodes mostrano ROLES=`<none>`: comportamento standard Kubernetes (kubeadm non
  assegna label di ruolo ai worker). Opzionale: `kubectl label node worker{1,2,3} node-role.kubernetes.io/worker=`
- Kubernetes v1.35.4, containerd 2.2.3, Ubuntu 24.04.4 LTS

### Test 10 — Full cluster da nodi puliti (regression) ✓ COMPLETATO (2026-04-17)
**Configurazione:** 3 CP + 3 worker, keepalived, Longhorn
**Macchine:** bootstrap (.10) + CP (.11-.13) + worker (.21-.23)
**Risultato:** PASS dopo 2 retry (cluster operativo)

Bug trovati e fixati:
- `LONGHORN_UI_NODEPORT` non valorizzato in `.env` → default era `0` → patch saltata → servizio non esposto
  **Fix:** default cambiato a `30080` in `config.py` e `.env.example`
- `kubectl wait` Calico timeout 300s su cluster HA a 6 nodi → fatal ma cluster poi parte
  **Fix:** `ignore_errors: true` sul task (DaemonSet viene già atteso con retry prima)
- `Create fun-kube-admin ServiceAccount` fallisce con "connection refused" su VIP keepalived
  dopo che `kubectl cluster-info` era già passato → micro-flap sul VIP tra i due task
  **Fix:** `retries: 10, delay: 10` su SA/ClusterRoleBinding/Secret apply in `bootstrap-kubeconfig.yml`
- `Verify longhorn-rwx StorageClass` fallisce: playbook verificava ma non creava mai la SC
  **Fix:** aggiunto task `Create longhorn-rwx StorageClass` prima della verifica; SC identica a
  `longhorn` + `nfsOptions: "vers=4.1,noresvport"` per RWX via share-manager; verifica ora senza
  `ignore_errors` (deve essere presente)

---

### Test 6 — MetalLB ✓ COMPLETATO (2026-04-17)
**Configurazione:** cluster HA (3 CP + 3 worker), `METALLB_ENABLED=true`, `METALLB_IP_POOL=10.0.0.200-10.0.0.201`
**Risultato:** PASS — IPAddressPool e L2Advertisement applicati, pool corretto

Bug trovati e fixati:
- `metallb.yml` non era nella sequenza playbook di `runner.py` → aggiunto
- `metallb_enabled`, `metallb_ip_pool`, `metallb_version` non erano in `_build_extra_vars` → aggiunti
- Versione MetalLB ora risolta da GitHub API (come Longhorn); override via `METALLB_VERSION` in `.env`
- Apply del manifest con `--validate=false` (coerente con altri role)
- Apply di `IPAddressPool`/`L2Advertisement` con `retries: 12, delay: 10` — i CRD MetalLB si
  registrano pochi secondi dopo l'avvio dei pod; senza retry l'apply fallisce se il pod-wait
  torna `ignore_errors`
- Idempotenza: version check via immagine `controller` deployment; skip manifest apply se già
  a target version; `changed_when` accurato su tutti gli apply
- Output: pool MetalLB e info Longhorn aggiunti a `cluster-info.txt` e file manutenzione

### Test 7 — Ingress Traefik ✓ COMPLETATO (2026-04-20)
**Configurazione:** cluster HA (3 CP + 3 worker), `INGRESS_ENABLED=true`, `INGRESS_TYPE=traefik`, `INGRESS_SERVICE_TYPE=auto`
**Dipendenza:** MetalLB (Test 6) per LoadBalancer IP
**Risultato:** PASS — DaemonSet 3 pod Running, LoadBalancer IP 10.0.0.200, IngressClass `traefik` registrata

Bug trovati e fixati:
- Helm non è installato sui nodi CP (è sulla bootstrap) → tutte le task Traefik usano
  `delegate_to: localhost` + `KUBECONFIG: /root/.kube/{{ cluster_name }}`

### Test 8 — Ingress Nginx Proxy Manager ✓ COMPLETATO (2026-04-20)
**Configurazione:** cluster HA (3 CP + 3 worker), `INGRESS_ENABLED=true`, `INGRESS_TYPE=nginx-proxy-manager`, `INGRESS_SERVICE_TYPE=auto`
**Dipendenza:** MetalLB (Test 6) + Longhorn (Test 9) per RWX storage
**Risultato:** PASS — DaemonSet 3 pod Running, MariaDB running, LoadBalancer IP 10.0.0.200,
PVC npm-shared RWX 10Gi (longhorn-rwx), PVC npm-mariadb-data RWO 5Gi (longhorn)

### Test 9 — Longhorn ✓ COMPLETATO (2026-04-17)
**Configurazione:** cluster HA (3 CP + 3 worker), `LONGHORN_ENABLED=true`, `LONGHORN_UI_NODEPORT=30080`
**Macchine:** bootstrap (.10) + 3 CP (.11-.13) + 3 worker (.21-.23)
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
INGRESS_TYPE=nginx-proxy-manager  # traefik | nginx-proxy-manager
INGRESS_SERVICE_TYPE=auto         # auto | loadbalancer | nodeport

# Traefik (solo se INGRESS_TYPE=traefik)
TRAEFIK_LB_IP=                    # vuoto = MetalLB auto-assign
TRAEFIK_HTTP_NODEPORT=30080
TRAEFIK_HTTPS_NODEPORT=30443
TRAEFIK_IS_DEFAULT_CLASS=true
TRAEFIK_DASHBOARD_HOST=           # vuoto = solo port-forward
TRAEFIK_ACME_EMAIL=               # vuoto = guida in output/

# Nginx Proxy Manager (solo se INGRESS_TYPE=nginx-proxy-manager)
NPM_LB_IP=                        # vuoto = MetalLB auto-assign
NPM_HTTP_NODEPORT=30080
NPM_HTTPS_NODEPORT=30443
NPM_ADMIN_NODEPORT=30081
NPM_DB_PASSWORD=T1sh-PwD-Sh0ulD-B3-Ch4nGeD-NOW
LONGHORN_ENABLED=false
LONGHORN_RWX=false
LONGHORN_UI_NODEPORT=31080        # (era 30080 — cambiato per non collidere con ingress)

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
  --yes / -y                  # salta la conferma interattiva (es. automazione)

./fun-kube check-deps         # verifica + installa tool bootstrap
  --verbose                   # mostra versioni

./fun-kube reset [.env]       # distrugge il cluster (kubeadm reset)
  --yes                       # salta conferma

./fun-kube diagnose [.env]    # stato nodi (kubelet, k8s, disk, ram)
```

Prima del provisioning viene sempre mostrato un riepilogo con:
- Tabella nodi: hostname, IP, ruolo
- CIDRs, SSH key, VIP keepalived (se HA)
- Addon abilitati con dettagli:
  - Longhorn: versione, RWX, URL dashboard (`http://<VIP>:<nodeport>`)
  - MetalLB: IP pool
- Richiesta di conferma esplicita (`Procedere? [y/N]`)

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

**Fix applicato:** `ignore_errors: true` sul task `Wait for Calico pods to be running`. In cluster HA a 6 nodi il timeout 300s non è sempre sufficiente; il cluster si avvia correttamente comunque. Il DaemonSet viene atteso con retry prima del `kubectl wait`, quindi Calico è operativo al termine del play.

**Idempotenza**
Ogni run può essere rieseguito senza danni:
- kubeadm init: skip se `/etc/kubernetes/admin.conf` esiste
- preflight: skip se cluster già inizializzato
- containerd: rigenera config solo se diverso
- swap: skip se già disabilitato
- untaint: ok se taint già rimosso
