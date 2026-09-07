#!/usr/bin/env bash
# Baixa os modelos e a musica nao vao pro git (peso e copyright).
set -e
cd "$(dirname "$(readlink -f "$0")")"

uv sync

[ -d model ] || {
  curl -L -o /tmp/vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
  unzip -q /tmp/vosk.zip -d /tmp && mv /tmp/vosk-model-small-en-us-0.15 model && rm /tmp/vosk.zip
}

[ -f face_landmarker.task ] || curl -L -o face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

mkdir -p sons
[ -f sons/alarm.mp3 ] && echo "pronto" || echo "falta por um mp3 em sons/alarm.mp3"
