#!/usr/bin/env python3
"""Descobre o limiar de olho fechado pra SUA cara. Voce controla o ritmo.

    ./calibra.py

No fim ele imprime a linha OLHO=... pra colar no wakeup.py.
"""
import statistics as st, threading, time
import cv2, mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions
import os

BASE = os.path.dirname(os.path.realpath(__file__))
rosto = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=f"{BASE}/face_landmarker.task"),
    output_face_blendshapes=True, num_faces=1,
    running_mode=vision.RunningMode.IMAGE))

cam = cv2.VideoCapture(0)
frame = [None]


def olheiro():
    while True:
        ok, f = cam.read()
        if ok:
            frame[0] = f


threading.Thread(target=olheiro, daemon=True).start()
print("esquentando a camera...")
time.sleep(4)


def coletar(seg):
    v = []
    t0 = time.time()
    while time.time() - t0 < seg:
        f = frame[0]
        if f is not None:
            r = rosto.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                      data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
            if r.face_blendshapes:
                b = {c.category_name: c.score for c in r.face_blendshapes[0]}
                v.append((b["eyeBlinkLeft"] + b["eyeBlinkRight"]) / 2)
        time.sleep(0.1)
    return v


input("\n[1/2] OLHOS ABERTOS olhando pra tela. Enter quando estiver pronto...")
print("     medindo 10s, pode piscar normal...")
a = coletar(10)

input("\n[2/2] Agora vai FECHAR OS OLHOS por 10s. Enter, fecha, e so abre quando ouvir o beep...")
print("     medindo 10s...")
f = coletar(10)
print("\a     pode abrir")

cam.release()

if not a or not f:
    print("\nnao consegui ver seu rosto. mais luz, ou mais de frente pra camera.")
    raise SystemExit(1)

ma, mf = st.median(a), st.median(f)
print(f"\n  aberto : mediana={ma:.3f}  (n={len(a)})")
print(f"  fechado: mediana={mf:.3f}  (n={len(f)})")

if mf - ma < 0.1:
    print("\n  os dois ficaram parecidos demais - o detector nao esta separando")
    print("  aberto de fechado na sua cara/luz. me manda esses numeros.")
    raise SystemExit(1)

print(f"\n  >>> use OLHO={(ma + mf) / 2:.2f} <<<")
print("  (edite a linha OLHO_FECHADO no wakeup.py)")
