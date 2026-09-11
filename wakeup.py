#!/usr/bin/env python3
"""Despertador: toca ate voce falar "stop", depois exige 30 min de cara acordada.

Quatro threads vivas o tempo todo:
  guardiao    - so quando a musica toca: unmute + volume + alto-falante
  anti_shadow - o alarme inteiro: brilho da tela no maximo
  olheiro     - so na prova: le a webcam a 30fps pra nunca olhar frame velho
  vitrine     - mantem o painel.py aberto (janela com camera e placar)

Saida de emergencia: systemctl stop alarm (devolve o fone antes de sair)
"""
import atexit, json, os, queue, signal, statistics, subprocess, sys, threading, time
import cv2, numpy as np, sounddevice as sd, mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions
from vosk import Model, KaldiRecognizer, SetLogLevel

BASE = os.path.dirname(os.path.realpath(__file__))

# dao pra sobrescrever por env so pra testar: ALVO=3 VOLUME=15% ./main.sh
ALVO = int(os.environ.get("ALVO", 360))  # 360 x 5s = 30 min de cara acordada
INTERVALO = int(os.environ.get("INTERVALO", 5))
VOLUME = os.environ.get("VOLUME", "70%")
OLHO_FECHADO = float(os.environ.get("OLHO", 0.5))  # calibra.py ajusta isso
FALHAS_MAX = 5
AMOSTRA = 1.5  # segundos de frames por checagem: a mediana ignora piscada
WARMUP = 3     # essa webcam sai do preto so depois de ~3s
PAINEL = os.environ.get("PAINEL", "1") != "0"  # PAINEL=0 roda sem janela

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
estado = {"pid": os.getpid(), "fase": "", "alvo": ALVO, "sucessos": 0,
          "falhas": 0, "falhas_max": FALHAS_MAX, "intervalo": INTERVALO,
          "olho": OLHO_FECHADO, "mic": 0, "ouvindo": "", "checagens": []}


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
    """(sink, porta, porta ativa) do alto-falante, ou None."""
    for s in pactl_json("list", "sinks"):
        for p in s.get("ports", []):
            if ALTO_FALANTE in p["name"]:
                return s["name"], p["name"], s.get("active_port")
    return None


perfil_original = {}  # placa -> perfil de antes do alarme, pra devolver o fone


def devolver_fone():
    for placa, perfil in perfil_original.items():
        sh("pactl", "set-card-profile", placa, perfil)
    perfil_original.clear()


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
        sink, porta, ativa = alvo
        sh("pactl", "set-default-sink", sink)
        if ativa != porta:
            sh("pactl", "set-sink-port", sink, porta)
    sh("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
    sh("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", VOLUME)


def guardiao():
    """Enquanto a musica toca: mantem o mpv vivo, no alto-falante e no volume.
    Mutar, trocar pro fone ou matar o mpv nao adianta - volta em 1s."""
    global mpv
    while True:
        if tocando.is_set():
            forcar_audio()  # antes do spawn: o mpv ja nasce no alto-falante
            if mpv is None or mpv.poll() is not None:
                mpv = subprocess.Popen(
                    ["mpv", "--no-video", "--no-terminal", "--loop=inf",
                     "--volume=100", f"{BASE}/sons/alarm.mp3"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif mpv:
            mpv.terminate(), mpv.wait()
            mpv = None
            devolver_fone()
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


# ------------------------------------------------------------------ logica ---

def escutar_stop():
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
    """Ctrl+C ou systemctl stop: mata a musica e devolve o fone."""
    tocando.clear()
    if mpv:
        mpv.kill()
    devolver_fone()


atexit.register(sair)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # sem isso o atexit nao roda
for t in (guardiao, anti_shadow, olheiro) + ((vitrine,) if PAINEL else ()):
    threading.Thread(target=t, daemon=True).start()

sucessos = 0
while sucessos < ALVO:
    tocando.set()
    publicar(fase="tocando", sucessos=sucessos, falhas=0, ouvindo="", mic=0)
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
        publicar(sucessos=sucessos, falhas=falhas, checagens=(
            estado["checagens"] + [[ok, motivo or "ok", score]])[-30:])
        time.sleep(max(0, INTERVALO - AMOSTRA))
    olhando.clear()

    if falhas >= FALHAS_MAX:
        log(f"perdeu os {sucessos} pontos, voltando pra musica")
        sucessos = 0

publicar(fase="acordado")
log("=== acordado! bom dia ===")
if PAINEL:
    time.sleep(8)  # da tempo do bom dia aparecer antes do systemd fechar tudo
