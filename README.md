# wakeup

Meu despertador. Toca 6h todo dia e não deixa eu voltar a dormir.

Antes eu tinha um `.sh` que só tocava um mp3. Funcionava até o dia em que eu
aprendi a apertar mute e voltar pra cama. Esse aqui não deixa.

## O que ele faz

Toca a música até eu falar **"stop"**. Aí a webcam tem que me ver **de olho
aberto** por 30 minutos. Se eu sumir ou cochilar 5 checagens seguidas, os
pontos zeram e a música volta do começo.

Três threads:

- **guardiao** — a cada 1s desmuta, põe o volume em 70%, joga o som pro
  alto-falante do notebook (fone esquecido plugado não salva ninguém) e sobe
  outro `mpv` se eu matar o processo.
- **anti_shadow** — brilho da tela no máximo o alarme inteiro.
- **olheiro** — lê a webcam a 30fps e guarda só o frame mais novo.

Esse último é o que mais deu trabalho. O V4L2 enfileira tudo: depois de 5s
parado tinha 2000+ frames na fila, e o atraso crescia a cada ciclo — eu punha
a cara na frente e nada acontecia, porque ele estava julgando imagem de
minutos atrás. Ler numa thread separada da detecção resolveu.

Câmera que não abre (tela trancada) conta como falha e a música volta. É de
propósito: se eu não logar, não desarmo.

## Rodando

```
./setup.sh          # deps + modelos
./calibra.py        # limiar de olho aberto pra minha cara
ALVO=3 ./main.sh    # teste rápido
```

Os units vão pra `/etc/systemd/system/`. `alarm.timer` dispara 06:00 com
`WakeSystem=true`, então acorda o note suspenso de tampa fechada.

Pra desligar quando der errado: `systemctl stop alarm`. Não tem `Restart=`
no service justamente pra isso.

## A tampa do notebook (sem isso nada funciona)

Eu durmo com o note fechado. Se fechar a tampa suspende, o alarme não toca —
o `WakeSystem=true` até acorda a máquina, mas ela volta a dormir.

No meu Mint (Cinnamon 6.6) é isto:

```
gsettings set org.cinnamon.settings-daemon.plugins.power lid-close-ac-action nothing
gsettings set org.cinnamon.settings-daemon.plugins.power lid-close-battery-action nothing
gsettings set org.cinnamon.settings-daemon.plugins.power inhibit-lid-switch true
```

Isso é por ambiente, não por distro. No GNOME o schema é
`org.gnome.settings-daemon.plugins.power` com as mesmas chaves, mas nas
versões novas ele ignora e quem manda é o systemd.

O jeito que vale em qualquer distro com systemd, e que não depende do
desktop estar rodando, é o logind:

```
# /etc/systemd/logind.conf
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
```

E `sudo systemctl restart systemd-logind` (derruba a sessão gráfica, então
faça antes de abrir as coisas).

Detalhe que descobri conferindo o meu: o `logind.conf` aqui está vazio, então
o default é `suspend` — quem segura a tampa é só o `csd-power` do Cinnamon,
via inhibitor lock. Dá pra ver quem está segurando com:

```
systemd-inhibit --list | grep lid
```

Se um dia eu trocar de desktop ou a sessão do Cinnamon cair, a tampa volta a
suspender e o despertador morre junto. O `logind.conf` é o cinto de segurança.

## Mudei de máquina ou de distro

Antes de formatar: salvar o `sons/alarm.mp3`. Ele não está no repo e o
`setup.sh` não baixa. O resto (`model/`, `face_landmarker.task`, `.venv/`)
o setup rebaixa sozinho.

Depois, conferir os quatro valores que estão presos na máquina antiga:

```
whoami                                          # User= no alarm.service
id -u                                           # XDG_RUNTIME_DIR=/run/user/<isso>
which uv                                        # caminho no main.sh
pactl list sinks | grep -E "Name:|Active Port"  # SINK e porta no wakeup.py
```

Instalar o que não costuma vir: `mpv` (esse é o que toca a música),
`uv`, e depois `./setup.sh`.

Se o desktop não for o Cinnamon, ler a seção da tampa aí em cima — no GNOME
novo o gsettings é ignorado e quem manda é o `logind.conf`.

## Notas

- O mp3 não está no repo. Põe o teu em `sons/alarm.mp3`.
- O nome do sink de áudio está fixo no `wakeup.py` pro meu hardware.
  `pactl list sinks` mostra o teu.
