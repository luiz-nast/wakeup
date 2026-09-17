#!/usr/bin/env python3
"""Despertador: toca ate voce falar "stop", depois exige 1 hora de cara acordada.

Cinco threads vivas o tempo todo:
  guardiao    - musica ou sirene no ar: unmute + volume + alto-falante
  anti_shadow - o alarme inteiro: brilho da tela no maximo
  olheiro     - so na prova: le a webcam a 30fps pra nunca olhar frame velho
  vitrine     - mantem o painel.py aberto (janela com camera e placar)
  berro       - a sirene das falhas: 3 cutuca, 4 incomoda, 5 levanta

Saida de emergencia: systemctl stop alarm (devolve o fone antes de sair)
"""
import atexit, json, os, queue, signal, statistics, subprocess, sys, threading, time
import cv2, numpy as np, sounddevice as sd, mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions
from vosk import Model, KaldiRecognizer, SetLogLevel

import sirene  # daqui do lado: os bips das falhas

BASE = os.path.dirname(os.path.realpath(__file__))

# dao pra sobrescrever por env so pra testar: ALVO=3 VOLUME=15% ./main.sh
ALVO = int(os.environ.get("ALVO", 720))  # 720 x 5s = 1 hora de cara acordada
INTERVALO = int(os.environ.get("INTERVALO", 5))
VOLUME = os.environ.get("VOLUME", "70%")
OLHO_FECHADO = float(os.environ.get("OLHO", 0.5))  # calibra.py ajusta isso
FALHAS_MAX = 5
SIRENE_A_PARTIR = 3  # falhas seguidas pra sirene ligar (3 cutuca, 4 incomoda, 5 doi)
AMOSTRA = 1.5  # segundos de frames por checagem: a mediana ignora piscada
WARMUP = 3     # essa webcam sai do preto so depois de ~3s
PAINEL = os.environ.get("PAINEL", "1") != "0"    # PAINEL=0 roda sem janela
ESCUTAR = os.environ.get("ESCUTAR", "1") != "0"  # ESCUTAR=0 pula o "stop" (teste)
SIRENE = os.environ.get("SIRENE", "1") != "0"    # SIRENE=0 nao apita (teste)

# Alto-falante do notebook: achado na hora pelo nome da porta, porque o nome
# do sink muda com a distro e some quando o fone esta plugado.
ALTO_FALANTE = "Speaker"

# O painel.py le daqui: estado.json e o frame mais novo (tmpfs, some no boot)
PAINEL_DIR = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/wakeup"
os.makedirs(PAINEL_DIR, exist_ok=True)

SetLogLevel(-1)
modelo = Model(f"{BASE}/model")
rosto = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=f"{BASE}/face_landmarker.task"),
    output_face_blendshapes=True, num_faces=1,
    running_mode=vision.RunningMode.IMAGE))

log = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)
sh = lambda *c: subprocess.run(c, check=False, capture_output=True)

tocando, olhando = threading.Event(), threading.Event()
frame_atual, mpv = [None], None
nivel = [0]  # falhas seguidas; de SIRENE_A_PARTIR pra cima a sirene liga
estado = {"pid": os.getpid(), "fase": "", "alvo": ALVO, "sucessos": 0,
          "falhas": 0, "falhas_max": FALHAS_MAX, "intervalo": INTERVALO,
          "olho": OLHO_FECHADO, "mic": 0, "ouvindo": "", "checagens": [],
          "nivel": 0}


def publicar(**mudou):
    """Atualiza o estado.json do painel. Troca atomica: o painel nunca le
    arquivo pela metade. Erro aqui nunca derruba o alarme."""
    estado.update(mudou)
    try:
        with open(f"{PAINEL_DIR}/estado.tmp", "w") as f:
            json.dump(estado, f)
        os.replace(f"{PAINEL_DIR}/estado.tmp", f"{PAINEL_DIR}/estado.json")
    except OSError:
        pass


def mostrar_frame(f):
    try:
        cv2.imwrite(f"{PAINEL_DIR}/frame.tmp.jpg", f)
        os.replace(f"{PAINEL_DIR}/frame.tmp.jpg", f"{PAINEL_DIR}/frame.jpg")
    except (OSError, cv2.error):
        pass


# ---------------------------------------------------------------- threads ---

def pactl_json(*args):
    # LC_ALL=C: com locale pt o pactl solta acento e quebra o json
    r = subprocess.run(["pactl", "-f", "json", *args], capture_output=True,
                       text=True, env={**os.environ, "LC_ALL": "C"})
    try:
        return json.loads(r.stdout)
    except ValueError:
        return []


def achar_alto_falante():
    """(o sink inteiro do pactl, a porta do alto-falante) ou None."""
    for s in pactl_json("list", "sinks"):
        for p in s.get("ports", []):
            if ALTO_FALANTE in p["name"]:
                return s, p["name"]
    return None


# o que era meu antes do alarme mexer, pra devolver igualzinho depois
perfil_original = {}  # placa -> perfil
audio_original = {}   # sink -> (volumes por canal, mudo)


def devolver_audio():
    """Desfaz tudo que o alarme mexeu: volume, mudo e perfil da placa.

    Volume antes do perfil: depois de voltar pro fone o sink do alto-falante
    some, e ai nao daria mais pra ajustar ele."""
    for sink, (volumes, mudo) in audio_original.items():
        sh("pactl", "set-sink-volume", sink, *volumes)
        sh("pactl", "set-sink-mute", sink, "1" if mudo else "0")
    audio_original.clear()
    for placa, perfil in perfil_original.items():
        sh("pactl", "set-card-profile", placa, perfil)
    perfil_original.clear()


def volume_agora():
    """Sirene no ultimo nivel toca no talo, doa a quem doer."""
    return "100%" if nivel[0] >= FALHAS_MAX else VOLUME


def forcar_audio():
    """Fone esquecido plugado nao adianta: o som vai pro alto-falante,
    desmutado e no volume certo.

    A placa esconde o alto-falante de dois jeitos, e isso cobre os dois:
      Mint   - um sink so, com porta Speaker e Headphones: troca a porta
      Ubuntu - um perfil com Speaker e outro com Headphones: com o fone
               plugado o sink do alto-falante nem existe, troca o perfil"""
    for c in pactl_json("list", "cards"):
        if ALTO_FALANTE in c["active_profile"]:
            continue
        p = next((p for p in c["profiles"] if ALTO_FALANTE in p), None)
        if p:
            perfil_original.setdefault(c["name"], c["active_profile"])
            sh("pactl", "set-card-profile", c["name"], p)
    t0 = time.time()  # o sink novo leva um instante pra aparecer
    while not (alvo := achar_alto_falante()) and time.time() - t0 < 2:
        time.sleep(0.2)
    if alvo:
        sink, porta = alvo
        if sink["name"] not in audio_original:  # guarda antes de encostar
            audio_original[sink["name"]] = (
                [str(c["value"]) for c in sink.get("volume", {}).values()],
                sink.get("mute", False))
        sh("pactl", "set-default-sink", sink["name"])
        if sink.get("active_port") != porta:
            sh("pactl", "set-sink-port", sink["name"], porta)
    sh("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
    sh("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", volume_agora())


def guardiao():
    """Enquanto a musica toca - ou a sirene apita - mantem o som no
    alto-falante, desmutado e no volume. Mutar, trocar pro fone ou matar o
    mpv nao adianta: volta em 1s."""
    global mpv
    while True:
        if tocando.is_set() or nivel[0] >= SIRENE_A_PARTIR:
            forcar_audio()  # antes do spawn: o mpv ja nasce no alto-falante
        elif perfil_original or audio_original:
            devolver_audio()
        if tocando.is_set():
            if mpv is None or mpv.poll() is not None:
                mpv = subprocess.Popen(
                    ["mpv", "--no-video", "--no-terminal", "--loop=inf",
                     "--volume=100", f"{BASE}/sons/alarm.mp3"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif mpv:
            mpv.terminate(), mpv.wait()
            mpv = None
        time.sleep(1)


def anti_shadow():
    """Tela no brilho maximo o alarme inteiro. Escurecer nao adianta.

    O backlight e root-only, mas o logind deixa a sessao ativa mudar o dela.
    A sessao muda a cada login, entao e redescoberta toda vez."""
    dev = next(iter(os.listdir("/sys/class/backlight")), None)
    if not dev:
        log("AVISO: sem controle de brilho nessa maquina")
        return
    teto = open(f"/sys/class/backlight/{dev}/max_brightness").read().strip()
    while True:
        s = subprocess.run(["loginctl", "show-user", str(os.getuid()),
                            "--property=Display", "--value"],
                           capture_output=True, text=True).stdout.strip()
        if s:
            sh("gdbus", "call", "--system", "--dest", "org.freedesktop.login1",
               "--object-path", f"/org/freedesktop/login1/session/{s}",
               "--method", "org.freedesktop.login1.Session.SetBrightness",
               "backlight", dev, teto)
        time.sleep(2)


def olheiro():
    """Le a webcam a 30fps e guarda so o frame mais novo.

    Sem isso o V4L2 enfileira TUDO: medi 2000+ frames de fila depois de 5s
    parado, e o atraso cresce a cada ciclo - a prova avaliava imagem de
    minutos atras. A camera so abre durante a prova (LED apagado no resto)."""
    cam, ultimo_jpg = None, 0
    while True:
        if not olhando.is_set():
            if cam:
                cam.release()
                cam = None
            frame_atual[0] = None
            time.sleep(0.2)
            continue
        if cam is None:
            cam = cv2.VideoCapture(0)
            if not cam.isOpened():  # sessao trancada: sem ACL na webcam
                cam.release()
                cam = None
                frame_atual[0] = None
                time.sleep(1)
                continue
            t0 = time.time()
            while time.time() - t0 < WARMUP:
                cam.read()
        ok, f = cam.read()
        if ok:
            frame_atual[0] = f
            if PAINEL and time.time() - ultimo_jpg > 1 / 15:  # painel a 15fps
                ultimo_jpg = time.time()
                mostrar_frame(f)
        else:
            frame_atual[0] = None
            cam.release()
            cam = None
            time.sleep(1)


def env_grafico():
    """O systemd sobe o alarme fora da sessao grafica. A sessao poe DISPLAY
    e XAUTHORITY no user manager no login (conferido no GNOME), entao pega
    de la."""
    env = dict(os.environ)
    r = subprocess.run(["systemctl", "--user", "show-environment"],
                       capture_output=True, text=True)
    for linha in r.stdout.splitlines():
        k, _, v = linha.partition("=")
        if k in ("DISPLAY", "XAUTHORITY"):
            env.setdefault(k, v)
    env["QT_QPA_PLATFORM"] = "xcb"  # o Qt que vem no opencv so tem xcb
    return env


def vitrine():
    """Mantem o painel.py aberto. Processo separado: se ele travar ou a
    janela for fechada, o alarme nem sente - e ela volta em 2s. Antes do
    login nao tem display, e ele so vai tentando de novo."""
    p = None
    while True:
        if p is None or p.poll() is not None:
            p = subprocess.Popen([sys.executable, f"{BASE}/painel.py"],
                                 env=env_grafico(), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        time.sleep(2)


def berro():
    """A sirene das falhas. O nivel e o proprio contador de falhas seguidas,
    entao ela piora a cada checagem perdida e cala na primeira que eu passo."""
    ultimo = 0
    while True:
        n = nivel[0]
        if n != ultimo:
            ultimo = n
            if n >= SIRENE_A_PARTIR:
                log(f"sirene nivel {n}")
        if n >= SIRENE_A_PARTIR and SIRENE:
            try:  # trocar a saida de audio no meio do bip pode derrubar o
                sirene.tocar(n)  # stream, e ficar sem sirene o alarme inteiro
            except Exception as e:
                log(f"sirene falhou ({e}), tentando de novo")
                time.sleep(1)
        else:
            time.sleep(0.2)


# ------------------------------------------------------------------ logica ---

def escutar_stop():
    if not ESCUTAR:  # teste: pula a fala e cai direto na camera
        return
    rec = KaldiRecognizer(modelo, 16000, '["stop", "[unk]"]')
    q = queue.Queue()
    with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16",
                           channels=1, callback=lambda i, f, t, s: q.put(bytes(i))):
        while True:
            dado = q.get()
            if rec.AcceptWaveform(dado):
                ouvi = json.loads(rec.Result())["text"]
                if "stop" in ouvi:
                    return
            else:
                ouvi = json.loads(rec.PartialResult())["partial"]
            # pro painel: nivel do mic de -60dB (silencio) a 0dB, e o que ouviu
            x = np.frombuffer(dado, np.int16) / 32768
            db = 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9)
            publicar(mic=round(float(min(max((db + 60) / 60, 0), 1)), 2),
                     ouvindo=ouvi)


def acordado():
    """Rosto na camera COM olho aberto.

    Amostra ~1.5s de frames e usa a mediana: uma piscada (~200ms) nao
    derruba a checagem, mas cochilar derruba."""
    scores = []
    t0 = time.time()
    while time.time() - t0 < AMOSTRA:
        f = frame_atual[0]
        if f is not None:
            r = rosto.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                      data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
            if r.face_blendshapes:
                b = {c.category_name: c.score for c in r.face_blendshapes[0]}
                scores.append((b["eyeBlinkLeft"] + b["eyeBlinkRight"]) / 2)
        time.sleep(0.1)
    if not scores:
        return False, "sem rosto", None
    m = statistics.median(scores)
    return (m < OLHO_FECHADO), ("olho fechado" if m >= OLHO_FECHADO else ""), m


def sair():
    """Ctrl+C ou systemctl stop: mata a musica, cala a sirene e devolve o audio."""
    tocando.clear()
    nivel[0] = 0
    sirene.parar()
    if mpv:
        mpv.kill()
    devolver_audio()  # devolve volume, mudo e perfil como estavam


atexit.register(sair)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # sem isso o atexit nao roda
for t in (guardiao, anti_shadow, olheiro, berro) + ((vitrine,) if PAINEL else ()):
    threading.Thread(target=t, daemon=True).start()

sucessos = 0
while sucessos < ALVO:
    if nivel[0] >= FALHAS_MAX:  # so quando venho da sirene no talo: o guardiao
        # so passaria aqui daqui a 1s, e a musica comecaria em 100%
        sh("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", VOLUME)
    nivel[0] = 0  # a musica ja e barulho suficiente
    tocando.set()
    publicar(fase="tocando", sucessos=sucessos, falhas=0, nivel=0, ouvindo="", mic=0)
    log("tocando. fale 'stop'")
    try:
        escutar_stop()
    except Exception as e:
        log(f"microfone falhou ({e}), indo direto pra camera")
    tocando.clear()

    olhando.set()
    publicar(fase="prova")
    falhas = 0
    while sucessos < ALVO and falhas < FALHAS_MAX:
        ok, motivo, score = acordado()
        if ok:
            sucessos, falhas = sucessos + 1, 0
            log(f"ok {sucessos}/{ALVO} (faltam ~{(ALVO-sucessos)*INTERVALO//60} min)")
        else:
            falhas += 1
            log(f"{motivo} ({falhas}/{FALHAS_MAX})")
        nivel[0] = falhas  # 3, 4 e 5 acendem a sirene; acertar cala
        publicar(sucessos=sucessos, falhas=falhas, nivel=nivel[0], checagens=(
            estado["checagens"] + [[ok, motivo or "ok", score]])[-30:])
        time.sleep(max(0, INTERVALO - AMOSTRA))
    olhando.clear()

    if falhas >= FALHAS_MAX:
        log(f"perdeu os {sucessos} pontos, voltando pra musica")
        sucessos = 0

nivel[0] = 0
publicar(fase="acordado", nivel=0)
log("=== acordado! bom dia ===")
if PAINEL:
    time.sleep(8)  # da tempo do bom dia aparecer antes do systemd fechar tudo
