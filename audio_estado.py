#!/usr/bin/env python3
"""O que o alarme mexeu no audio, anotado em disco.

O alarme troca o perfil da placa pra achar o alto-falante (com fone plugado o
sink dele nem existe). Se o processo morrer no soco - SIGKILL, queda de luz,
reboot no meio - o perfil fica trocado. E o WirePlumber guarda perfil em
disco, entao nem reiniciar a maquina resolve: o fone simplesmente some.

Por isso o "antes" e escrito aqui assim que o alarme encosta no audio, e so
sai quando ele devolve. Quem abrir depois - o proprio alarme ou o app da
agenda - acha o arquivo e devolve por ele.

So stdlib: os dois lados importam isso (o alarme roda no .venv, o app no
python do sistema).
"""
import json, os, subprocess

ARQUIVO = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "wakeup", "audio-antes.json")

sh = lambda *c: subprocess.run(c, check=False, capture_output=True)


def guardar(perfis, sinks, padrao):
    """perfis: placa -> perfil; sinks: sink -> (volumes, mudo); padrao: sink."""
    try:
        os.makedirs(os.path.dirname(ARQUIVO), exist_ok=True)
        with open(f"{ARQUIVO}.tmp", "w") as f:
            json.dump({"perfis": perfis, "sinks": sinks, "padrao": padrao}, f)
        os.replace(f"{ARQUIVO}.tmp", ARQUIVO)
    except OSError:
        pass


def devolver():
    """Devolve o que estiver anotado e apaga a anotacao. True se tinha algo.

    Volume e mudo primeiro: depois que o perfil volta, o sink do alto-falante
    some e nao daria mais pra ajustar ele. O padrao por ultimo, que ele so
    existe depois do perfil certo estar no ar."""
    try:
        with open(ARQUIVO) as f:
            antes = json.load(f)
    except (OSError, ValueError):
        return False
    for sink, (volumes, mudo) in antes.get("sinks", {}).items():
        sh("pactl", "set-sink-volume", sink, *volumes)
        sh("pactl", "set-sink-mute", sink, "1" if mudo else "0")
    for placa, perfil in antes.get("perfis", {}).items():
        sh("pactl", "set-card-profile", placa, perfil)
    if antes.get("padrao"):
        sh("pactl", "set-default-sink", antes["padrao"])
    try:
        os.remove(ARQUIVO)
    except OSError:
        pass
    return bool(antes.get("perfis") or antes.get("sinks"))


if __name__ == "__main__":  # pra rodar na mao quando tudo mais falhar
    print("devolvi o audio" if devolver() else "nada anotado pra devolver")
