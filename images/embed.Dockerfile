# syntax=docker/dockerfile:1
# EKS용(미실행): TEI에 임베딩 모델 디렉터리를 넣는다.
# build/e5는 scripts/fetch_models.sh로 데운 HF 캐시의 스냅숏을 심볼릭 링크를 풀어 복사한 것이다.
ARG TEI_IMAGE=ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4
FROM ${TEI_IMAGE}
COPY build/e5 /models/e5
