# reports/

감사 실행 하나가 폴더 하나다. 폴더 이름은 실행 환경과 날짜를 담는다.

```
reports/
└── <run-id>/                  # 예: kind-20261003 (kind on macOS, colima)
    ├── REPORT.md              # before · after · leak 표와 게이트 결과
    ├── before.png …           # 구성 요소 × 단계 히트맵
    └── before/ after/ leak/   # 원자료 사본
        ├── capture.txt        # 노드 netns tcpdump (SYN·UDP)
        ├── coredns.log        # CoreDNS log 플러그인 질의 기록
        ├── findings.csv       # 분석 결과(구성 요소, 단계, 목적지, 포트, 시도, 노드 밖으로 나간 수)
        ├── gate.txt           # 허용 목록 비교 결과
        ├── policy-check.txt   # 기본 거부 정책 자체 점검(0초·15초)
        ├── stages.json        # 단계 경계 시각(unix 초)
        ├── pods.jsonl         # 5초마다 찍은 파드 IP
        ├── meta.json          # 클러스터 대역, DNS 서비스 IP, 유휴 시간
        ├── versions.txt       # kind·쿠버네티스·kindnetd·CoreDNS·docker·colima 버전
        ├── images.txt         # 노드에 반입된 이미지와 digest
        ├── values.yaml        # 설치에 쓴 Helm 값 전체
        ├── first-request.json # 첫 요청(ingest·ask) 응답
        └── log-*.txt          # 구성 요소 로그
```

- 원자료는 `results/<run-id>/`에 먼저 쌓이고(커밋하지 않음), `egress-audit report`가 필요한 파일만 이 폴더로 복사한다.
- `tests/fixtures/`의 합성 데이터로 만든 결과는 이 폴더에 두지 않는다.
- EKS 변형은 실행하지 않았으므로 `eks-*` 폴더가 없다.
