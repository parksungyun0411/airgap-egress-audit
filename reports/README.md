# reports/

감사 실행(before·after·leak 묶음) 하나가 폴더 하나다. 폴더 이름은 실행 환경과 날짜를 담는다. 정책 적용 시점 시험은 구조가 달라 따로 둔다.

```
reports/
├── <run-id>/                  # 예: kind-20261003 (kind on macOS, colima)
│   ├── REPORT.md              # before · after · leak 표와 게이트 결과
│   ├── before.png …           # 구성 요소 × 단계 히트맵
│   └── before/ after/ leak/   # 원자료 사본
│       ├── capture.txt        # 노드 netns tcpdump (SYN·UDP)
│       ├── coredns.log        # CoreDNS log 플러그인 질의 기록
│       ├── findings.csv       # 분석 결과(아래 열 설명)
│       ├── gate.txt           # 허용 목록 비교 결과
│       ├── policy-check.txt   # 기본 거부 정책 자체 점검(0초·15초)
│       ├── stages.json        # 단계 경계 시각(unix 초)
│       ├── pods.jsonl         # 5초마다 찍은 파드 IP
│       ├── meta.json          # 클러스터 대역, DNS 서비스 IP, 노드 IP, 네임스페이스, 유휴 시간
│       ├── versions.txt       # kind·쿠버네티스·kindnetd·CoreDNS·docker·colima 버전
│       ├── images.txt         # 노드에 반입된 이미지와 digest
│       ├── host-images.txt    # 호스트 docker 기준 레지스트리 digest
│       ├── kind-cluster.yaml  # 클러스터 생성에 쓴 kind 설정(호스트 경로는 <repo>로 바꿈)
│       ├── values.yaml        # 설치에 쓴 Helm 값 전체
│       ├── first-request.json # 첫 요청(ingest·ask) 응답
│       ├── pods-describe.txt  # 파드 상태(kind-20261003 이후 실행부터)
│       └── log-*.txt          # 구성 요소 로그
└── policy-timing-<날짜>/      # 정책 반영 지연과 새 파드 틈을 나눈 시험(scripts/policy_timing.sh)
    ├── REPORT.md              # 절차와 결과
    └── cluster-*/             # 클러스터마다 A-existing-pod.txt, B-new-pods.txt, capture.txt, versions.txt
```

`findings.csv` 열:

| 열 | 뜻 |
|---|---|
| `component` | 시도한 구성 요소(파드 IP로 귀속, 노드 자신이면 `node`) |
| `stage` | 단계(install, startup, first_request, idle) |
| `protocol` | dns, tcp, udp |
| `destination` | 외부 이름(DNS 응답으로 알 수 있을 때) 또는 IP |
| `ip` | 그 행에서 연결을 시도한 모든 목적지 IP(DNS 행은 비어 있음) |
| `port` | 목적지 포트 |
| `attempts` | 패킷·질의 수(재전송 포함) |
| `left_cluster` | 클러스터 밖으로 나간 수(TCP·UDP는 노드 eth0의 NAT 사본, DNS는 CoreDNS가 상위 리졸버로 넘긴 질의) |
| `first_seen_s` | helm 설치 시작부터 처음 본 시각(초) |

- 원자료는 `results/<run-id>/`에 먼저 쌓이고(커밋하지 않음), `egress-audit report`가 필요한 파일만 이 폴더로 복사한다.
- `tests/fixtures/`의 합성 데이터로 만든 결과는 이 폴더에 두지 않는다.
- EKS 변형은 실행하지 않았으므로 `eks-*` 폴더가 없다.
