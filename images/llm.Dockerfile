# syntax=docker/dockerfile:1
# EKS용(미실행): 모델을 이미지에 넣은 llama-server. kind에서는 hostPath로 같은 파일을 붙인다.
ARG LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server-b11347@sha256:970168341efef973da5666ea5c362fa428ee66ba0dfa621b0f69bf3feb3320f7

FROM curlimages/curl:8.22.0 AS model
ARG GGUF_URL=https://huggingface.co/DevQuasar/kakaocorp.kanana-1.5-2.1b-instruct-2505-GGUF/resolve/4d3b6203857d893ebfcaca6c51901e3ea1d00d00/kakaocorp.kanana-1.5-2.1b-instruct-2505.Q4_K_M.gguf
ARG GGUF_SHA256=24d3db59d0af2c85c0afc0bbc99da1174b73ef6728bce2650bd91bec28ad1c81
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]
WORKDIR /tmp
RUN curl -fsSL --retry 3 -o model.gguf "${GGUF_URL}" \
 && echo "${GGUF_SHA256}  model.gguf" | sha256sum -c -

FROM ${LLAMA_IMAGE}
COPY --from=model --chown=10001:10001 /tmp/model.gguf /models/kanana-1.5-2.1b-instruct-q4_k_m.gguf
USER 10001:10001
