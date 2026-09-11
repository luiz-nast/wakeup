#!/usr/bin/env python3
"""Painel do despertador: o que a camera ve, o olho agora e quanto falta.

So le o que o wakeup.py publica em $XDG_RUNTIME_DIR/wakeup/ (estado.json e
frame.jpg) - nao abre camera nem microfone. Se travar ou for fechado, o
alarme nem sente, e o wakeup.py abre de novo em 2s.

O olho e medido de novo aqui, a ~8fps, so pra mostrar ao vivo. Quem decide
ponto e falha continua sendo o wakeup.py.
"""
import json, os, time
import cv2, numpy as np

BASE = os.path.dirname(os.path.realpath(__file__))
DIR = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/wakeup"
JANELA = "wakeup"
W, H = 1040, 560
CX, CY = 20, 60    # canto da camera (640x480)
PX, PW = 690, 330  # coluna do placar

# BGR
FUNDO, CAIXA, TRILHO = (30, 26, 24), (44, 40, 38), (70, 64, 60)
TEXTO, APAGADO = (235, 235, 235), (130, 130, 130)
VERDE, VERMELHO, AMARELO = (100, 205, 100), (80, 80, 235), (60, 200, 235)
FONTE = cv2.FONT_HERSHEY_SIMPLEX

# contorno dos olhos no face mesh do mediapipe
OLHOS = ([33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],
         [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398])

FASES = {
    "tocando": ("TOCANDO - fale STOP", VERMELHO),
    "prova": ("PROVA - fica de olho aberto", AMARELO),
    "acordado": ("ACORDADO! bom dia", VERDE),
    "parado": ("despertador parado", APAGADO),
}


def ler_estado():
    try:
        with open(f"{DIR}/estado.json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ler_frame():
    """Frame mais novo, ou None se a camera esta desligada (nada novo ha 1s)."""
    p = f"{DIR}/frame.jpg"
    try:
        if time.time() - os.path.getmtime(p) > 1:
            return None
        return cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def criar_detector():
    """Mesmo modelo e mesma conta do wakeup.py: media dos dois eyeBlink."""
    import mediapipe as mp
    from mediapipe.tasks.python import vision, BaseOptions
    rosto = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=f"{BASE}/face_landmarker.task"),
        output_face_blendshapes=True, num_faces=1,
        running_mode=vision.RunningMode.IMAGE))

    def detectar(f):
        r = rosto.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                  data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
        if not r.face_blendshapes:
            return None, None
        b = {c.category_name: c.score for c in r.face_blendshapes[0]}
        h, w = f.shape[:2]
        lm = r.face_landmarks[0]
        olhos = [np.array([(lm[i].x * w, lm[i].y * h) for i in o], np.int32)
                 for o in OLHOS]
        return (b["eyeBlinkLeft"] + b["eyeBlinkRight"]) / 2, olhos
    return detectar


# ---------------------------------------------------------------- desenho ---

def escrever(img, s, x, y, escala=0.6, cor=TEXTO, grossura=1, centro=False):
    if centro:
        x -= cv2.getTextSize(s, FONTE, escala, grossura)[0][0] // 2
    cv2.putText(img, s, (x, y), FONTE, escala, cor, grossura, cv2.LINE_AA)


def barra(img, x, y, w, h, frac, cor, marca=None):
    cv2.rectangle(img, (x, y), (x + w, y + h), TRILHO, -1)
    cv2.rectangle(img, (x, y), (x + int(w * min(max(frac, 0), 1)), y + h), cor, -1)
    if marca is not None:  # linha do limite
        mx = x + int(w * marca)
        cv2.line(img, (mx, y - 5), (mx, y + h + 5), TEXTO, 2)


def desenhar(e, frame, score, olhos, ligado):
    tela = np.full((H, W, 3), FUNDO, np.uint8)
    fase = e.get("fase")
    if not ligado and fase != "acordado":
        fase = "parado" if e else None
    titulo, cor = FASES.get(fase, ("esperando o despertador...", APAGADO))
    escrever(tela, titulo, CX, 42, 1.0, cor, 2)
    escrever(tela, time.strftime("%H:%M:%S"), W - 140, 42, 0.8, APAGADO, 2)
    limite = e.get("olho", 0.5)

    # ---- camera
    meio = CX + 320
    if frame is not None:
        if olhos:
            cv2.polylines(frame, olhos, True,
                          VERMELHO if score >= limite else VERDE, 2, cv2.LINE_AA)
        tela[CY:CY + 480, CX:CX + 640] = frame
        if score is None:
            cv2.rectangle(tela, (CX, CY + 430), (CX + 640, CY + 480), (0, 0, 0), -1)
            escrever(tela, "SEM ROSTO - olha pra camera", meio, CY + 464,
                     0.8, VERMELHO, 2, True)
    else:
        cv2.rectangle(tela, (CX, CY), (CX + 640, CY + 480), CAIXA, -1)
        if fase == "tocando":
            escrever(tela, "fale", meio, CY + 170, 1.2, APAGADO, 2, True)
            escrever(tela, "STOP", meio, CY + 260, 3.2, VERMELHO, 7, True)
            escrever(tela, "microfone", meio - 200, CY + 330, 0.55, APAGADO)
            barra(tela, meio - 200, CY + 342, 400, 16, e.get("mic", 0), VERDE)
            ouvi = (e.get("ouvindo") or "").replace("[unk]", "?") or "..."
            escrever(tela, f"ouvi: {ouvi}", meio, CY + 410, 0.8, TEXTO, 2, True)
        elif fase == "prova":
            escrever(tela, "ligando a camera...", meio, CY + 230, 0.9, APAGADO, 2, True)
            escrever(tela, "(com a tela trancada ela nao abre)", meio, CY + 270,
                     0.6, APAGADO, 1, True)
        else:
            escrever(tela, "camera desligada", meio, CY + 250, 0.9, APAGADO, 2, True)

    # ---- placar
    alvo, sucessos = e.get("alvo", 1), e.get("sucessos", 0)
    escrever(tela, "PONTOS", PX, 88, 0.55, APAGADO)
    escrever(tela, f"{sucessos}/{alvo}", PX, 138, 1.5, TEXTO, 3)
    barra(tela, PX, 154, PW, 16, sucessos / alvo, VERDE)
    falta = (alvo - sucessos) * e.get("intervalo", 5)
    escrever(tela, f"faltam {falta // 60} min {falta % 60:02d} s", PX, 196, 0.6)

    falhas, fmax = e.get("falhas", 0), e.get("falhas_max", 5)
    escrever(tela, "FALHAS SEGUIDAS", PX, 244, 0.55, APAGADO)
    for i in range(fmax):
        cv2.circle(tela, (PX + 12 + i * 34, 270), 11,
                   VERMELHO if i < falhas else TRILHO, -1, cv2.LINE_AA)
    escrever(tela, f"com {fmax} a musica volta e zera", PX, 306, 0.5, APAGADO)

    escrever(tela, "OLHO AGORA", PX, 350, 0.55, APAGADO)
    if score is None:
        barra(tela, PX, 362, PW, 16, 0, TRILHO)
        escrever(tela, "sem rosto" if frame is not None else "-", PX, 406,
                 0.7, APAGADO, 2)
    else:
        aberto = score < limite
        cor = VERDE if aberto else VERMELHO
        barra(tela, PX, 362, PW, 16, 1 - score, cor, marca=1 - limite)
        escrever(tela, "aberto" if aberto else "FECHADO", PX, 406, 0.7, cor, 2)
        escrever(tela, f"abertura {1 - score:.2f}, precisa passar de {1 - limite:.2f}",
                 PX, 430, 0.45, APAGADO)

    escrever(tela, "ULTIMAS CHECAGENS", PX, 470, 0.55, APAGADO)
    chk = e.get("checagens", [])[-30:]
    for i, (ok, _, _) in enumerate(chk):
        x = PX + i * 11
        cv2.rectangle(tela, (x, 482), (x + 8, 500), VERDE if ok else VERMELHO, -1)
    if chk:
        escrever(tela, f"ultima: {chk[-1][1]}", PX, 526, 0.55)
    return tela


def main():
    # sem display o Qt morre aqui, antes de gastar tempo carregando modelo
    cv2.namedWindow(JANELA, cv2.WINDOW_AUTOSIZE)
    detectar = criar_detector()
    ultima, score, olhos, parado_desde = 0, None, None, None
    while True:
        e = ler_estado()
        ligado = vivo(e.get("pid"))
        if ligado:
            parado_desde = None
        else:
            parado_desde = parado_desde or time.time()
            if time.time() - parado_desde > 8:
                break
        frame = ler_frame()
        if frame is None:
            score, olhos = None, None
        else:
            frame = cv2.flip(cv2.resize(frame, (640, 480)), 1)  # espelho
            if time.time() - ultima > 0.12:
                ultima = time.time()
                score, olhos = detectar(frame)
        cv2.imshow(JANELA, desenhar(e, frame, score, olhos, ligado))
        cv2.waitKey(40)
        if cv2.getWindowProperty(JANELA, cv2.WND_PROP_VISIBLE) < 1:
            break  # fechou a janela: o wakeup.py abre de novo


if __name__ == "__main__":
    main()
