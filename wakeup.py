#!/usr/bin/env python3
"""Despertador: toca ate voce falar "stop", depois exige 30 min de cara acordada.

Tres threads vivas o tempo todo:
  guardiao    - so quando a musica toca: unmute + volume + alto-falante
  anti_shadow - o alarme inteiro: brilho da tela no maximo
  olheiro     - so na prova: le a webcam a 30fps pra nunca olhar frame velho

Saida de emergencia: systemctl stop alarm
"""
import atexit, json, os, queue, statistics, subprocess, threading, time
import cv2, sounddevice as sd, mediapipe as mp
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

# Alto-falante do notebook. `pactl list sinks` mostra o nome se a placa mudar.
SINK = ("alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic"
        ".HiFi__hw_sofhdadsp__sink")

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


# ---------------------------------------------------------------- threads ---

def forcar_audio():
    """Fone esquecido plugado nao adianta: o som vai pro alto-falante,
    desmutado e no volume certo."""
    sh("pactl", "set-default-sink", SINK)
    sh("pactl", "set-sink-port", SINK, "[Out] Speaker")
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
    cam = None
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
        else:
            frame_atual[0] = None
            cam.release()
            cam = None
            time.sleep(1)


# ------------------------------------------------------------------ logica ---

def escutar_stop():
    rec = KaldiRecognizer(modelo, 16000, '["stop", "[unk]"]')
    q = queue.Queue()
    with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16",
                           channels=1, callback=lambda i, f, t, s: q.put(bytes(i))):
        while True:
            if rec.AcceptWaveform(q.get()):
                if "stop" in json.loads(rec.Result())["text"]:
                    return


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
        return False, "sem rosto"
    m = statistics.median(scores)
    return (m < OLHO_FECHADO), ("olho fechado" if m >= OLHO_FECHADO else "")


for t in (guardiao, anti_shadow, olheiro):
    threading.Thread(target=t, daemon=True).start()
atexit.register(lambda: mpv and mpv.kill())

sucessos = 0
while sucessos < ALVO:
    tocando.set()
    log("tocando. fale 'stop'")
    try:
        escutar_stop()
    except Exception as e:
        log(f"microfone falhou ({e}), indo direto pra camera")
    tocando.clear()

    olhando.set()
    falhas = 0
    while sucessos < ALVO and falhas < FALHAS_MAX:
        ok, motivo = acordado()
        if ok:
            sucessos, falhas = sucessos + 1, 0
            log(f"ok {sucessos}/{ALVO} (faltam ~{(ALVO-sucessos)*INTERVALO//60} min)")
        else:
            falhas += 1
            log(f"{motivo} ({falhas}/{FALHAS_MAX})")
        time.sleep(max(0, INTERVALO - AMOSTRA))
    olhando.clear()

    if falhas >= FALHAS_MAX:
        log(f"perdeu os {sucessos} pontos, voltando pra musica")
        sucessos = 0

log("=== acordado! bom dia ===")
