# Changelog

## 1.1.0 — in sviluppo

### Novità

- **Wizard interattivo** `./fun-kube --wizard` (o `fun-kube wizard`): interfaccia a terminale stile
  k9s, solo tastiera, in inglese. Crea o modifica il `.env` di uno o più cluster con scelte a
  cascata, spiegazioni per ogni passo e campo, opzioni avanzate su `F2`, campi bloccati sui
  cluster già installati, test SSH dei nodi, validazione con le regole di `up`; alla fine salva,
  esegue il dry-run o lancia il provisioning. Nuova dipendenza: Textual (installata
  automaticamente nel venv).
- Wizard: **upgrade di Kubernetes** sui cluster esistenti (versioni disponibili lette dal cluster e
  da pkgs.k8s.io, compatibilità con gli addon, ripresa di un upgrade interrotto) → lancia
  `fun-kube upgrade`; anteprima del `.env` completa e scorrevole; le variabili già presenti nel
  file vengono sempre conservate.
- `up`, `upgrade`, `reset`, `diagnose` e il wizard si fermano subito se non eseguiti come root
  (prima `up` falliva a metà, dopo aver già modificato i nodi; il wizard non vedeva i cluster
  esistenti). Indicano come rilanciare con sudo.
- Wizard: il dry-run (di `up` e di `upgrade`) gira dentro il wizard; al termine si torna a
  modificare, si esegue per davvero o si esce.

## 1.0.0 — 2026-10-13

Prima versione stabile. Rispetto al codice precedente (commit `b31232b`): versioni aggiornate e
fissate nel repo, upgrade di Kubernetes, addon monitoring, controlli di sicurezza su cluster
esistenti e numerosi bug corretti durante i test nel lab (HA 3 CP + 3 worker e mononodo).

### Novità

- **`fun-kube upgrade --to vX.Y.Z`** — upgrade di Kubernetes con kubeadm: patch o una minor per
  volta, un nodo alla volta (drain → upgrade → uncordon), snapshot etcd sulla bootstrap,
  controlli preliminari (salute, addon compatibili, volumi Longhorn), ripresa automatica dopo
  un'interruzione, fermo dei workload solo con conferma esplicita (mononodo, volumi a 1 replica).
- **Addon monitoring** (`MONITORING_ENABLED`) — kube-prometheus-stack, Loki, Alloy (log ed eventi),
  dashboard "Log dei pod", ServiceMonitor Longhorn. Grafana esposto per default solo sulla rete
  dei nodi (MetalLB o NodePort); pubblicazione via NPM o virtual host Traefik su scelta esplicita.
  Metriche del control plane esposte (niente falsi allarmi KubeSchedulerDown / etcdMembersDown).
  Adotta un'installazione manuale esistente fatta con la guida observability.
- **Controllo `.env` ↔ cluster esistente** prima di ogni `up`: errore se cambiano endpoint/VIP,
  CIDR, password del DB di NPM, versione di Kubernetes; avvisi su nodi rimossi, taint, repliche.
- **`up --dry-run`** esegue anche il preflight (SSH, sola lettura): risorse, porte, orologio dei
  nodi, spazio Longhorn per il monitoring.
- **keepalived con health check** dell'API server: il VIP passa a un CP sano se l'API non risponde.
- **API server di Calico** (configurazione standard dell'operatore): `kubectl get tiers`,
  risorse `projectcalico.org/v3`.
- **`reset`** arresta prima i workload (i volumi NFS/Longhorn si staccano in modo pulito),
  segnala i processi bloccati; `--reboot` riavvia i nodi.
- **`diagnose`** con sezioni Calico e Monitoring (alert attivi).
- `fun-kube --version`; versione mostrata nell'intestazione dei comandi.

### Versioni dei componenti

Tutte in `fun_kube/versions.py`; manifest kubectl vendored nel repo (`scripts/vendor-manifests.py`).

| Componente | Prima | 1.0.0 |
|---|---|---|
| Kubernetes (default) | v1.35.4 | v1.35.9 |
| containerd.io | non fissata | 2.3.6 |
| Calico | v3.28.0 | v3.32.2 |
| cert-manager | v1.17.2 | v1.21.2 |
| metrics-server | latest | v0.9.0 |
| local-path-provisioner | v0.0.30 | v0.0.37 |
| MetalLB | v0.15.3 | v0.16.0 |
| Longhorn | v1.11.1 | v1.12.1 |
| Traefik chart | 39.0.8 | 41.6.1 |
| NPM / MariaDB | latest / 10.6 | 2.16.0 / 11.4 |
| Helm (bootstrap) | 3 | 4 |
| kube-prometheus-stack / Loki / Alloy | — | 92.3.0 / 18.15.1 / 1.13.1 |

### Correzioni principali

- IP del nodo esplicito (`node_ip`): il primo CP si registrava con il VIP keepalived.
- Calico: Installation invalida sui cluster esistenti (due metodi di autodetection dell'IP).
- Longhorn: versione letta da un'annotation inesistente (protezione downgrade mai attiva,
  `upgrade` non vedeva Longhorn); repliche impostate via ConfigMap (la patch della
  StorageClass veniva rifiutata con meno di 3 nodi).
- Traefik chart 41: il tipo di Service va in `service.spec.type` (NodePort diventava LoadBalancer).
- Attese dei rollout bloccate sui pod della versione precedente (MetalLB, Calico, cert-manager).
- kubeadm: fallback dei pacchetti mai eseguito; nessun cambio di versione dei pacchetti su nodi
  già nel cluster (kubelet più nuovo dell'API server).
- kubeconfig di emergenza con utente SSH non-root; interfaccia keepalived rilevata
  automaticamente; MTU tra i nodi; MariaDB `Recreate`; mononodo con una sola StorageClass default.
- Orologio dei nodi: controllo nel preflight, sync NTP prima di apt, chrony `makestep 1 -1`.
- Ctrl+C durante `upgrade`: blocco se kubeadm è ancora in esecuzione sul nodo.

### Da sapere per i cluster esistenti

- Le variabili `CERT_MANAGER_VERSION`, `LOCAL_PATH_VERSION`, `METALLB_VERSION`, `LONGHORN_VERSION`
  nel `.env` vengono ignorate (avviso): le versioni sono fissate nel repo.
- Il primo `up` aggiorna gli addon alle versioni sopra (testato da cluster creati con il codice
  precedente: Calico 3.28→3.32, cert-manager 1.17→1.21, Longhorn 1.11→1.12, MariaDB 10.6→11.4).
- `up` non aggiorna più Kubernetes: se `K8S_VERSION` differisce dai nodi si ferma → `upgrade`.
- Volumi di default più piccoli (NPM 1Gi + 2Gi, monitoring 22Gi): i volumi esistenti non vengono
  mai ridotti.
- `TRAEFIK_CHART_VERSION`, se impostato, deve essere >= 41.

### Limitazioni note

- **Non testati su cluster reale dopo le modifiche**: modalità `LOCAL_NODE` (bootstrap = nodo);
  nuovo `reset` con arresto pulito dei workload; adozione del monitoring installato a mano
  (verificata offline); verifiche dentro Grafana (dashboard "Log dei pod", eventi in Loki).
- Mettere un addon a `false` non lo disinstalla.
- `up` non cambia endpoint/VIP, CIDR o topologia di un cluster esistente (richiede reinstallazione).
- Gli upgrade di Kubernetes supportano fino alla 1.36 con le versioni degli addon fissate
  (Calico e cert-manager vanno aggiornati per la 1.37).
