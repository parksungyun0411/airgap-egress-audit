#!/usr/bin/env bash
# kind 클러스터 하나를 새로 만들어 RAG 스택을 설치하고, 설치·기동·첫 요청·유휴 네 단계 동안
# 밖으로 나가려는 시도를 기록한다.
#   scripts/audit.sh <before|after|leak> <out-dir>
# 환경변수: IDLE_SECONDS(기본 600), KEEP_CLUSTER=1(끝나고 클러스터를 남김), TEI_IMAGE(amd64용 태그 등)
set -euo pipefail

PROFILE="${1:?profile: before|after|leak}"
OUT="${2:?out dir}"
CLUSTER="${CLUSTER:-airgap}"
NS="${NAMESPACE:-rag}"
IDLE_SECONDS="${IDLE_SECONDS:-600}"
NODE="${CLUSTER}-control-plane"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_IMAGE="nicolaka/netshoot@sha256:b09d9b21381f47a79b3cbcb30da25266dc17186ea00ae65e99fdc51396f48e70"
ARCH="$(docker info --format '{{.Architecture}}' | sed 's/aarch64/arm64/; s/x86_64/amd64/')"
# TEI의 CPU 이미지는 아키텍처마다 태그가 다르다.
TEI_TAG="cpu-1.9.4"
if [[ $ARCH == arm64 ]]; then TEI_TAG="cpu-arm64-1.9.4"; fi
TEI_IMAGE="${TEI_IMAGE:-ghcr.io/huggingface/text-embeddings-inference:$TEI_TAG}"
IMAGES=(
  ghcr.io/ggml-org/llama.cpp:server-b11347
  "$TEI_IMAGE"
  qdrant/qdrant:v1.19.1
  airgap-rag-app:0.1.0
)

VALUES=(-f "$ROOT/charts/rag-stack/values.yaml")
case "$PROFILE" in
  before) ;;
  after) VALUES+=(-f "$ROOT/charts/rag-stack/values-after.yaml") ;;
  leak) VALUES+=(-f "$ROOT/charts/rag-stack/values-after.yaml" -f "$ROOT/charts/rag-stack/values-leak.yaml") ;;
  *) echo "알 수 없는 프로필: $PROFILE" >&2; exit 2 ;;
esac
VALUES+=(--set "components.embed.image=$TEI_IMAGE")

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

PIDS=()
cleanup() {
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  docker rm -f egress-capture >/dev/null 2>&1 || true
  if [[ "${KEEP_CLUSTER:-0}" != 1 ]]; then kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

# 1. 새 클러스터
log "클러스터 생성"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
sed "s#__MODELS_DIR__#$ROOT/models#" "$ROOT/kind/cluster.yaml" > "$OUT/kind-cluster.yaml"
kind create cluster --name "$CLUSTER" --config "$OUT/kind-cluster.yaml" --wait 120s >/dev/null

# 2. kindnet 정책 엔진은 nfqueue를 쓴다. 커널 모듈이 없으면 정책이 조용히 적용되지 않는다.
if ! docker exec "$NODE" test -e /proc/net/netfilter/nfnetlink_queue; then
  if command -v colima >/dev/null; then
    colima ssh -- sudo modprobe nfnetlink_queue
  else
    sudo modprobe nfnetlink_queue
  fi
  kubectl -n kube-system rollout restart ds/kindnet >/dev/null
  kubectl -n kube-system rollout status ds/kindnet --timeout=120s >/dev/null
fi
docker exec "$NODE" test -e /proc/net/netfilter/nfnetlink_queue \
  || { echo "nfnetlink_queue가 없어 NetworkPolicy가 적용되지 않는다. 중단한다." >&2; exit 1; }

# 3. 이미지 반입. 파드는 imagePullPolicy: Never라서 레지스트리에 갈 일이 없다.
log "이미지 반입"
TMP="$(mktemp -d)"
for img in "${IMAGES[@]}"; do
  docker image inspect "$img" >/dev/null 2>&1 || docker pull -q --platform "linux/$ARCH" "$img" >/dev/null
  docker save --platform "linux/$ARCH" -o "$TMP/img.tar" "$img"
  kind load image-archive --name "$CLUSTER" "$TMP/img.tar" >/dev/null
done
rm -rf "$TMP"

# 4. CoreDNS 질의 로그를 켠다(log 플러그인).
kubectl -n kube-system get cm coredns -o json \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); cf=c["data"]["Corefile"]; c["data"]["Corefile"]=cf if "\n    log\n" in cf else cf.replace("    errors\n","    errors\n    log\n",1); print(json.dumps(c))' \
  | kubectl apply -f - >/dev/null
kubectl -n kube-system rollout restart deploy/coredns >/dev/null
kubectl -n kube-system rollout status deploy/coredns --timeout=120s >/dev/null

# 5. 정책 자체 점검: 기본 거부 네임스페이스의 파드가 밖으로 나가지 못하는지 확인한다.
"$ROOT/scripts/policy_check.sh" > "$OUT/policy-check.txt" 2>&1 || true
log "정책 점검: $(tail -1 "$OUT/policy-check.txt")"

# 6. 메타데이터
SERVICE_CIDR="$(kubectl -n kube-system get pod -l component=kube-apiserver -o json \
  | python3 -c 'import json,sys; a=json.load(sys.stdin)["items"][0]["spec"]["containers"][0]["command"]; print([x.split("=",1)[1] for x in a if x.startswith("--service-cluster-ip-range")][0])')"
POD_CIDR="$(kubectl -n kube-system get ds kindnet -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="POD_SUBNET")].value}')"
SEARCH_DOMAINS="$(docker exec "$NODE" sh -c "awk '/^search/ {for (i=2;i<=NF;i++) print \$i}' /etc/resolv.conf" | tr '\n' ' ')"
python3 - "$OUT/meta.json" <<EOF
import json, subprocess, sys
dns = subprocess.check_output(["kubectl","-n","kube-system","get","svc","kube-dns","-o","jsonpath={.spec.clusterIP}"], text=True)
nodes = json.loads(subprocess.check_output(["kubectl","get","nodes","-o","json"], text=True))
ips = [a["address"] for n in nodes["items"] for a in n["status"]["addresses"] if a["type"] == "InternalIP"]
json.dump({"kube_dns_ip": dns, "pod_cidr": "$POD_CIDR", "service_cidr": "$SERVICE_CIDR",
           "node_ips": ips, "namespace": "$NS", "profile": "$PROFILE",
           "search_domains": "$SEARCH_DOMAINS".split(),
           "idle_seconds": int("$IDLE_SECONDS")}, open(sys.argv[1], "w"), indent=2)
EOF

# 7. 캡처 시작: 노드 컨테이너의 네트워크 네임스페이스에 붙어 모든 인터페이스에서
#    TCP SYN과 UDP를 기록한다. 컨트롤 플레인 내부 포트는 뺀다.
FILTER='(udp and not port 8472) or (tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn and not port 6443 and not port 10250 and not port 2379 and not port 2380 and not port 2381 and not port 10257 and not port 10259)'
docker rm -f egress-capture >/dev/null 2>&1 || true
docker run -d --name egress-capture --net "container:$NODE" --cap-add NET_ADMIN --cap-add NET_RAW \
  "$CAPTURE_IMAGE" tcpdump -i any -nn -tt -l "$FILTER" >/dev/null
sleep 3

# 8. 파드 IP를 5초마다 기록한다(구성 요소 귀속용).
(
  while true; do
    kubectl get pods -A -o json 2>/dev/null | python3 -c '
import json, sys, time
d = json.load(sys.stdin)
pods = []
for p in d["items"]:
    lab = p["metadata"].get("labels", {})
    comp = lab.get("app.kubernetes.io/name") or lab.get("k8s-app") or p["metadata"]["name"].rsplit("-", 2)[0]
    pods.append({"ns": p["metadata"]["namespace"], "name": p["metadata"]["name"], "component": comp,
                 "ip": p["status"].get("podIP"), "hostNetwork": p["spec"].get("hostNetwork", False)})
print(json.dumps({"ts": time.time(), "pods": [p for p in pods if not p["hostNetwork"]]}))' >> "$OUT/pods.jsonl" || true
    sleep 5
  done
) &
PIDS+=($!)

# 9. 단계별 실행
log "설치 ($PROFILE)"
T_INSTALL="$(now)"
helm upgrade --install rag "$ROOT/charts/rag-stack" -n "$NS" --create-namespace "${VALUES[@]}" >/dev/null
until kubectl -n "$NS" get pods -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null \
  | grep -q . && ! kubectl -n "$NS" get pods -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' | grep -vq '^Running$'; do
  sleep 1
done
T_RUNNING="$(now)"
log "기동 대기"
NOT_READY=""
if ! kubectl -n "$NS" wait --for=condition=Ready pods --all --timeout=1500s >/dev/null; then
  NOT_READY="$(kubectl -n "$NS" get pods --no-headers | awk '$2 !~ /^1\/1$/ {print $1}' | tr '\n' ' ')"
  log "Ready가 되지 않은 파드: $NOT_READY"
fi
T_READY="$(now)"

log "첫 요청"
kubectl -n "$NS" port-forward svc/rag 18000:8000 >/dev/null 2>&1 &
PIDS+=($!)
sleep 3
{
  echo "not_ready: ${NOT_READY:-none}"
  curl -fsS -X POST localhost:18000/ingest -H 'Content-Type: application/json' -d @"$ROOT/scripts/docs.json"
  echo
  curl -fsS -X POST localhost:18000/ask -H 'Content-Type: application/json' \
    -d '{"question":"전입신고는 언제까지 해야 하나요?"}'
  echo
} > "$OUT/first-request.json" 2>&1 || log "첫 요청 실패(기록은 계속한다)"
T_FIRST="$(now)"

log "유휴 ${IDLE_SECONDS}초"
sleep "$IDLE_SECONDS"
T_IDLE="$(now)"
printf '{"install_start": %s, "pods_running": %s, "pods_ready": %s, "first_request_end": %s, "idle_end": %s}\n' \
  "$T_INSTALL" "$T_RUNNING" "$T_READY" "$T_FIRST" "$T_IDLE" > "$OUT/stages.json"

# 10. 수집
sleep 2
docker logs egress-capture > "$OUT/capture.txt" 2>/dev/null
kubectl -n kube-system logs -l k8s-app=kube-dns --timestamps --tail=-1 --prefix > "$OUT/coredns.log"
helm get values rag -n "$NS" --all > "$OUT/values.yaml"
kubectl -n "$NS" describe pods > "$OUT/pods-describe.txt" 2>&1 || true
for c in llm embed qdrant rag; do kubectl -n "$NS" logs "deploy/$c" --timestamps > "$OUT/log-$c.txt" 2>&1 || true; done
{
  echo "kind: $(kind version)"
  echo "kubernetes: $(kubectl version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
  echo "node image: $(docker inspect -f '{{.Config.Image}}' "$NODE")"
  echo "kindnetd: $(kubectl -n kube-system get ds kindnet -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "coredns: $(kubectl -n kube-system get deploy coredns -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "docker: $(docker version --format '{{.Server.Version}}') ($ARCH)"
  if command -v colima >/dev/null; then echo "colima: $(colima version | head -1)"; fi
  echo "capture: $CAPTURE_IMAGE"
  echo "host: $(uname -sm)"
} > "$OUT/versions.txt"
docker exec "$NODE" crictl images --digests > "$OUT/images.txt"
# 노드의 digest는 docker save로 만든 단일 플랫폼 사본 기준이라, 레지스트리 digest를 따로 남긴다.
for img in "${IMAGES[@]}"; do
  echo "$img $(docker image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null || echo '-')"
done > "$OUT/host-images.txt"

# 11. 분석과 게이트
cd "$ROOT"
uv run egress-audit analyze --run-dir "$OUT"
uv run egress-audit gate --findings "$OUT/findings.csv" --out "$OUT/gate.txt" || true
log "완료: $OUT"
