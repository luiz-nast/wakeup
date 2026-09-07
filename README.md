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

## Notas

- O mp3 não está no repo. Põe o teu em `sons/alarm.mp3`.
- O nome do sink de áudio está fixo no `wakeup.py` pro meu hardware.
  `pactl list sinks` mostra o teu.
