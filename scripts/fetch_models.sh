#!/usr/bin/env bash
# 반입 전(인터넷이 되는 쪽) 준비: 모델 파일을 models/에 받는다.
#  - LLM: kanana-1.5-2.1b-instruct Q4_K_M GGUF (커밋·sha256 고정)
#  - 임베딩: TEI를 한 번 온라인으로 띄워 TEI가 실제로 읽는 파일로 HF 캐시를 채운다.
#    TEI의 CPU 백엔드는 아키텍처에 따라 필요한 파일이 다르다(arm64는 onnx/model.onnx를 받는다).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODELS="$ROOT/models"
ARCH="$(docker info --format '{{.Architecture}}' | sed 's/aarch64/arm64/; s/x86_64/amd64/')"
TEI_IMAGE="${TEI_IMAGE:-ghcr.io/huggingface/text-embeddings-inference:cpu-$([[ $ARCH == arm64 ]] && echo arm64-)1.9.4}"
GGUF_URL="https://huggingface.co/DevQuasar/kakaocorp.kanana-1.5-2.1b-instruct-2505-GGUF/resolve/4d3b6203857d893ebfcaca6c51901e3ea1d00d00/kakaocorp.kanana-1.5-2.1b-instruct-2505.Q4_K_M.gguf"
GGUF_SHA256="24d3db59d0af2c85c0afc0bbc99da1174b73ef6728bce2650bd91bec28ad1c81"
GGUF="$MODELS/kanana-1.5-2.1b-instruct-q4_k_m.gguf"
EMBED_MODEL="intfloat/multilingual-e5-small"
EMBED_REVISION="614241f622f53c4eeff9890bdc4f31cfecc418b3"

mkdir -p "$MODELS/hf-cache"
if [[ ! -f "$GGUF" ]]; then
  curl -fsSL --retry 3 -o "$GGUF.part" "$GGUF_URL"
  mv "$GGUF.part" "$GGUF"
fi
echo "$GGUF_SHA256  $GGUF" | sha256sum -c - 2>/dev/null || echo "$GGUF_SHA256  $GGUF" | shasum -a 256 -c -

docker rm -f tei-warm >/dev/null 2>&1 || true
docker run -d --name tei-warm -v "$MODELS/hf-cache:/data" -p 18080:80 "$TEI_IMAGE" \
  --model-id "$EMBED_MODEL" --revision "$EMBED_REVISION" >/dev/null
for _ in $(seq 1 120); do
  if curl -sf localhost:18080/health >/dev/null; then break; fi
  sleep 5
done
curl -sf localhost:18080/embed -H 'Content-Type: application/json' -d '{"inputs":["query: 확인"]}' >/dev/null
docker rm -f tei-warm >/dev/null
echo "models ready: $MODELS"
