#!/usr/bin/env bash
# kindnet NetworkPolicy 적용 시점을 두 가지로 나눠 잰다.
#   A. 정책 반영 지연: 이미 떠 있는 파드에 기본 거부 정책을 건 뒤, 언제부터 막히는가
#   B. 새 파드 틈: 정책이 적용된 것을 확인한 뒤 새로 만든 파드가 뜨자마자 밖으로 나갈 수 있는가
#   scripts/policy_timing.sh <out-dir> [repetitions]
# 클러스터(kind, 이름 airgap)와 airgap-rag-app 이미지가 이미 있어야 한다.
set -euo pipefail

OUT="${1:?out dir}"
REPS="${2:-8}"
NS=policy-timing
NODE="${CLUSTER:-airgap}-control-plane"
TARGET="${TARGET:-1.1.1.1}"
CAPTURE_IMAGE="nicolaka/netshoot@sha256:b09d9b21381f47a79b3cbcb30da25266dc17186ea00ae65e99fdc51396f48e70"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }

# 파드 안에서 TARGET:443 연결을 시도한다. 시도마다 컨테이너 시작 후 경과 시간과 결과를 찍는다.
PROBE='
import socket, sys, time
t0 = time.time()
for wait in [float(x) for x in sys.argv[1].split(",")]:
    while time.time() - t0 < wait:
        time.sleep(0.05)
    s = socket.socket(); s.settimeout(2)
    t = time.time() - t0
    try:
        s.connect(("'"$TARGET"'", 443)); r = "CONNECTED"
    except OSError as e:
        r = "BLOCKED " + type(e).__name__
    finally:
        s.close()
    print(f"t={t:5.2f}s {r}", flush=True)
'

kubectl delete namespace "$NS" --ignore-not-found --wait >/dev/null
kubectl create namespace "$NS" >/dev/null

docker rm -f policy-capture >/dev/null 2>&1 || true
docker run -d --name policy-capture --net "container:$NODE" --cap-add NET_ADMIN --cap-add NET_RAW \
  "$CAPTURE_IMAGE" tcpdump -i any -nn -tt -l "tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn and host $TARGET" >/dev/null
sleep 2

# A. 기존 파드에 정책을 건다.
kubectl -n "$NS" run existing --image=airgap-rag-app:0.1.0 --image-pull-policy=Never --restart=Never \
  --command -- sleep 3600 >/dev/null
kubectl -n "$NS" wait --for=condition=Ready pod/existing --timeout=120s >/dev/null
echo "existing pod IP: $(kubectl -n "$NS" get pod existing -o jsonpath='{.status.podIP}')" > "$OUT/A-existing-pod.txt"
kubectl -n "$NS" exec existing -- python -c "$PROBE" 0 >> "$OUT/A-existing-pod.txt"   # 정책 전: 연결돼야 한다

T_POLICY="$(now)"
cat <<'EOF' | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
spec:
  podSelector: {}
  policyTypes: [Egress]
EOF
echo "policy applied at $T_POLICY" >> "$OUT/A-existing-pod.txt"
for _ in $(seq 1 20); do
  t="$(now)"
  r="$(kubectl -n "$NS" exec existing -- python -c "$PROBE" 0 | sed 's/^t= *[0-9.]*s //')"
  echo "since_policy=$(python3 -c "print(f'{$t-$T_POLICY:.2f}')")s $r" >> "$OUT/A-existing-pod.txt"
  [[ "$r" == BLOCKED* ]] && break
done

# 정책이 막는 것을 확인한 뒤 10초 더 기다려 반영이 끝난 상태에서 B를 시작한다.
sleep 10

# B. 새 파드가 뜨자마자 시도한다. 각 파드는 0, 0.5, 1, 2, 3, 5초에 시도한다.
for i in $(seq 1 "$REPS"); do
  kubectl -n "$NS" run "new-$i" --image=airgap-rag-app:0.1.0 --image-pull-policy=Never --restart=Never \
    --command -- python -c "$PROBE" 0,0.5,1,2,3,5 >/dev/null
  kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/new-$i" --timeout=120s >/dev/null
  {
    echo "== new-$i ip=$(kubectl -n "$NS" get pod "new-$i" -o jsonpath='{.status.podIP}')"
    kubectl -n "$NS" logs "new-$i"
  } >> "$OUT/B-new-pods.txt"
done

sleep 2
docker logs policy-capture > "$OUT/capture.txt" 2>/dev/null
docker rm -f policy-capture >/dev/null
{
  echo "kind: $(kind version)"
  echo "kindnetd: $(kubectl -n kube-system get ds kindnet -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "node image: $(docker inspect -f '{{.Config.Image}}' "$NODE")"
  echo "target: $TARGET:443, connect timeout 2s, repetitions: $REPS"
} > "$OUT/versions.txt"
kubectl delete namespace "$NS" --wait=false >/dev/null
echo "done: $OUT"
