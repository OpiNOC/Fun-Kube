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
  • supportare addon opzionali: MetalLB, Ingress (Traefik / NPM), Longhorn, DN-essence
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
| fun_kube/preflight.py         | ✓ local + SSH checks (nodi nuovi anche su cluster esistente); confronto .env ↔ cluster |
| fun_kube/runner.py            | ✓ inventory, sequenza playbook (longhorn prima di ingress), Ctrl+C; output addon; longhorn_replicas dinamico |
| fun_kube/cli.py               | ✓ up, check-deps, reset (unmount pre-kubeadm reset), diagnose (nodi + addon + keepalived MASTER/BACKUP), upgrade; config_warnings |
| fun_kube/upgrade.py           | ✓ upgrade kubeadm patch e +1 minor, ripresa, check Longhorn, mononodo (Test 16-21) |
| fun_kube/deps.py              | ✓ check + auto-install tools (kubectl pinnato, Helm 4) |
| fun_kube/versions.py          | ✓ versioni componenti, manifest vendored, compatibilità addon ↔ K8s |
| ansible/roles/common          | ✓ testato                      |
| ansible/roles/containerd      | ✓ testato (fix config v2.x)    |
| ansible/roles/kubeadm         | ✓ testato                      |
| ansible/roles/calico          | ✓ testato (server-side apply)  |
| ansible/roles/metrics-server  | ✓ testato                      |
| ansible/roles/cert-manager    | ✓ testato                      |
| ansible/roles/cert-renewal    | ✓ testato                      |
| ansible/roles/local-path-provisioner | ✓ testato              |
| ansible/roles/keepalived      | ✓ testato (Test 4+5); health check API server sul VIP (Test 17) |
| ansible/roles/metallb         | ✓ testato (Test 6)                   |
| ansible/roles/traefik         | ✓ implementato — DaemonSet, LB/NodePort, dashboard, LE, externalTrafficPolicy:Local (Test 7) |
| ansible/roles/nginx-proxy-manager | ✓ implementato — DaemonSet, LB/NodePort, multi/single-node, externalTrafficPolicy:Local (Test 8) |
| ansible/roles/longhorn        | ✓ testato (Test 9)                   |
| ansible/roles/monitoring      | implementato — kube-prometheus-stack, Loki, Alloy, dashboard log, check capienza Longhorn — DA TESTARE |
| ansible/roles/control-plane-metrics | implementato — metriche scheduler/controller-manager/etcd/kube-proxy su 0.0.0.0 — DA TESTARE |
| ansible/roles/dn-essence      | ✓ implementato — Helm OCI, NodePort/ClusterIP, default enabled; fix delegate_to: localhost |
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
3. Installa ansible via apt, kubectl via download (stessa versione di `K8S_VERSION` default), helm 4 via get-helm-4
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

### Upgrade di Kubernetes (`fun-kube upgrade`) — 2026-10-08 — TESTATO

Design concordato: patch o +1 minor per esecuzione; drain → upgrade → uncordon un nodo
alla volta; ripresa automatica; snapshot etcd sulla bootstrap; `.env` aggiornato a fine
upgrade; mononodo/volumi a 1 replica → fermo workload con conferma esplicita.

- `fun_kube/upgrade.py` — `analyze()`: controlli preliminari senza modifiche; `execute()`:
  `keepalived.yml` (HA) + `upgrade.yml`, `node-drain-policy` Longhorn impostata ad
  `always-allow` solo se servono drain con volumi a 1 replica (ripristinata sempre)
- `ansible/playbooks/upgrade.yml` + `tasks/upgrade-node.yml` — sequenza kubeadm ufficiale;
  i comandi kubectl (drain/Ready/uncordon) partono dalla bootstrap via VIP
- `versions.K8S_COMPAT` — minor K8s testate dagli addon alle versioni fissate: blocca un
  target fuori range (`--ignore-addon-compat` per forzare). Da aggiornare con le versioni
- keepalived: `vrrp_script` su `https://127.0.0.1:6443/livez` con `weight -60` → il VIP
  lascia un CP con API server giù (utile anche fuori dall'upgrade); reload invece di
  restart al cambio di config

Test:
- **Test 16 ✓ (2026-10-08)** — `--dry-run` su cluster HA 3 CP 1.35.9: piano corretto
  (drain, nessun fermo workload, addon allineati), nessuna modifica
- **Test 17 ✓ (2026-10-08)** — failover keepalived: kube-apiserver fermato sul MASTER
  (`mv /etc/kubernetes/manifests/kube-apiserver.yaml /root/`) → il VIP passa a un altro CP,
  `kubectl` via VIP continua a rispondere; manifest ripristinato → il VIP torna
- **Test 18 ✓ (2026-10-08)** — upgrade 1.35.9 → 1.36.5 su HA **3 CP senza worker**
  (Calico, cert-manager, metrics-server, MetalLB): tutti i nodi a v1.36.5, tutti i pod su.
  Confermato che `etcdctl` è disponibile nell'immagine etcd (snapshot pre-upgrade riuscito)
- **Test 18b ✓ (2026-10-12)** — upgrade 1.35.9 → 1.36.5 su HA 3 CP + 3 worker con Longhorn
  (PVC RWO/RWX, NPM su Longhorn): tutti i 6 nodi a v1.36.5, `.env` e kubectl bootstrap aggiornati.
  **Nota**: a causa del bug sulla versione Longhorn (vedi Test 15) i controlli Longhorn
  dell'upgrade (volumi healthy, attesa repliche tra i nodi) NON erano attivi → ripetuto col
  Test 21
- **Test 21 ✓ (2026-10-12)** — upgrade di **patch** 1.35.4 → 1.35.9 su HA 3 CP + 3 worker con
  Longhorn, Traefik e workload su PVC RWX, dopo il fix della versione Longhorn: controlli Longhorn
  attivi (task "Wait for Longhorn volumes healthy" eseguito dopo ogni nodo), `.env` e kubectl
  bootstrap aggiornati (kubectl riportato da 1.36 a 1.35.9)
- **Blocco cambio VIP ✓ (2026-10-12)** — `up` con KEEPALIVED_VIP diverso: fermato prima del
  riepilogo e senza toccare i nodi, messaggio chiaro
- **Test 19 ✓ (2026-10-12)** — ripresa: interrotto due volte con Ctrl+C (la prima durante
  `kubeadm upgrade apply` su cp1), ripartito correttamente saltando i nodi già aggiornati.
  Scoperto che il task kubeadm è async: Ctrl+C ferma ansible ma **kubeadm prosegue in
  background** sul nodo → aggiunto blocco se `kubeadm` è ancora in esecuzione (`pgrep -x`),
  messaggi che spiegano di attendere, piano di ripresa con "kubelet (control plane già a ...)".
  Ogni esecuzione fa un nuovo snapshot etcd (anche le riprese): pulizia manuale di
  `output/backups/`
- **Test 13 ✓ (2026-10-13)** — mononodo remoto (1 CP, untaint) con MetalLB, NPM (PVC su
  local-path), Longhorn, DN-essence: tutto Running, tigerastatus apiserver/tiers ok su
  installazione da zero. Trovato: due StorageClass default (local-path + longhorn) → fix
- **Test 20 ✓ (2026-10-13)** — upgrade mononodo 1.35.4 → 1.35.9: piano "FERMO WORKLOAD" (solo
  cordon), conferma col nome del cluster, nodo a v1.35.9, tutti i pod Running. Riavvii attesi
  di etcd e dei controller con leader election (tigera-operator, sidecar CSI Longhorn) durante
  l'indisponibilità dell'API server

### Modifiche su cluster esistente (re-run di `up`) — 2026-10-08

Comportamento di `up` quando il `.env` cambia dopo l'installazione:

| Modifica | Comportamento |
|---|---|
| Pool MetalLB, parametri Traefik, NodePort NPM/DN-essence/Longhorn UI | applicati |
| Nuovo worker / nuovo CP in cluster già HA | join; preflight completo sul solo nodo nuovo |
| Addon abilitato dopo | installato |
| Addon messo a `false` | **non disinstallato** (playbook saltato, resta com'è) |
| Nodo rimosso dal `.env` | non toccato — warning con comandi drain/delete |
| Repliche Longhorn cambiate (nodi aggiunti) | SC `longhorn` aggiornata via ConfigMap `longhorn-storageclass`, `longhorn-rwx` ricreata; volumi esistenti invariati |
| Primi worker su cluster solo-CP | warning: i CP restano senza taint (comando per ri-applicarlo) |
| `KEEPALIVED_VIP` / endpoint (anche 1 CP → HA), `POD_CIDR`, `SERVICE_CIDR` | **errore prima di toccare i nodi** — richiede reset + up |
| `NPM_DB_PASSWORD` | **errore** — procedura ALTER USER nel file di manutenzione |
| `K8S_VERSION` su nodi già nel cluster | **errore** — usare `fun-kube upgrade --to` |
| `CONTAINERD_VERSION` su nodi già nel cluster | versione installata mantenuta + warning |

Il confronto `.env` ↔ cluster (`preflight.check_existing_cluster`) usa il kubeconfig
della bootstrap e legge `kubeadm-config`, nodi, Secret `npm-db`, StorageClass longhorn.
Se il cluster non è raggiungibile il confronto viene saltato.

Fix inclusi: il patch diretto delle repliche sulla SC `longhorn` era rifiutato dall'API
(`parameters` immutabili) → `up` falliva già alla prima installazione con < 3 nodi schedulabili.

### Aggiornamento versioni — 2026-10-08 — TESTATO

| Componente | Versione |
|---|---|
| Kubernetes (default, `K8S_VERSION`) | v1.35.9 |
| containerd.io (repo Docker) | 2.3.6 |
| Calico | v3.32.2 |
| cert-manager | v1.21.2 |
| metrics-server | v0.9.0 |
| local-path-provisioner | v0.0.37 |
| MetalLB | v0.16.0 |
| Longhorn | v1.12.1 |
| Traefik chart (default, `TRAEFIK_CHART_VERSION`) | 41.6.1 (Traefik v3.7) |
| Nginx Proxy Manager / MariaDB | 2.16.0 / 11.4 |
| Helm sulla bootstrap | 4.x |

Test:
- **Test 11 — parziale (2026-10-08)**: cluster HA 3 CP (senza worker) creato da nodi puliti con
  le nuove versioni → OK; addon presenti nel cluster: Calico, cert-manager, metrics-server,
  MetalLB. DA COMPLETARE: worker, Longhorn RWX, Traefik LB, DN-essence
- **Test 11/12 — 2026-10-12**: HA 3 CP + 2 worker da nodi puliti, MetalLB, Longhorn RWX, NPM
  (LoadBalancer), DN-essence → tutto Ready/Running, `kubectl top` OK, repliche Longhorn = 2,
  PVC RWO e RWX Bound, NPM 2.16 + MariaDB 11.4 su Longhorn OK, keepalived iface auto-detect OK.
  **Bug trovato**: cp1 registrato con InternalIP = VIP keepalived (vedi Note tecniche,
  "IP del nodo esplicito"); API server ed etcd di cp1 annunciavano invece l'IP corretto →
  correzione verificabile con `up` sul cluster esistente. Traefik ancora da verificare.
- **Fix node_ip su cluster esistente ✓ (2026-10-12)** — `up` dopo il fix: cp1 InternalIP
  corretto (.21), IP Calico corretti su tutti i nodi, nessun pod riavviato
- **Aggiunta worker ✓ (2026-10-12)** — worker3 aggiunto via `up`: preflight completo solo sul
  nodo nuovo, join OK, tutti i DaemonSet presenti; repliche Longhorn 2 → 3 su `longhorn`
  (ricreata da longhorn-manager dalla ConfigMap: log "Updated the default Longhorn
  StorageClass") e `longhorn-rwx`; PVC esistenti Bound (restano a 2 repliche, come previsto)
- **Test 12** — stesso cluster con NPM al posto di Traefik
- **Test 13** — vedi sopra (mononodo remoto). LOCAL_NODE (bootstrap = nodo) non ritestato
- **Test 14 ✓ (2026-10-12)** — re-run `up -y` su cluster HA 3+3 allineato (Longhorn, Traefik,
  MetalLB, DN-essence, workload su PVC): nessun pod ricreato (UID invariati) né riavviato,
  quasi tutti i play `changed=0`; Longhorn ora salta l'apply (versione letta dalla label)
- **Test 15 ✓ (2026-10-12)**: cluster creato con il commit b31232b (K8s 1.35.4,
  Calico 3.28, cert-manager 1.17, MetalLB 0.15, Longhorn 1.11, Traefik chart 39), poi `up`
  con main. Trovati: (1) attese `kubectl wait pod --all` appese sui pod vecchi durante il
  rollout (MetalLB) → rollout status / tigerastatus; (2) Installation Calico invalida per due
  metodi di autodetection → fix sopra; (3) la versione Longhorn era letta da un'annotation
  `longhorn.io/version` inesistente (bug presente dal codice originale): sempre vuota → apply
  del manifest ad ogni `up`, nessun blocco su downgrade/salti di minor, e `upgrade` non vedeva
  Longhorn → ora label `app.kubernetes.io/version` con fallback sul tag immagine.
  Risultati addon: Calico 3.32.2 (tigerastatus Available), cert-manager 1.21.2, MetalLB 0.16.0,
  Longhorn 1.12.1 (volume healthy), Traefik chart 41.6.1 con stesso EXTERNAL-IP, dati del
  workload `web` (PVC RWX) intatti. Inoltre `reset` lascia i pacchetti k8s installati: il
  codice vecchio non riusciva a scendere di versione (bug del fallback, già corretto in main)
- **Test 15 (originale)** — upgrade da cluster esistente creato con le versioni precedenti
  (Calico 3.28→3.32, cert-manager 1.17→1.21, Longhorn 1.11→1.12, MariaDB 10.6→11.4)

### Bug fix (backport da madmin/funkube + review) — 2026-10-08
- `kubeadm`: il fallback senza versione usava `ansible_failed_result` (definito solo in `rescue:`)
  e non scattava mai → ora `register` + `when: _k8s_install_strict is failed`; unhold dei
  pacchetti prima dell'install (necessario per up/downgrade su ri-provisioning)
- kubeconfig di emergenza: `scp` falliva con `SSH_USER` non-root (`admin.conf` è 0600 root) →
  `ssh ... sudo -n cat`, con tentativo su ogni CP
- keepalived: `KEEPALIVED_INTERFACE` vuoto = auto-detect dell'interfaccia che porta l'IP del nodo
  (fallback: interfaccia della default route); prima default fisso `eth0`
- `common`: path MTU detection — se l'interfaccia ha MTU > 1500 sonda gli altri nodi con ping DF e
  porta l'MTU a ≤ 1500 (runtime + drop-in `/etc/netplan/99-fun-kube-mtu.yaml`)
- metrics-server: `--kubelet-insecure-tls` inserito nel manifest prima dell'apply (il patch
  successivo veniva annullato dall'apply al run seguente → 2 rollout ad ogni `up`)
- NPM: variabili `MARIADB_*`; StorageClass `longhorn-rwx` creata dal role NPM allineata a
  `longhorn.yml` (repliche dinamiche, `nfsOptions`)
- `METALLB_IP_POOL`: accetta range o CIDR, errore chiaro su formato non valido, check di
  sovrapposizione esatto senza espandere il range
- `diagnose`: sezione DN-essence; conteggio IP per pool MetalLB in formato CIDR
- Calico: rimosso il download inutilizzato di `custom-resources.yaml`

---

### Addon monitoring — 2026-10-13 — IN TEST

Dalla guida observability di Enrico (kube-prometheus-stack 91.8.2 / Loki 18.13.7 / Alloy 1.13.0
installati a mano sul cluster OPI, creato con Fun-Kube). Scelte:
- variabili con i nomi della guida (`GRAFANA_*`, `PROM_*`, `LOKI_*`, `ALERTMANAGER_*`) +
  `MONITORING_ENABLED` e `MONITORING_STORAGE_CLASS`; default volumi per cluster piccolo/medio
  (Prometheus 10Gi/8GB, Loki 10Gi, Grafana 1Gi, Alertmanager 1Gi = 22Gi) invece dei valori
  della guida per cluster grandi (30/20/5/2 = 57Gi), riportati nel `.env.example`
- esposizione (rivista con Marco): default prudente `GRAFANA_EXPOSE=auto` = loadbalancer
  (MetalLB, `GRAFANA_LB_IP=auto` o IP del pool, `GRAFANA_ALLOWED_CIDRS` →
  loadBalancerSourceRanges) altrimenti nodeport (30300); `clusterip` esplicito per NPM nel
  cluster (a fine `up` il nome `.svc.cluster.local`) o con `GRAFANA_TRAEFIK_INGRESS=true` per
  il virtual host Traefik — flag dichiarativo nel `.env` invece di una domanda interattiva
  (funziona con `up -y` e resta gestito alle esecuzioni successive). `GRAFANA_DOMAIN` solo se
  un proxy pubblica Grafana (root_url). A fine `up` i valori reali (IP MetalLB letto dal Service)
- password Grafana: `.env` > Secret esistente > generata
- adozione dell'installazione manuale: stessi nomi; StorageClass creata solo se assente; CRD
  Prometheus Operator applicati server-side prima dell'upgrade (`helm show crds`); errore se le
  dimensioni dei volumi nel `.env` differiscono dai PVC esistenti
- **metriche del control plane**: su OPI scattavano da 13 giorni KubeSchedulerInstanceUnreachable,
  KubeControllerManagerInstanceUnreachable, KubeProxyInstanceUnreachable, TargetDown,
  etcdInsufficientMembers, etcdMembersDown (kubeadm le espone solo su 127.0.0.1) →
  `control-plane-metrics.yml`: `kubeadm-config` (persistenza per upgrade e join), ConfigMap
  kube-proxy, manifest statici un CP alla volta con attesa
- controllo capienza Longhorn prima dell'installazione (`files/longhorn_capacity.py`): i
  worker del lab (dischi 47 GiB → ~31 GiB prenotabili, ~93 in tutto) bastano per i default
  (44 GiB) ma NON per i valori da cluster grande (114 GiB)
- controllo dello spazio anticipato (richiesta di Marco: `LOKI_STORAGE=1000Gi` passava il
  dry-run): `up --dry-run` ora esegue anche il preflight (sola lettura); cluster nuovo → stima
  dal filesystem di `/var/lib` dei nodi schedulabili (70%, default Longhorn); cluster esistente
  → `check_existing_cluster` con i dati reali di Longhorn (stesso script `longhorn_capacity.py`,
  funzione `place()`)
- limiti inotify (`/etc/sysctl.d/99-inotify.conf`) nel role `common` per tutti i cluster
- versioni: kube-prometheus-stack 92.3.0 (91→92: solo nodeSelector linux, un riavvio),
  Loki 18.15.1, Alloy 1.13.1

Verificato offline: risoluzione `auto` e validazioni (12 scenari), patch kubeadm-config /
kube-proxy (formato v1beta4, idempotente), replace dei manifest (idempotente, non tocca
`--listen-client-urls`), check capienza (5 scenari), `helm template` dei 4 chart con i values
generati in 5 scenari (Service/Ingress Grafana, root_url, retention, volumi, Slack, toleration).

Primo giro nel lab (2026-10-13, cluster nuovo HA 3 CP + 3 worker con NPM, Longhorn, MetalLB):
- `control-plane-metrics.yml` eseguito senza errori sul cluster reale
- worker con orologio indietro di ~1 giorno (VM ripristinate da snapshot con RAM) → apt
  rifiutava i repository → controllo dell'orologio nel preflight + sync NTP prima di apt +
  chrony `makestep 1 -1`
- `--dry-run` non eseguiva il preflight (un `LOKI_STORAGE=1000Gi` passava) → ora lo esegue,
  con stima dello spazio Longhorn
- la stima non contava i volumi NPM creati prima nello stesso `up` (dry-run OK, poi stop sul
  controllo reale: 18,2 GiB per worker) → stima al netto di NPM; default NPM ridotti
- `helm upgrade` falliva: chiave `values` nel loop interpretata da Jinja come metodo `.values()`
  del dict → rinominata; ora l'intero role è verificato eseguendolo con kubectl/helm finti
- installazione completata (volumi ridotti 6+6+1+1 GiB; NPM ha mantenuto i volumi vecchi 5+10
  GiB senza errori): `diagnose` → **nessun alert attivo oltre a Watchdog** (metriche del
  control plane OK, i falsi allarmi di OPI non compaiono), Grafana su LoadBalancer MetalLB con
  l'IP mostrato a fine `up`, 4/4 PVC Bound, 20/20 pod Running. Da verificare in Grafana:
  dashboard "Log dei pod", Compute Resources, eventi in Loki, Alloy su tutti i nodi;
  restrizione `GRAFANA_ALLOWED_CIDRS`

Test da fare:
- **Test 22** — lab HA: completare l'installazione, poi verificare in `diagnose` nessun alert
  attivo oltre a Watchdog (metriche control plane), IP Grafana mostrato a fine `up`,
  dashboard "Log dei pod", eventi in Loki, `GRAFANA_ALLOWED_CIDRS`
- **Test 23** — lab: valori da cluster grande (30/20/5/2) → `up` deve fermarsi sul controllo
  di capienza
- **Test 24** — `fun-kube upgrade` su cluster con monitoring: le metriche del control plane
  restano su 0.0.0.0 dopo `kubeadm upgrade`
- **Test 25** — cluster OPI: adozione dell'installazione manuale (prima `--dry-run`), con
  `GRAFANA_EXPOSE=clusterip`, `GRAFANA_DOMAIN=watch-k8s.opiquad.it` e i volumi attuali
  (`PROM_STORAGE=30Gi PROM_RETENTION_SIZE=25GB LOKI_STORAGE=20Gi GRAFANA_STORAGE=5Gi
  ALERTMANAGER_STORAGE=2Gi`)

## Prossimi passi / idee (aggiornato 2026-10-13)

- **Test residui**: reset con il nuovo arresto pulito dei workload (verifica nessun processo
  in stato D), LOCAL_NODE (bootstrap = nodo)
- **Calico APIServer**: aggiunto e testato su cluster esistente (2026-10-12): tigerastatus
  apiserver/calico/ippools/tiers tutti Available, `kubectl get tiers` OK, API
  `projectcalico.org/v3` disponibili (i pod stanno in `calico-system`); verificato anche su
  installazione da zero (Test 13)
- **Load worker2/worker3 ~3**: causa trovata (processi bloccati su NFS dopo un reset) → nuovo
  `reset` con arresto pulito dei workload e `--reboot`. DA TESTARE: reset del cluster HA con
  workload su PVC RWX, poi verificare che nessun nodo abbia processi in stato D
- **Monitoring**: completare i test 22-25 (sopra); poi eventuali ServiceMonitor per MetalLB,
  Traefik, cert-manager
- **Altri nuovi componenti**: da definire (il monitoring era il primo)
- **Headlamp** (presente in madmin/funkube): da valutare dopo il monitoring
- **Disco dedicato a Longhorn** (`LONGHORN_DISK=/dev/sdb`): evita di condividere lo spazio con il
  sistema e la riserva del 30% sul filesystem di root
- **Disinstallazione addon** (`fun-kube addon uninstall <nome>`): oggi mettere un addon a
  `false` lo lascia installato. I playbook uninstall di madmin vanno corretti (usano `helm`
  sui CP, dove non è installato)
- **Adozione di cluster esistenti** non creati da Fun-Kube (analisi del 2026-10-08):
  - fattibile solo per cluster kubeadm + Ubuntu + containerd; non per k3s/RKE2/managed
  - oggi `up` su un cluster esterno è pericoloso: riapplica Calico (doppia CNI se diversa),
    rigenera la config di containerd, imposta gli hostname, installa keepalived sul VIP
    anche se c'è già un altro LB (kube-vip/HAProxy), riapplica cert-manager/metrics-server
  - proposta: `fun-kube adopt --scan` in sola lettura (rapporto + bozza di `.env`), poi
    `.env` che indica per ogni componente se è gestito da Fun-Kube o lasciato com'è;
    primi obiettivi: diagnose, upgrade, aggiunta worker

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
│   ├── runner.py             # inventory + sequenza playbook Ansible
│   └── versions.py           # versioni componenti (unica fonte di verità)
├── scripts/
│   └── vendor-manifests.py   # riscarica i manifest vendored in roles/*/files/
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
│   │   ├── calico-apiserver.yml    # API server Calico (dopo untaint-cp)
│   │   ├── untaint-cp.yml          # mononodo/single-cp senza worker
│   │   ├── metrics-server.yml
│   │   ├── cert-manager.yml
│   │   ├── cert-renewal.yml        # systemd timer rinnovo certificati
│   │   ├── local-path-provisioner.yml  # StorageClass default (single-node)
│   │   ├── bootstrap-kubeconfig.yml    # SA kubeconfig non-expiring
│   │   ├── control-plane-metrics.yml # metriche control plane su 0.0.0.0 (monitoring)
│   │   ├── monitoring.yml          # kube-prometheus-stack, Loki, Alloy
│   │   ├── metallb.yml
│   │   ├── longhorn.yml
│   │   ├── ingress.yml
│   │   └── dn-essence.yml
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
│       ├── longhorn/
│       └── dn-essence/             # DNS rewrite manager per CoreDNS
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
K8S_VERSION=v1.35.9         # vuoto = default di versions.py, 'latest' = ultima stabile
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
KEEPALIVED_INTERFACE=           # vuoto = auto-detect (interfaccia con l'IP del nodo)
```

CIDR da tenere non sovrapposti: `POD_CIDR`, `SERVICE_CIDR`, `METALLB_IP_POOL`.
`config.py` lo verifica automaticamente all'avvio.

---

## CLI

```bash
./fun-kube up [.env]          # provisiona il cluster
  --dry-run                   # configurazione + preflight (sola lettura), nessuna modifica
  --debug                     # output verboso (ansible -vv)
  --skip-checks               # salta preflight
  --yes / -y                  # salta la conferma interattiva (es. automazione)

./fun-kube check-deps         # verifica + installa tool bootstrap
  --verbose                   # mostra versioni

./fun-kube reset [.env]       # distrugge il cluster (arresto workload + kubeadm reset)
  --yes                       # salta conferma
  --reboot                    # riavvia i nodi a fine reset

./fun-kube diagnose [.env]    # stato nodi (kubelet, k8s, disk, ram) + addon

./fun-kube upgrade --to vX.Y.Z [.env]   # upgrade Kubernetes (patch o +1 minor)
  --dry-run                   # solo controlli e piano
  --yes / -y                  # salta la conferma (non basta con fermo workload)
  --allow-downtime            # accetta il fermo workload senza conferma interattiva
  --ignore-addon-compat       # procede con addon non testati sulla versione target
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
2b. cluster check  se il cluster esiste: confronto .env ↔ cluster (endpoint, CIDR,
                   password NPM → errore; nodi/taint/repliche Longhorn → warning)
3. preflight       nodi nuovi: check completi; nodi già nel cluster: solo SSH/sudo
4. provisioning    playbook Ansible in sequenza:
     bootstrap.yml → [keepalived.yml] → kubeadm-init.yml
     → [control-plane-join.yml] → [worker-join.yml]
     → calico.yml → [untaint-cp.yml] → calico-apiserver.yml
     → metrics-server.yml → cert-manager.yml → cert-renewal.yml
     → [local-path-provisioner.yml]  (solo single-node)
     → bootstrap-kubeconfig.yml
     → [metallb.yml]
     → [longhorn.yml]     ← PRIMA di ingress (NPM usa SC longhorn/longhorn-rwx)
     → [ingress.yml]      ← Traefik o Nginx Proxy Manager
     → [dn-essence.yml]   ← default enabled; disabilitabile con DN_ESSENCE_ENABLED=false
     → [control-plane-metrics.yml → monitoring.yml]   ← MONITORING_ENABLED=true
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

**Versioni e manifest vendored**
Tutte le versioni sono in `fun_kube/versions.py` e arrivano ad Ansible come extra-vars
(`versions.ansible_vars()`). I manifest kubectl (Calico, cert-manager, metrics-server,
local-path, MetalLB, Longhorn) sono in `ansible/roles/*/files/`, generati da
`scripts/vendor-manifests.py` (che applica anche le patch, es. `--kubelet-insecure-tls`
su metrics-server). `CERT_MANAGER_VERSION`, `LOCAL_PATH_VERSION`, `METALLB_VERSION`,
`LONGHORN_VERSION` in `.env` sono ignorate con warning.
`K8S_VERSION=latest` viene risolta una volta sola in Python da `https://dl.k8s.io/release/stable.txt`
e passata ad Ansible come `k8s_version_resolved`.

**Calico API server**
Le installazioni Calico via operatore includono l'`APIServer` di default (espone
`projectcalico.org/v3` a kubectl e serve al controller dei tier); Fun-Kube non lo creava e
`tigerastatus tiers` restava Degraded ("Waiting for Tigera API server to be ready").
Ora `calico-apiserver.yml` (dopo `untaint-cp`, perché sui cluster senza worker i pod devono
girare sui CP) crea `APIServer default`, attende `tigerastatus apiserver` Available e nessun
componente Calico Degraded; altrimenti `up` si ferma (API aggregata non disponibile = errori
di discovery di kubectl). `diagnose` mostra lo stato di ogni componente Calico; `upgrade`
rifiuta di partire con componenti Calico degradati.

**Calico >= 3.30: CRD separati**
I CRD sono in `operator-crds.yaml`, separato da `tigera-operator.yaml`: vanno applicati prima,
server-side (il CRD `installations` supera 1MB). L'apply dell'`Installation` ha retry perché i CRD
appena creati possono non essere ancora Established.

**cert-manager: server-side apply**
Dalla v1.21 il CRD `clusterissuers` supera ~300KB (oltre il limite di 262KB dell'annotation
last-applied del client-side apply) → `kubectl apply --server-side --force-conflicts`.

**Traefik chart >= 41: `service.spec.type`**
Il tipo di Service va in `service.spec.type` (default del chart: `LoadBalancer`); il vecchio
`service.type` viene ignorato senza errori. L'IP fisso MetalLB usa l'annotation
`metallb.io/loadBalancerIPs` (`spec.loadBalancerIP` è deprecato), anche per NPM.

**Longhorn: upgrade automatico**
Se installato, l'apply del nuovo manifest avviene solo per upgrade di patch o +1 minor
(supportati da Longhorn); downgrade e salti di più minor falliscono con istruzioni.

**MariaDB (NPM): Recreate + auto-upgrade**
La Deployment usa `strategy: Recreate` (volume RWO: con RollingUpdate il nuovo pod resterebbe
bloccato al cambio immagine) e `MARIADB_AUTO_UPGRADE=1` per migrare datadir da 10.6 a 11.4.

**Calico: "no matching resources found" al wait dei pod**
Il task `Wait for Calico pods to be running` esegue `kubectl wait --for=condition=Ready pod -l k8s-app=calico-node -n calico-system --timeout=300s` subito dopo l'apply del manifest dell'operatore Calico. In quel momento l'operatore non ha ancora riconciliato e i pod `calico-node` non esistono ancora → `kubectl wait` fallisce con `error: no matching resources found` (non "pod non ready", ma "nessun pod trovato").
Il task usa `ignore_errors: yes` quindi l'installazione prosegue e Calico si avvia correttamente pochi secondi dopo.

**Fix applicato:** `ignore_errors: true` sul task `Wait for Calico pods to be running`. In cluster HA a 6 nodi il timeout 300s non è sempre sufficiente; il cluster si avvia correttamente comunque. Il DaemonSet viene atteso con retry prima del `kubectl wait`, quindi Calico è operativo al termine del play.

**IP del nodo esplicito (node_ip)**
keepalived aggiunge il VIP all'interfaccia del primo CP *prima* di `kubeadm init`. Senza un IP
esplicito kubelet (InternalIP), kubeadm (advertise address di API server ed etcd) e Calico
(auto-detect first-found) possono scegliere il VIP come IP del nodo: osservato nel lab
(cp1 InternalIP = VIP). Quando il VIP si sposta, l'indirizzo del nodo punta a un altro CP.
Fix: l'inventory passa `node_ip` (IP del `.env`) a ogni host → `/etc/default/kubelet`
(`--node-ip`), `kubeadm init/join --apiserver-advertise-address`, Calico
`nodeAddressAutodetectionV4: kubernetes: NodeInternalIP` (gli altri metodi sono azzerati
esplicitamente + patch merge atomica: sui cluster esistenti l'operatore aveva scritto
`firstFound: true` nello spec e `kubectl apply` fondeva i due metodi → Installation invalida,
"no more than one node address autodetection method", osservato nel Test 15). Il controllo `.env` ↔ cluster
segnala i nodi con InternalIP diverso: `up` corregge kubelet; se su un CP anche l'API server
annuncia l'IP sbagliato (annotation kubeadm sul pod kube-apiserver) serve reinstallare.

**Idempotenza**
Ogni run può essere rieseguito senza danni:
- kubeadm init: skip se `/etc/kubernetes/admin.conf` esiste
- preflight: skip se cluster già inizializzato
- containerd: rigenera config solo se diverso
- swap: skip se già disabilitato
- untaint: ok se taint già rimosso

**Volumi di default ridotti (NPM)**
NPM creava MariaDB 5Gi + dati 10Gi con 3 repliche Longhorn = 45 GiB sul cluster per dati di
pochi MB (configurazioni) più i log di accesso: nel lab lasciava ~18 GiB per worker e il
monitoring non ci stava. Ora `NPM_DB_STORAGE=1Gi`, `NPM_DATA_STORAGE=2Gi` (configurabili, 9 GiB
con 3 repliche). Su cluster esistenti `check_existing_cluster` mantiene la dimensione dei PVC
più grandi (un PVC non si riduce: l'apply fallirebbe) e consente l'espansione su Longhorn.
La riserva Longhorn del 30% resta invariata: i dati stanno sul filesystem di sistema (immagini,
log, soglie di eviction di kubelet); per la produzione l'opzione giusta sarebbe un disco
dedicato a Longhorn (idea futura: `LONGHORN_DISK`).

**Orologio dei nodi**
Nel lab (2026-10-13) i worker avevano l'orologio indietro di ~1 giorno: apt rifiutava i
repository ("Release file ... is not valid yet") e il bootstrap falliva; con un orologio
sfasato anche i certificati del cluster risultano non validi. Ora il preflight (anche in
`--dry-run`) confronta l'orologio di ogni nodo con la bootstrap ed è bloccante oltre 60s,
indicando i comandi per correggerlo; il role `common` attiva la sincronizzazione NTP
(chrony o systemd-timesyncd) prima di apt. Causa nel lab: VM ripristinate da snapshot con
RAM (l'orologio riparte da quello salvato). chrony configurato con `makestep 1 -1` (default
Ubuntu `makestep 1 3`: dopo i primi aggiornamenti recupera lo scarto solo rallentando
l'orologio, settimane per un giorno di scarto).

**Mononodo con Longhorn: una sola StorageClass default**
Su topologia single-node local-path veniva sempre marcata default, e Longhorn lo è dalla sua
ConfigMap → due StorageClass default. Ora local-path è default solo se Longhorn è disabilitato;
con Longhorn resta installata ma non default (NPM su mononodo la usa in modo esplicito).

**Reset: arresto pulito dei workload e processi bloccati**
Nel lab, dopo un reset, due worker avevano load ~3 costante: processi in stato D (`nginx` del
workload su volume RWX e il thread NFS del kernel) appesi da ore a uno share-manager Longhorn
(`nfs: server 10.106.107.181 not responding`) del cluster distrutto. Lo smontaggio lazy
nascondeva il mount ma il processo restava bloccato fino al riavvio.
Ora `reset` (`fun_kube/reset.py`): (1) se il cluster risponde, cordon di tutti i nodi,
cancellazione dei pod dei namespace applicativi e attesa che non restino mount NFS/Longhorn di
kubelet sui nodi; (2) reset dei nodi con `umount -f` prima del lazy; (3) segnala i nodi con
processi in stato D e consiglia il riavvio; `--reboot` riavvia i nodi a fine reset.

**Reset con volumi Longhorn montati**
`kubeadm reset` si bloccava indefinitamente sui worker con volumi Longhorn (iSCSI/NFS) ancora montati in `/var/lib/kubelet`.
**Fix:** il comando `reset` in `cli.py` esegue prima `systemctl stop kubelet && systemctl stop iscsid` e poi un loop `umount -f -l` su tutti i mount kubelet/longhorn/csi, prima di invocare `kubeadm reset -f`.

**Repliche Longhorn dinamiche**
La StorageClass `longhorn` e `longhorn-rwx` avevano `numberOfReplicas: "3"` hardcoded. Su cluster con meno di 3 nodi schedulabili i PVC restavano `Pending`.
**Fix:** `ClusterConfig.longhorn_replicas = min(nodi_schedulabili, 3)` in `config.py`; passato come extra var ad Ansible; `longhorn.yml` usa `{{ longhorn_replicas }}` nella SC e patcha la SC default dopo l'installazione. `config.py` emette un warning non bloccante se repliche < 3.

**Helm delegate_to: localhost**
Helm è installato solo sulla bootstrap machine, non sui nodi CP.
Tutte le task che usano `helm` nel role `traefik` usano `delegate_to: localhost` con `environment: KUBECONFIG: /root/.kube/{{ cluster_name }}`. Questo pattern vale per qualsiasi tool disponibile solo sulla bootstrap.

**Ordine playbook: longhorn prima di ingress**
NPM richiede le StorageClass `longhorn` (per MariaDB RWO) e `longhorn-rwx` (per storage condiviso RWX). `ingress.yml` deve quindi essere eseguito DOPO `longhorn.yml`. L'ordine in `runner.py` è: metallb → longhorn → ingress → dn-essence.

**DN-essence default enabled**
A differenza degli altri addon (default `false`), DN-essence è abilitato di default (`DN_ESSENCE_ENABLED=true`). Per disabilitarlo: `DN_ESSENCE_ENABLED=false` in `.env`.
Deploy via `helm upgrade --install` con chart OCI (`oci://ghcr.io/opinoc/helm-charts/dn-essence`); idempotente ma incrementa la revisione Helm ad ogni run (comportamento atteso, accettato).
Tutte le task (template + Helm + kubectl) usano `delegate_to: localhost` + `KUBECONFIG: /root/.kube/{{ cluster_name }}`, coerente con il pattern usato da Traefik/NPM (Helm è installato solo sulla bootstrap machine).
Il chart OCI non supporta `service.nodePort` nei values: dopo il deploy viene eseguito un `kubectl patch` idempotente per impostare la porta NodePort specifica (default 30880). Se la porta è già corretta il task viene saltato.
DN-essence appare nel riepilogo pre-installazione (`_print_cluster_summary` in `cli.py`) con URL UI e versione.

**externalTrafficPolicy: Local su NodePort e LoadBalancer**
Con `externalTrafficPolicy: Cluster` (default Kubernetes), kube-proxy applica SNAT al traffico in ingresso: il pod riceve l'IP del nodo anziché l'IP sorgente reale. Questo rompe applicazioni che si affidano al client IP reale (log, rate-limit, geo-block).
Fix: entrambi i Service (Traefik e NPM) impostano `externalTrafficPolicy: Local` sia in modalità LoadBalancer che NodePort. Con `Local` il nodo serve il traffico solo se ospita un pod locale — condizione sempre vera perché entrambi i controller sono DaemonSet (un pod per nodo). Nessun rischio di connessioni droppate.
Su cluster esistenti già deployati, ri-eseguire `./fun-kube up` propaga la modifica al Service tramite `kubectl apply` (nessun downtime).

**MetalLB su nodi control-plane (label exclude-from-external-load-balancers)**
kubeadm applica automaticamente `node.kubernetes.io/exclude-from-external-load-balancers` ai nodi CP. MetalLB layer2 rispetta questo label e non invia ARP reply per i VIP, rendendo i LoadBalancer irraggiungibili dall'esterno su cluster CP-only.
Fix in `metallb/tasks/main.yml`: se non ci sono nodi worker puri (rilevato via `kubectl get nodes` live), il label viene rimosso da tutti i CP prima di applicare IPAddressPool e L2Advertisement.
