# NetworkPolicy 적용 시점 점검: policy-timing-20261003 (kind on macOS, colima)

```bash
scripts/policy_timing.sh <out-dir> 8   # 새 kind 클러스터 2개에서 한 번씩 실행
```

환경: kind v0.33.0, kindnetd v20260820-69b56db7, kindest/node v1.37.0 (`cluster-*/versions.txt`).
대상: `1.1.1.1:443`, 연결 제한시간 2초. 이미지는 `airgap-rag-app:0.1.0`(Python)을 썼다.

두 설명을 나눠 보려고 순서를 정했다.

- A. 정책 반영 지연: 이미 떠 있는 파드에서 연결이 되는 것을 확인한 뒤 기본 거부 egress 정책을 걸고, 바로 다시 시도한다.
- B. 새 파드 틈: A에서 정책이 막는 것을 확인하고 10초 더 기다린 뒤 새 파드 8개를 하나씩 만든다. 각 파드는 프로세스 시작 후 0, 0.5, 1, 2, 3, 5초에 연결을 시도한다(앞 시도가 막히면 2초 제한시간만큼 뒤로 밀린다).

## 결과

A. 기존 파드(`cluster-*/A-existing-pod.txt`)

| 클러스터 | 정책 전 | 정책 적용 후 첫 시도 |
|---|---|---|
| cluster-1 | CONNECTED | BLOCKED (`kubectl exec` 시작 기준 0.06초 뒤) |
| cluster-2 | CONNECTED | BLOCKED (`kubectl exec` 시작 기준 0.06초 뒤) |

`kubectl exec`가 파드 안에서 연결을 시작하기까지 걸리는 시간이 있어, 정책 반영 지연은 이 시험의 해상도보다 짧았다는 것만 말할 수 있다.

B. 새 파드(`cluster-*/B-new-pods.txt`), 두 클러스터 합계 16개

| 프로세스 시작 후 시도 시각 | 연결됨 / 시도 |
|---|---|
| 0초 | 16 / 16 |
| 약 0.5초 | 7 / 16 (cluster-1 3/8, cluster-2 4/8) |
| 약 1초 이후 | 0 / 64 |

패킷 기록(`cluster-*/capture.txt`, 대상 주소로 가는 SYN만)에서 노드 eth0으로 NAT되어 나간 SYN은 cluster-1 12개, cluster-2 13개다. 각각 B에서 연결된 수(11, 12)에 A의 정책 전 연결 1개를 더한 값과 같다.

## 해석

이 환경에서는 "정책 자체를 늦게 프로그래밍한다"보다 "정책이 이미 적용된 네임스페이스에서도 새로 뜬 파드는 첫 1초 안팎 동안 밖으로 나갈 수 있다"는 쪽이 관찰과 맞는다. 원인(새 파드 IP가 정책 엔진의 대상 집합에 들어가기까지의 시간 등)은 이 시험으로 확인하지 않았다.
