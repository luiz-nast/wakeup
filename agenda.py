#!/usr/bin/env python3
"""Agenda do despertador: cria e apaga alarmes.

Roda no python do sistema (python3-gi): aqui nao tem camera nem modelo, so
json e systemd, entao nao usa o .venv do despertador.

Escreve ~/.config/wakeup/alarmes.json e mais nada. Quem vira timer do systemd
e o wakeup-sync, que roda como root acordado pelo wakeup-sync.path quando o
arquivo muda - por isso criar alarme aqui nao pede senha. O "aplicando..." na
linha e a janela de ~1s entre salvar e o timer existir.
"""
import json, os, subprocess, time
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

CONFIG = os.environ.get("WAKEUP_JSON",
                        os.path.expanduser("~/.config/wakeup/alarmes.json"))


def ler():
    try:
        with open(CONFIG) as f:
            return sorted({a["hora"] for a in json.load(f)["alarmes"]})
    except (OSError, ValueError, KeyError, TypeError):
        return []


def salvar(horas):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(f"{CONFIG}.tmp", "w") as f:
        json.dump({"alarmes": [{"hora": h} for h in sorted(set(horas))]}, f, indent=2)
    os.replace(f"{CONFIG}.tmp", CONFIG)  # o root nunca le arquivo pela metade


def estado(hora):
    """Como o systemd esta vendo esse alarme agora.

    O --timestamp=unix e o que faz o systemctl responder "@<segundos>". Sem
    ele vem a data por extenso ("Sun 2026-09-13 05:30:00 -03"), que muda com
    a versao e com o locale - e quebrava a lista inteira aqui."""
    unit = f"wakeup@{hora.replace(':', '')}.timer"
    try:
        r = subprocess.run(["systemctl", "show", "--timestamp=unix", unit,
                            "-p", "ActiveState", "-p", "NextElapseUSecRealtime"],
                           capture_output=True, text=True)
    except OSError:
        return "?"
    d = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    if d.get("ActiveState") != "active":
        return "aplicando..."
    try:  # nada aqui vale derrubar a janela
        falta = int(d.get("NextElapseUSecRealtime", "").lstrip("@")) - time.time()
    except ValueError:
        return "armado"
    if falta <= 0:
        return "armado"
    h, m = divmod(int(falta // 60), 60)
    return f"armado, toca em {h}h{m:02d}"


class Janela(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Alarmes",
                         default_width=420, default_height=520)
        mais = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="novo alarme")
        mais.connect("clicked", self.novo)
        cabecalho = Adw.HeaderBar()
        cabecalho.pack_start(mais)

        self.grupo = Adw.PreferencesGroup()
        pagina = Adw.PreferencesPage()
        pagina.add(self.grupo)
        self.pilha = Gtk.Stack()
        self.pilha.add_named(pagina, "lista")
        self.pilha.add_named(Adw.StatusPage(
            icon_name="alarm-symbolic", title="Nenhum alarme",
            description="O + ali em cima cria o primeiro."), "vazio")
        self.toasts = Adw.ToastOverlay(child=self.pilha)

        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecalho)
        vista.set_content(self.toasts)
        self.set_content(vista)

        self.linhas, self.horas = {}, ler()
        self.desenhar()
        GLib.timeout_add_seconds(2, self.atualizar)

    def desenhar(self):
        for linha in self.linhas.values():
            self.grupo.remove(linha)
        self.linhas = {}
        for hora in self.horas:
            linha = Adw.ActionRow(title=f"<span size='xx-large'>{hora}</span>",
                                  subtitle="todo dia")
            apagar = Gtk.Button(icon_name="user-trash-symbolic",
                                valign=Gtk.Align.CENTER, tooltip_text="apagar")
            apagar.add_css_class("flat")
            apagar.connect("clicked", self.apagar, hora)
            linha.add_suffix(apagar)
            self.grupo.add(linha)
            self.linhas[hora] = linha
        self.pilha.set_visible_child_name("lista" if self.horas else "vazio")
        self.atualizar()

    def atualizar(self):
        for hora, linha in self.linhas.items():
            linha.set_subtitle(f"todo dia - {estado(hora)}")
        return GLib.SOURCE_CONTINUE

    def avisar(self, texto):
        self.toasts.add_toast(Adw.Toast(title=texto))

    def novo(self, _botao):
        hora = Adw.SpinRow(title="hora", adjustment=Gtk.Adjustment(
            lower=0, upper=23, step_increment=1, value=7))
        minuto = Adw.SpinRow(title="minuto", adjustment=Gtk.Adjustment(
            lower=0, upper=59, step_increment=5, value=0))
        grupo = Adw.PreferencesGroup()
        grupo.add(hora)
        grupo.add(minuto)
        dialogo = Adw.AlertDialog(heading="Novo alarme",
                                  body="Toca todo dia nesse horario.")
        dialogo.set_extra_child(grupo)
        dialogo.add_response("cancelar", "Cancelar")
        dialogo.add_response("criar", "Criar")
        dialogo.set_response_appearance("criar", Adw.ResponseAppearance.SUGGESTED)
        dialogo.set_default_response("criar")
        dialogo.connect("response", self.criar, hora, minuto)
        dialogo.present(self)

    def criar(self, _dialogo, resposta, hora, minuto):
        if resposta != "criar":
            return
        nova = f"{int(hora.get_value()):02d}:{int(minuto.get_value()):02d}"
        if nova in self.horas:
            return self.avisar(f"ja tem alarme das {nova}")
        self.horas = sorted(self.horas + [nova])
        salvar(self.horas)
        self.desenhar()
        self.avisar(f"alarme das {nova} criado")

    def apagar(self, _botao, hora):
        self.horas = [h for h in self.horas if h != hora]
        salvar(self.horas)
        self.desenhar()
        self.avisar(f"alarme das {hora} apagado")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.nast.wakeup")

    def do_activate(self):
        (self.props.active_window or Janela(self)).present()


if __name__ == "__main__":
    App().run(None)
