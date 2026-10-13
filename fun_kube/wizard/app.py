"""
Fun-Kube wizard — keyboard-driven TUI (Textual) that writes a .env and optionally
launches `fun-kube up`. Rules and validation live in model.py, not here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (Button, ContentSwitcher, Footer, Input, Label, OptionList,
                             RadioButton, RadioSet, RichLog, Static, Switch, TextArea)
from textual.widgets.option_list import Option

from .. import __version__
from . import model
from .model import STEPS, WizardState
from .texts import CHOICES, FIELDS, LOCKED_HINT, STEP_INTRO, TOPOLOGY_HELP, UPGRADE_HELP

STEP_FIELDS = {
    "Cluster": ["CLUSTER_NAME", "K8S_VERSION", "CLUSTER_TIMEZONE"],
    "Access & network": ["LOCAL_NODE", "SSH_USER", "SSH_KEY_PATH", "KEEPALIVED_VIP", "KEEPALIVED_INTERFACE",
                         "POD_CIDR", "SERVICE_CIDR", "API_SERVER_EXTRA_SANS"],
    "Add-ons": ["METALLB_ENABLED", "METALLB_IP_POOL", "INGRESS_ENABLED", "INGRESS_TYPE", "INGRESS_SERVICE_TYPE",
                "TRAEFIK_LB_IP", "TRAEFIK_HTTP_NODEPORT", "TRAEFIK_HTTPS_NODEPORT", "TRAEFIK_ACME_EMAIL",
                "TRAEFIK_DASHBOARD_HOST", "TRAEFIK_IS_DEFAULT_CLASS", "NPM_LB_IP", "NPM_HTTP_NODEPORT",
                "NPM_HTTPS_NODEPORT", "NPM_ADMIN_NODEPORT", "NPM_DB_PASSWORD", "NPM_DB_STORAGE",
                "NPM_DATA_STORAGE", "LONGHORN_ENABLED", "LONGHORN_RWX", "LONGHORN_UI_NODEPORT",
                "DN_ESSENCE_ENABLED", "DN_ESSENCE_UI_NODEPORT"],
    "Monitoring": ["MONITORING_ENABLED", "GRAFANA_EXPOSE", "GRAFANA_LB_IP", "GRAFANA_ALLOWED_CIDRS",
                   "GRAFANA_NODEPORT", "GRAFANA_TRAEFIK_INGRESS", "GRAFANA_DOMAIN", "GRAFANA_ADMIN_PASSWORD",
                   "MONITORING_STORAGE_CLASS", "PROM_RETENTION", "PROM_RETENTION_SIZE", "PROM_STORAGE",
                   "LOKI_RETENTION", "LOKI_STORAGE", "GRAFANA_STORAGE", "ALERTMANAGER_STORAGE",
                   "ALERTMANAGER_SLACK_WEBHOOK", "ALERTMANAGER_SLACK_CHANNEL"],
}
BOOL_KEYS = {k for k in FIELDS if k.endswith("_ENABLED") or k in (
    "LOCAL_NODE", "LONGHORN_RWX", "TRAEFIK_IS_DEFAULT_CLASS", "GRAFANA_TRAEFIK_INGRESS")}
DYNAMIC_CHOICES = {"INGRESS_SERVICE_TYPE": "ingress_service_types", "GRAFANA_EXPOSE": "grafana_exposures"}

CSS = """
Screen { layout: vertical; }
#title { height: 1; background: $primary; color: $text; padding: 0 1; }
#body { height: 1fr; }
#steps { width: 26; border: round $primary; }
#main { width: 1fr; border: round $secondary; padding: 0 1; }
#help { height: 5; border: round $accent; padding: 0 1; color: $text-muted; }
.intro { color: $text-muted; margin: 0 0 1 0; }
.fld { height: auto; margin: 0 0 1 0; }
.fld Label { text-style: bold; }
.hint { color: $text-muted; }
.locked .hint { color: $warning; }
.row { height: auto; }
.row Label { width: 18; padding: 1 1 0 0; }
.row Input { width: 1fr; }
.errors { color: $error; }
.warnings { color: $warning; }
.ok { color: $success; }
#step-5 { height: 1fr; }
#review-status { height: auto; max-height: 12; overflow-y: auto; }
#review-buttons { margin: 1 0 0 0; }
#review-env { height: 1fr; min-height: 8; border: round $secondary; }
#review-env:focus { border: round $accent; }
#upgrade-box { height: auto; margin: 1 0 0 0; }
#run-log { height: 1fr; border: round $secondary; }
#run-status { height: auto; margin: 1 0 0 0; }
Switch { width: auto; }
RadioSet { width: 1fr; }
"""


# ---------------------------------------------------------------------------
# Screen 1 — choose the cluster file
# ---------------------------------------------------------------------------

class FileScreen(Screen):
    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit", priority=True)]

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = project_dir
        self.files = model.list_env_files(project_dir)

    def compose(self) -> ComposeResult:
        yield Static(f" Fun-Kube {__version__} — cluster wizard", id="title")
        with VerticalScroll(id="main"):
            yield Static("Choose the cluster to configure. Each cluster has its own .env file in "
                         f"{self.project_dir}. Pick an existing one to change it, or create a new one.",
                         classes="intro")
            opts = [Option(f"{f.path.name:<14} cluster: {f.cluster}  ({f.nodes} nodes)", id=f"file:{f.path.name}")
                    for f in self.files]
            opts.append(Option("+ New cluster", id="new"))
            yield OptionList(*opts, id="files")
            with Vertical(id="new-box", classes="fld"):
                yield Label("New file name")
                yield Input(placeholder=".env.prod", id="new-name")
                yield Static("Use .env for your main cluster, .env.<name> for the others. "
                             "Run it later with ./fun-kube up <file>.", classes="hint")
                yield Static("", id="new-error", classes="errors")
        yield Static("↑↓ choose · Enter open · Ctrl+Q quit", id="help")

    def on_mount(self) -> None:
        self.query_one("#new-box").display = False
        self.query_one("#files", OptionList).focus()

    @on(OptionList.OptionSelected, "#files")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "new":
            box = self.query_one("#new-box")
            box.display = True
            name = self.query_one("#new-name", Input)
            if not name.value:
                name.value = ".env" if not (self.project_dir / ".env").exists() else ""
            name.focus()
        else:
            path = self.project_dir / event.option.id.split(":", 1)[1]
            self.app.push_screen(WizardScreen(path, model.load_state(path)))

    @on(Input.Submitted, "#new-name")
    def _new(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        err = self.query_one("#new-error", Static)
        if not (name == ".env" or name.startswith(".env.")) or name == ".env.example" or "/" in name:
            err.update("Use .env or .env.<name> (e.g. .env.prod).")
            return
        path = self.project_dir / name
        if path.exists():
            err.update(f"{name} already exists: choose it from the list to change it.")
            return
        self.app.push_screen(WizardScreen(path, model.load_state(None)))


# ---------------------------------------------------------------------------
# Screen 2 — the wizard
# ---------------------------------------------------------------------------

class WizardScreen(Screen):
    BINDINGS = [
        Binding("ctrl+n", "next", "Next", priority=True),
        Binding("ctrl+b", "back", "Back", priority=True),
        Binding("f2", "advanced", "Advanced", priority=True),
        Binding("ctrl+s", "review", "Review & save", priority=True),
        Binding("ctrl+q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, path: Path, state: WizardState):
        super().__init__()
        self.path = path
        self.state = state
        self.step = 0
        self.advanced = False
        if not state.v("SSH_KEY_PATH") and model.ssh_keys():
            state.values["SSH_KEY_PATH"] = model.ssh_keys()[0]

    # --- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        mode = "existing cluster — some fields are locked" if self.state.existing_cluster else "new configuration"
        yield Static(f" Fun-Kube {__version__} — wizard · {self.path.name} ({mode})", id="title")
        with Horizontal(id="body"):
            yield OptionList(*[Option(s, id=f"step:{i}") for i, s in enumerate(STEPS)], id="steps")
            with ContentSwitcher(initial="step-0", id="main"):
                for i, name in enumerate(STEPS):
                    with (Vertical if name == "Review" else VerticalScroll)(id=f"step-{i}"):
                        yield Static(STEP_INTRO[name], classes="intro")
                        yield from self._step_widgets(name)
        yield Static("", id="help")
        yield Footer()

    def _step_widgets(self, name: str) -> ComposeResult:
        if name == "Nodes":
            yield from self._nodes_widgets()
        elif name == "Review":
            yield Static("", id="review-status")
            with Horizontal(classes="row", id="review-buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Save + dry-run", id="save-dry")
                yield Button("Save + provision", id="save-up", variant="warning")
                yield Button("Save + upgrade dry-run", id="save-upg-dry")
                yield Button("Save + upgrade", id="save-upg", variant="warning")
            yield Static("The file that will be written — Tab to it, then ↑↓ PgUp PgDn to scroll.",
                         classes="hint")
            yield TextArea("", id="review-env", read_only=True, soft_wrap=False, show_line_numbers=False)
        else:
            for key in STEP_FIELDS[name]:
                yield self._field(key)
            if name == "Cluster" and self.state.existing_cluster:
                with Vertical(id="upgrade-box", classes="fld"):
                    yield Label("Kubernetes upgrade")
                    yield Static("Checking the versions available for this cluster…", id="upgrade-status",
                                 classes="hint")
            if name == "Access & network":
                yield Button("Test SSH to all nodes", id="test-ssh")
                yield Static("", id="ssh-result")

    def _field(self, key: str) -> Vertical:
        label, hint, _, adv = FIELDS[key]
        locked = self.state.existing_cluster and key in model.LOCKED_ON_EXISTING
        value = self.state.v(key)
        if key in BOOL_KEYS:
            widget = Switch(value=self.state.on(key), id=f"w-{key}")
        elif key in CHOICES or key in DYNAMIC_CHOICES:
            options = CHOICES.get(key) or [(v, lbl) for v, lbl, _ in getattr(self.state, DYNAMIC_CHOICES[key])()]
            widget = RadioSet(*[RadioButton(lbl, value=(v == value), id=f"r-{key}--{v}")
                                for v, lbl in options], id=f"w-{key}")
        else:
            widget = Input(value=value, id=f"w-{key}",
                           password=key.endswith("PASSWORD") or key.endswith("WEBHOOK"))
        widget.disabled = locked
        classes = "fld" + (" adv" if adv else "") + (" locked" if locked else "")
        if locked and key == "K8S_VERSION" and self.state.live:
            hint = f"The cluster runs {self.state.live.version}. To update it choose a Kubernetes upgrade below."
        elif locked:
            hint = LOCKED_HINT
        return Vertical(Label(label + ("  (advanced)" if adv else "")), widget,
                        Static(hint, classes="hint"),
                        id=f"fld-{key}", classes=classes)

    def _nodes_widgets(self) -> ComposeResult:
        locked = self.state.existing_cluster
        yield Label("Topology")
        topo = RadioSet(*[RadioButton(lbl, value=(k == self.state.topology), id=f"topo--{k}")
                          for k, lbl in model.TOPOLOGIES.items()], id="topology")
        topo.disabled = locked
        yield topo
        yield Static(LOCKED_HINT if locked else "", id="topo-hint", classes="hint")
        with Horizontal(classes="row", id="counts"):
            yield Label("Control-planes")
            yield Input(str(len(self.state.cps)), id="cp-count", disabled=locked)
            yield Label("Workers")
            yield Input(str(len(self.state.workers)), id="worker-count")
        yield Static("Control-planes: 3 or 5 (etcd needs an odd number). Workers: 0 = control-planes also "
                     "run workloads.", classes="hint", id="counts-hint")
        yield Vertical(id="node-rows")

    def on_mount(self) -> None:
        self._build_node_rows()
        self._refresh()
        self.query_one("#steps", OptionList).highlighted = 0
        self._focus_step()
        if self.state.existing_cluster:
            self._upgrade_worker()

    # --- Kubernetes upgrade (existing clusters) ----------------------------
    @work(thread=True, exclusive=True, group="upgrade")
    def _upgrade_worker(self) -> None:
        targets = model.upgrade_targets(self.state)
        self.app.call_from_thread(self._show_upgrade_targets, targets)

    def _show_upgrade_targets(self, targets) -> None:
        live = self.state.live
        status = self.query_one("#upgrade-status", Static)
        if not targets:
            status.update(f"The cluster runs {live.version}: no newer patch or minor version found "
                          "(or pkgs.k8s.io is unreachable).")
            return
        status.update(f"The cluster runs {live.version}. One step at a time: a newer patch, or the next "
                      "minor version. Nodes are upgraded one by one (drain → upgrade → uncordon).")
        buttons = [RadioButton("No upgrade", value=True, id="up--none")]
        for version, label, avail in targets:
            b = RadioButton(label, id="up--" + version.replace(".", "_"))
            b.disabled = not avail
            buttons.append(b)
        self.query_one("#upgrade-box").mount(RadioSet(*buttons, id="upgrade"), after=status)

    # --- nodes ------------------------------------------------------------
    def _build_node_rows(self) -> None:
        box = self.query_one("#node-rows", Vertical)
        box.remove_children()
        rows = [("cp", i, n) for i, n in enumerate(self.state.cps)] + \
               [("worker", i, n) for i, n in enumerate(self.state.workers)]
        for kind, i, node in rows:
            if not node.hostname:
                node.hostname = f"{'cp' if kind == 'cp' else 'worker'}{i + 1}"
            label = f"{'Control-plane' if kind == 'cp' else 'Worker'} {i + 1}"
            box.mount(Horizontal(Label(label),
                                 Input(node.ip, placeholder="IP address", id=f"n-{kind}-ip-{i}"),
                                 Input(node.hostname, placeholder="hostname", id=f"n-{kind}-host-{i}"),
                                 classes="row"))
        counts = self.query_one("#counts")
        counts.display = self.state.topology != "single"
        self.query_one("#cp-count").display = self.state.topology == "ha"
        self.query_one("#counts-hint").display = self.state.topology != "single"
        if not self.state.existing_cluster:
            self.query_one("#topo-hint", Static).update(TOPOLOGY_HELP[self.state.topology])

    # --- refresh: visibility, forced values, availability, step markers ----
    def _refresh(self) -> None:
        st = self.state
        st.normalize()
        for fld in self.query(".fld"):
            if not fld.id or not fld.id.startswith("fld-"):
                continue
            key = fld.id[4:]
            fld.display = st.visible(key) and (self.advanced or not fld.has_class("adv"))
            widget = fld.query_one(f"#w-{key}")
            if isinstance(widget, Switch) and widget.value != st.on(key):
                with widget.prevent(Switch.Changed):
                    widget.value = st.on(key)
        for key, method in DYNAMIC_CHOICES.items():
            for v, label, avail in getattr(st, method)():
                btn = self.query_one(f"#r-{key}--{v}", RadioButton)
                btn.disabled = not avail
                btn.label = label if avail else f"{label}  (needs MetalLB)"
        lh = self.query_one("#fld-LONGHORN_ENABLED")
        lh.query_one(".hint", Static).update(
            "Required: Nginx Proxy Manager on several nodes stores its data on Longhorn (RWX)"
            if st.longhorn_required() else FIELDS["LONGHORN_ENABLED"][1])
        lh.query_one(Switch).disabled = st.longhorn_required()
        errs = model.step_errors(st)
        steps = self.query_one("#steps", OptionList)
        for i, name in enumerate(STEPS):
            mark = "  " if name == "Review" else (" ✗" if errs[name] else " ✓")
            steps.replace_option_prompt_at_index(i, f"{i + 1}. {name}{mark}")

    def _focus_step(self) -> None:
        self.query_one("#main", ContentSwitcher).current = f"step-{self.step}"
        self.query_one("#steps", OptionList).highlighted = self.step
        if STEPS[self.step] == "Review":
            self._update_review()
        container = self.query_one(f"#step-{self.step}")
        for w in container.query("Input, Switch, RadioSet, Button"):
            if w.display and not w.disabled and all(a.display for a in w.ancestors if hasattr(a, "display")):
                w.focus()
                break
        self.query_one("#help", Static).update(STEP_INTRO[STEPS[self.step]])

    # --- events -----------------------------------------------------------
    @on(Input.Changed)
    def _input(self, event: Input.Changed) -> None:
        wid = event.input.id or ""
        if wid.startswith("w-"):
            self.state.values[wid[2:]] = event.value.strip()
        elif wid.startswith("n-"):
            _, kind, what, idx = wid.split("-")
            node = (self.state.cps if kind == "cp" else self.state.workers)[int(idx)]
            setattr(node, "ip" if what == "ip" else "hostname", event.value.strip())
        elif wid in ("cp-count", "worker-count"):
            return            # applied on Enter / blur, not on every keystroke
        self._refresh()

    @on(Input.Submitted, "#cp-count, #worker-count")
    def _counts(self, event: Input.Submitted) -> None:
        self._apply_counts()

    def _apply_counts(self) -> None:
        try:
            cps = int(self.query_one("#cp-count", Input).value or 0)
            workers = int(self.query_one("#worker-count", Input).value or 0)
        except ValueError:
            return
        workers = max(0, min(workers, model.MAX_NODES))
        self.state.set_topology(self.state.topology, cp_count=cps, worker_count=workers)
        self.query_one("#cp-count", Input).value = str(len(self.state.cps))
        self.query_one("#worker-count", Input).value = str(len(self.state.workers))
        self._build_node_rows()
        self._refresh()

    @on(Switch.Changed)
    def _switch(self, event: Switch.Changed) -> None:
        if (event.switch.id or "").startswith("w-"):
            self.state.values[event.switch.id[2:]] = "true" if event.value else "false"
            self._refresh()

    @on(RadioSet.Changed)
    def _radio(self, event: RadioSet.Changed) -> None:
        rid = event.pressed.id or ""
        if event.radio_set.id == "upgrade":
            v = rid.split("--", 1)[1]
            self.state.upgrade_to = "" if v == "none" else v.replace("_", ".")
        elif event.radio_set.id == "topology":
            self.state.set_topology(rid.split("--", 1)[1])
            self.query_one("#cp-count", Input).value = str(len(self.state.cps))
            self.query_one("#worker-count", Input).value = str(len(self.state.workers))
            self._build_node_rows()
        elif rid.startswith("r-"):
            key, value = rid[2:].split("--", 1)
            self.state.values[key] = value
        self._refresh()

    def on_descendant_focus(self, event) -> None:
        widget = event.widget
        for node in [widget, *widget.ancestors]:
            nid = getattr(node, "id", None) or ""
            if nid.startswith("fld-"):
                key = nid[4:]
                label, hint, help_, _ = FIELDS[key]
                self.query_one("#help", Static).update(f"[b]{label}[/b] — {help_ or hint}")
                return
            if nid == "upgrade-box":
                self.query_one("#help", Static).update(UPGRADE_HELP)
                return
            if nid in ("topology", "node-rows", "counts"):
                self.query_one("#help", Static).update(TOPOLOGY_HELP[self.state.topology])
                return
        if (getattr(widget, "id", "") or "") in ("cp-count", "worker-count"):
            self._apply_counts()

    @on(OptionList.OptionSelected, "#steps")
    def _step_picked(self, event: OptionList.OptionSelected) -> None:
        self.step = int(event.option.id.split(":")[1])
        self._focus_step()

    @on(Button.Pressed, "#test-ssh")
    def _test_ssh(self) -> None:
        self.query_one("#ssh-result", Static).update("Testing SSH…")
        self._ssh_worker()

    @work(thread=True, exclusive=True)
    def _ssh_worker(self) -> None:
        st = self.state
        lines = []
        if st.on("LOCAL_NODE"):
            lines.append("Local mode: no SSH needed.")
        for n in st.cps + st.workers:
            if st.on("LOCAL_NODE"):
                break
            if not n.ip:
                lines.append(f"{n.hostname or '?'}: no IP")
                continue
            cmd = ["ssh", "-i", os.path.expanduser(st.v("SSH_KEY_PATH")), "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                   f"{st.v('SSH_USER') or 'root'}@{n.ip}", "sudo -n true && echo ok"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                ok = r.returncode == 0 and "ok" in r.stdout
                detail = "SSH + sudo OK" if ok else (r.stderr.strip().splitlines() or ["failed"])[-1][:70]
            except subprocess.TimeoutExpired:
                ok, detail = False, "timeout"
            lines.append(f"{'[green]✓[/]' if ok else '[red]✗[/]'} {n.hostname} ({n.ip}): {detail}")
        self.app.call_from_thread(self.query_one("#ssh-result", Static).update, "\n".join(lines))

    # --- review & save ----------------------------------------------------
    def _update_review(self) -> None:
        st = self.state
        errs = model.step_errors(st)
        problems = [f"{s}: {e}" for s in STEPS for e in errs[s]]
        warnings: List[str] = []
        if not problems:
            cfg_errors, warnings = model.validate(st)
            problems = cfg_errors
        status = self.query_one("#review-status", Static)
        if problems:
            text = "[b]Fix before saving:[/b]\n" + "\n".join(f"[red]✗[/] {p}" for p in problems)
        else:
            text = "[green]✓ Configuration valid.[/]"
            if warnings:
                text += "\n" + "\n".join(f"[yellow]![/] {w.splitlines()[0]}" for w in warnings)
        if st.upgrade_to and st.live:
            text += (f"\n[b]Kubernetes upgrade {st.live.version} → {st.upgrade_to}[/b]: runs "
                     "`fun-kube upgrade` (it updates K8S_VERSION in the file when done). Other changes "
                     f"(add-ons, new nodes) are applied afterwards with ./fun-kube up {self.path.name}.")
        status.update(text)
        self.query_one("#review-env", TextArea).load_text(st.render())
        upgrade = bool(st.upgrade_to)
        for bid in ("save", "save-dry", "save-up", "save-upg-dry", "save-upg"):
            btn = self.query_one(f"#{bid}", Button)
            btn.disabled = bool(problems)
            btn.display = bid == "save" or (bid.startswith("save-upg") == upgrade)

    @on(Button.Pressed, "#save, #save-dry, #save-up, #save-upg-dry, #save-upg")
    def _save(self, event: Button.Pressed) -> None:
        model.save(self.state, self.path)
        action = {"save": "save", "save-dry": "dry-run", "save-up": "up",
                  "save-upg-dry": "upgrade-dry-run", "save-upg": "upgrade"}[event.button.id]
        if action.endswith("dry-run"):
            self.app.push_screen(RunScreen(self.path, action, self.state.upgrade_to))
        else:
            self.app.exit((self.path, action, self.state.upgrade_to))

    # --- actions ----------------------------------------------------------
    def action_next(self) -> None:
        if self.step < len(STEPS) - 1:
            self.step += 1
            self._focus_step()

    def action_back(self) -> None:
        if self.step > 0:
            self.step -= 1
            self._focus_step()

    def action_review(self) -> None:
        self.step = len(STEPS) - 1
        self._focus_step()

    def action_advanced(self) -> None:
        self.advanced = not self.advanced
        self._refresh()
        self.notify("Advanced options " + ("shown" if self.advanced else "hidden"), timeout=2)


# ---------------------------------------------------------------------------
# Screen 3 — dry-run inside the wizard, then: back to edit / run for real / quit
# ---------------------------------------------------------------------------

class RunScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back to the wizard", priority=True),
        Binding("ctrl+q", "quit_saved", "Quit", priority=True),
    ]

    def __init__(self, path: Path, action: str, upgrade_to: str):
        super().__init__()
        self.path, self.action, self.upgrade_to = path, action, upgrade_to
        self.upgrade = action.startswith("upgrade")
        self.proc: Optional[subprocess.Popen] = None

    def compose(self) -> ComposeResult:
        what = f"upgrade to {self.upgrade_to}" if self.upgrade else "provisioning"
        yield Static(f" Fun-Kube {__version__} — dry-run of the {what} · {self.path.name}", id="title")
        with Vertical(id="main"):
            yield Static("Checks only, nothing is changed on the cluster. "
                         + ("Analysis: cluster health, add-on compatibility, Longhorn volumes, upgrade plan."
                            if self.upgrade else
                            "Configuration, comparison with the existing cluster, SSH preflight on every node."),
                         classes="intro")
            yield RichLog(id="run-log", wrap=True, markup=False, auto_scroll=True)
            yield Static("Running…", id="run-status")
            with Horizontal(classes="row"):
                yield Button("Back to the wizard", id="run-back", variant="primary")
                yield Button(f"Run the upgrade to {self.upgrade_to}" if self.upgrade else "Provision now",
                             id="run-real", variant="warning", disabled=True)
                yield Button("Quit", id="run-quit")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#run-back").focus()
        self._run()

    @work(thread=True, exclusive=True, group="run")
    def _run(self) -> None:
        log = self.query_one("#run-log", RichLog)
        width = max(60, log.size.width - 4)
        env = {**os.environ, "FORCE_COLOR": "1", "COLUMNS": str(width), "PYTHONUNBUFFERED": "1"}
        try:
            self.proc = subprocess.Popen(model.command(self.path, self.action, self.upgrade_to),
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, text=True, env=env, bufsize=1)
            for line in self.proc.stdout:
                self.app.call_from_thread(log.write, Text.from_ansi(line.rstrip("\n")))
            rc = self.proc.wait()
        except OSError as e:
            self.app.call_from_thread(log.write, Text(str(e), style="red"))
            rc = -1
        self.app.call_from_thread(self._done, rc)

    def _done(self, rc: int) -> None:
        status = self.query_one("#run-status", Static)
        real = self.query_one("#run-real", Button)
        if rc == 0:
            status.update("[green]✓ Dry-run passed — nothing was changed.[/] "
                          + ("Run the upgrade now (it asks for confirmation, and for the cluster name if "
                             "workloads must stop), go back to change something, or quit."
                             if self.upgrade else
                             "Provision now (it asks for confirmation), go back to change something, or quit."))
            real.disabled = False
            real.focus()
        elif rc < 0 and self.proc is None:
            status.update("[red]✗ Could not start fun-kube.[/]")
        else:
            status.update(f"[red]✗ Dry-run failed (exit {rc}).[/] Read the messages above, go back to "
                          "the wizard to fix the configuration (or fix the cluster) and try again.")

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    @on(Button.Pressed, "#run-back")
    def action_back(self) -> None:
        self._stop()
        self.app.pop_screen()

    @on(Button.Pressed, "#run-real")
    def _real(self) -> None:
        self.app.exit((self.path, "upgrade" if self.upgrade else "up", self.upgrade_to))

    @on(Button.Pressed, "#run-quit")
    def action_quit_saved(self) -> None:
        self._stop()
        self.app.exit((self.path, "save", self.upgrade_to))


class WizardApp(App):
    CSS = CSS
    TITLE = "Fun-Kube wizard"

    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = project_dir

    def on_mount(self) -> None:
        self.push_screen(FileScreen(self.project_dir))


def run(project_dir: Path) -> Optional[Tuple[Path, str, str]]:
    """Run the wizard. Returns (env file, action, upgrade target) or None if cancelled."""
    return WizardApp(project_dir).run()
