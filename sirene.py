#!/usr/bin/env python3
"""A sirene das falhas: quanto mais eu falho seguido, pior fica.

  3 falhas - bip curto e baixo, de vez em quando: so pra me cutucar
  4 falhas - bip duplo, alto e quadrado: ja incomoda
  5 falhas - sirene subindo, sem pausa, no volume maximo (e a musica volta)

Quem manda o nivel e o wakeup.py. Pra ouvir cada um na mao:

    ./sirene.py 4
"""
import sys, time
import numpy as np, sounddevice as sd

TAXA = 48000
RAMPA = 0.005  # 5ms de sobe-e-desce em cada ponta pra nao estalar

# nivel: (frequencia, sobe ate, duracao, silencio depois, amplitude, quadrada)
NIVEIS = {
    3: (880, None, 0.12, 1.20, 0.25, False),
    4: (1200, None, 0.16, 0.45, 0.60, True),
    5: (700, 2200, 0.70, 0.08, 1.00, True),
}


def onda(freq, ate, dur, amp, quadrada):
    n = int(TAXA * dur)
    if ate:  # varredura: a sirene sobe de freq ate `ate`
        fase = 2 * np.pi * np.cumsum(np.linspace(freq, ate, n)) / TAXA
    else:
        fase = 2 * np.pi * freq * np.arange(n) / TAXA
    som = np.sign(np.sin(fase)) if quadrada else np.sin(fase)
    envelope = np.ones(n)
    r = int(TAXA * RAMPA)
    envelope[:r] = np.linspace(0, 1, r)
    envelope[-r:] = np.linspace(1, 0, r)
    return (amp * som * envelope).astype(np.float32)


def tocar(nivel):
    """Um ciclo do bip desse nivel, com o silencio depois. Bloqueia."""
    freq, ate, dur, silencio, amp, quadrada = NIVEIS[min(max(nivel, 3), 5)]
    som = onda(freq, ate, dur, amp, quadrada)
    sd.play(som, TAXA, blocking=True)
    if nivel == 4:  # o 4 e bip duplo
        time.sleep(0.08)
        sd.play(som, TAXA, blocking=True)
    time.sleep(silencio)


def parar():
    sd.stop()


if __name__ == "__main__":
    nivel = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"sirene nivel {nivel} - ctrl+c pra parar")
    while True:
        tocar(nivel)
