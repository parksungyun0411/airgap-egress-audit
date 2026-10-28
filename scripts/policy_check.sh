#!/usr/bin/env bash
# kindnet이 기본 거부 egress 정책을 실제로 적용하는지 확인한다.
# 파드가 뜨자마자 보낸 요청과 15초 기다린 뒤 보낸 요청을 각각 시도한다.
set -uo pipefail

NS=policy-check
kubectl create namespace "$NS" >/dev/null
cat <<'EOF' | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
spec:
  podSelector: {}
  policyTypes: [Egress]
EOF

for delay in 0 15; do
  kubectl -n "$NS" run "probe-$delay" --image=airgap-rag-app:0.1.0 --image-pull-policy=Never \
    --restart=Never --command -- python -c "
import socket, time
time.sleep($delay)
s = socket.socket(); s.settimeout(5)
try:
    s.connect(('1.1.1.1', 443)); print('delay=${delay}s: CONNECTED')
except OSError as e:
    print('delay=${delay}s: BLOCKED', type(e).__name__)
" >/dev/null
done
for delay in 0 15; do
  kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/probe-$delay" --timeout=120s >/dev/null
  kubectl -n "$NS" logs "probe-$delay"
done
kubectl delete namespace "$NS" --wait=false >/dev/null
