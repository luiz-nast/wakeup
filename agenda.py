#!/usr/bin/env python3
"""Agenda do despertador: cria e apaga alarmes.

Roda no python do sistema (python3-gi): aqui nao tem camera nem modelo, so
json e systemd, entao nao usa o .venv do despertador.

Escreve ~/.config/wakeup/alarmes.json e mais nada. Quem vira timer do systemd
e o wakeup-sync, que roda como root acordado pelo wakeup-sync.path quando o
arquivo muda - por isso criar alarme aqui nao pede senha. O "aplicando..." na
linha e a janela de ~1s entre salvar e o timer existir.
"""
import json, os, signal, subprocess, time
import gi

import audio_estado  # daqui do lado: devolve audio que um alarme morto deixou

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

BASE = os.path.dirname(os.path.realpath(__file__))
CONFIG = os.environ.get("WAKEUP_JSON",
                        os.path.expanduser("~/.config/wakeup/alarmes.json"))
ESTADO = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/wakeup/estado.json"

# o teste é curto de propósito: 5 checagens de 3s em vez de 720 de 5s
TESTE = {"ALVO": "5", "INTERVALO": "3"}


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


def rodando():
    """O estado do despertador, se tiver um no ar (de verdade ou em teste)."""
    try:
        with open(ESTADO) as f:
            e = json.load(f)
        os.kill(e["pid"], 0)
        return e
    except (OSError, ValueError, KeyError, TypeError):
        return None


def resumo(e):
    """Uma linha do que está acontecendo lá dentro, pro teste."""
    if not e:
        return "começando..."
    if e.get("fase") == "tocando":
        return 'tocando — fala "stop"'
    if e.get("fase") == "prova":
        linha = f"prova: {e.get('sucessos', 0)}/{e.get('alvo', 0)} pontos"
        if e.get("nivel", 0) >= 3:
            return f"{linha} — SIRENE NÍVEL {e['nivel']}"
        return f"{linha}, {e.get('falhas', 0)} falha(s) seguida(s)"
    if e.get("fase") == "acordado":
        return "acordado! bom dia"
    return "começando..."


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

        self.linhas, self.horas, self.teste = {}, ler(), None
        self.connect("close-request", self.ao_fechar)
        self.desenhar()
        # se um alarme morreu no soco, o fone fica mudo ate alguem devolver
        if not rodando() and audio_estado.devolver():
            self.avisar("devolvi o áudio que um alarme anterior deixou trocado")
        GLib.timeout_add_seconds(2, self.atualizar)

    def desenhar(self):
        for linha in self.linhas.values():
            self.grupo.remove(linha)
        self.linhas = {}
        for hora in self.horas:
            linha = Adw.ActionRow(title=f"<span size='xx-large'>{hora}</span>",
                                  subtitle="todo dia")
            testar = Gtk.Button(icon_name="media-playback-start-symbolic",
                                valign=Gtk.Align.CENTER,
                                tooltip_text="testar agora (toca de verdade)")
            testar.add_css_class("flat")
            testar.connect("clicked", self.testar, hora)
            linha.add_suffix(testar)
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
                                  body="Toca todo dia nesse horário.")
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

    # ---- teste: o único lugar do app que para um despertador. O alarme de
    # verdade não tem botão nenhum aqui, senão eu desarmava ele da cama.

    def testar(self, _botao, hora):
        se_ja_tem = rodando()
        if se_ja_tem:
            return self.avisar("o despertador já está no ar")
        try:
            self.teste = subprocess.Popen(
                [f"{BASE}/main.sh"], env={**os.environ, **TESTE},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)  # sessão própria: dá pra matar o grupo
        except OSError as e:
            return self.avisar(f"não consegui começar o teste: {e}")
        dialogo = self.dialogo_teste(hora)
        dialogo.connect("response", lambda *_: self.parar_teste())
        dialogo.present(self)
        GLib.timeout_add_seconds(1, self.vigiar_teste, dialogo)

    def dialogo_teste(self, hora):
        """A janela do teste, com o botão de cancelar que só ela tem."""
        dialogo = Adw.AlertDialog(
            heading=f"Testando o alarme das {hora}",
            body='A música vai tocar. Fala "stop" e encara a câmera até fechar '
                 "os 5 pontos.\n\nPra ouvir a sirene, desvia o olho uns 10s.")
        dialogo.rotulo = Gtk.Label(label="começando...", css_classes=["dim-label"])
        dialogo.set_extra_child(dialogo.rotulo)
        dialogo.add_response("parar", "Parar teste")
        dialogo.set_response_appearance("parar", Adw.ResponseAppearance.DESTRUCTIVE)
        dialogo.set_can_close(False)  # sair só pelo botão
        return dialogo

    def vigiar_teste(self, dialogo):
        if not self.teste or self.teste.poll() is not None:
            dialogo.force_close()
            self.avisar("teste encerrado")
            self.teste = None
            return GLib.SOURCE_REMOVE
        dialogo.rotulo.set_label(resumo(rodando()))
        return GLib.SOURCE_CONTINUE

    def parar_teste(self):
        """SIGTERM primeiro: é ele que faz o alarme devolver o áudio."""
        self.sinalizar(signal.SIGTERM)
        GLib.timeout_add_seconds(8, self.insistir)

    def insistir(self):
        if self.teste and self.teste.poll() is None:  # travou, aí não tem jeito bonito
            self.sinalizar(signal.SIGKILL)
        return GLib.SOURCE_REMOVE

    def sinalizar(self, sinal):
        try:
            os.killpg(os.getpgid(self.teste.pid), sinal)
        except (OSError, AttributeError):
            pass

    def ao_fechar(self, _janela):
        if self.teste and self.teste.poll() is None:
            self.parar_teste()  # fechar o app não deixa teste tocando sozinho
        return False

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
