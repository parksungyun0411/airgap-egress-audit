"""캡처한 패킷을 '구성 요소 × 단계 × 외부 목적지' 시도 목록으로 바꾼다.

입력(실행 폴더 하나):
  capture.txt   tcpdump -nn -tt -i any 텍스트
  pods.jsonl    주기적으로 찍은 파드 IP 목록 {"ts":…, "pods":[{"ns","name","component","ip"}]}
  stages.json   단계 경계 시각 {"install_start", "pods_running", "pods_ready", "first_request_end", "idle_end"}
  meta.json     {"kube_dns_ip", "pod_cidr", "service_cidr", "node_ips", "namespace"}
"""

from __future__ import annotations

import csv
import ipaddress
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from egressaudit.capture import Packet, parse

STAGES = ["install", "startup", "first_request", "idle"]
FINDING_COLUMNS = [
    "component",
    "stage",
    "protocol",
    "destination",
    "ip",
    "port",
    "attempts",
    "left_cluster",
    "first_seen_s",
]


@dataclass
class Context:
    kube_dns_ip: str
    namespace: str
    node_ips: set[str]
    internal_nets: list
    host_search: tuple[str, ...] = ()
    pods: dict[str, tuple[str, str]] = field(default_factory=dict)  # ip -> (ns, component)

    @classmethod
    def from_meta(cls, meta: dict) -> Context:
        nets = [ipaddress.ip_network(n) for n in (meta["pod_cidr"], meta["service_cidr"])]
        nets.append(ipaddress.ip_network("127.0.0.0/8"))
        return cls(
            kube_dns_ip=meta["kube_dns_ip"],
            namespace=meta["namespace"],
            node_ips=set(meta["node_ips"]),
            internal_nets=nets,
            host_search=tuple(meta.get("search_domains", [])),
        )

    def internal(self, ip: str) -> bool:
        if ip in self.node_ips:
            return True
        addr = ipaddress.ip_address(ip)
        return any(addr in n for n in self.internal_nets)

    def component(self, ip: str) -> str:
        if ip in self.node_ips:
            return "node"
        ns, comp = self.pods.get(ip, ("?", "unknown"))
        return comp if ns == self.namespace else f"{ns}/{comp}"


def load_pods(path: Path) -> dict[str, tuple[str, str]]:
    """IP가 재사용되지 않는다는 가정으로, 실행 중 한 번이라도 본 파드를 모두 기억한다."""
    pods: dict[str, tuple[str, str]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        for p in json.loads(line)["pods"]:
            if p.get("ip"):
                pods[p["ip"]] = (p["ns"], p["component"])
    return pods


def stage_of(ts: float, stages: dict) -> str | None:
    bounds = [
        ("install", stages["install_start"], stages["pods_running"]),
        ("startup", stages["pods_running"], stages["pods_ready"]),
        ("first_request", stages["pods_ready"], stages["first_request_end"]),
        ("idle", stages["first_request_end"], stages["idle_end"]),
    ]
    for name, start, end in bounds:
        if start <= ts < end:
            return name
    return None


CLUSTER_SUFFIXES = ("{ns}.svc.cluster.local", "svc.cluster.local", "cluster.local")


def _strip_host_search(name: str, host_search: tuple[str, ...]) -> str:
    for domain in host_search:
        if name.endswith("." + domain.lower()):
            return name[: -len(domain) - 1]
    return name


def external_names(qnames, namespace: str, host_search: tuple[str, ...] = ()) -> set[str]:
    """질의 이름들에서 외부 이름 집합을 만든다.

    - 클러스터 검색 도메인이 붙지 않은 이름(역방향 조회 제외)
    - `<이름>.<ns>.svc.cluster.local` 꼴에서 <이름>에 점이 있는 것(검색 경로가 처음 붙이는 형태)
    """
    out: set[str] = set()
    ns_suffix = "." + CLUSTER_SUFFIXES[0].format(ns=namespace)
    for q in qnames:
        name = _strip_host_search(q.rstrip(".").lower(), host_search)
        if name.endswith((".in-addr.arpa", ".ip6.arpa")):
            continue
        if name.endswith(ns_suffix):
            rest = name[: -len(ns_suffix)]
            if "." in rest:
                out.add(rest)
        elif not name.endswith("cluster.local"):
            out.add(name)
    return out


def canonical_name(
    qname: str, namespace: str, externals: set[str], host_search: tuple[str, ...] = ()
) -> str | None:
    """질의가 외부 이름의 한 형태이면 그 외부 이름을, 아니면 None을 돌려준다.

    한 외부 이름에 대해 그대로 묻는 것, 클러스터 검색 도메인 세 가지를 붙인 것,
    노드 검색 도메인을 붙인 것을 모두 같은 이름의 시도로 센다.
    """
    name = _strip_host_search(qname.rstrip(".").lower(), host_search)
    if name in externals:
        return name
    for suffix in CLUSTER_SUFFIXES:
        suffix = "." + suffix.format(ns=namespace)
        if name.endswith(suffix) and name[: -len(suffix)] in externals:
            return name[: -len(suffix)]
    return None


def findings(packets: list[Packet], ctx: Context, stages: dict) -> list[dict]:
    t0 = stages["install_start"]
    agg: dict[tuple, dict] = {}
    pod_syn_seqs: set[int] = set()
    pod_udp_keys: set[tuple] = set()
    queries: dict[tuple, str] = {}
    ip_names: dict[str, str] = {}
    dns_clients: list[tuple[float, str, str]] = []  # (ts, 외부 이름, 구성 요소)
    coredns_ips = {ip for ip, (ns, comp) in ctx.pods.items() if comp == "kube-dns"}
    externals = external_names(
        (p.qname for p in packets if p.kind == "dns_query"), ctx.namespace, ctx.host_search
    )

    def canon(qname: str) -> str | None:
        return canonical_name(qname, ctx.namespace, externals, ctx.host_search)

    # TCP·UDP: 노드 eth0으로 NAT되어 나간 사본이 있으면 정책이 버리지 못하고 밖으로 나간 시도다.
    nat_copies = [
        (p.seq, p.sport, p.dst, p.dport, p.ts)
        for p in packets
        if p.iface == "eth0" and p.direction == "Out" and p.src in ctx.node_ips
    ]

    def left_cluster(p: Packet) -> bool:
        key = (p.seq, p.sport, p.dst, p.dport)
        return any(c[:4] == key and abs(c[4] - p.ts) < 0.05 for c in nat_copies)

    def add(component, ts, protocol, destination, ip, port, escaped=False, attempt=True):
        stage = stage_of(ts, stages)
        if stage is None:
            return
        key = (component, stage, protocol, destination, port)
        row = agg.setdefault(
            key,
            {
                "component": component,
                "stage": stage,
                "protocol": protocol,
                "destination": destination,
                "ip": set(),
                "port": port,
                "attempts": 0,
                "left_cluster": 0,
                "first_seen_s": round(ts - t0, 1),
            },
        )
        if ip:
            row["ip"].add(ip)
        row["attempts"] += int(attempt)
        row["left_cluster"] += int(escaped)

    # 1차: DNS 질의·응답을 짝지어 IP → 이름 표를 만든다. 클라이언트 쪽 사본(서비스 IP 방향)만 쓴다.
    for p in packets:
        if p.kind == "dns_query" and p.dst == ctx.kube_dns_ip:
            queries[(p.src, p.sport, p.dns_id)] = p.qname
        elif p.kind == "dns_answer" and p.src == ctx.kube_dns_ip:
            name = queries.get((p.dst, p.dport, p.dns_id))
            if name:
                for ip in p.answers:
                    ip_names.setdefault(ip, canon(name) or name)

    def from_pod(p: Packet) -> bool:
        return p.iface.startswith("veth") and p.direction == "In" and p.src not in ctx.node_ips

    for p in packets:
        if from_pod(p) and p.kind in ("udp", "dns_query"):
            pod_udp_keys.add((p.sport, p.dst, p.dport))

    for p in packets:
        if not from_pod(p):
            continue
        if p.kind == "dns_query" and p.dst == ctx.kube_dns_ip:
            name = canon(p.qname)
            if name:
                dns_clients.append((p.ts, name, ctx.component(p.src)))
                add(ctx.component(p.src), p.ts, "dns", name, "", 53)
        elif p.kind == "dns_query" and p.src in coredns_ips and not ctx.internal(p.dst):
            # CoreDNS가 외부 이름을 상위 리졸버로 넘긴 질의. 직전에 그 이름을 물은 구성 요소의 행에 센다.
            name = canon(p.qname) or p.qname
            asked = [c for c in dns_clients if c[1] == name and 0 <= p.ts - c[0] < 5]
            comp = asked[-1][2] if asked else "kube-system/coredns"
            add(comp, asked[-1][0] if asked else p.ts, "dns", name, "", 53, True, attempt=False)
        elif p.kind == "syn":
            pod_syn_seqs.add(p.seq)
            if not ctx.internal(p.dst):
                add(
                    ctx.component(p.src),
                    p.ts,
                    "tcp",
                    ip_names.get(p.dst, p.dst),
                    p.dst,
                    p.dport,
                    left_cluster(p),
                )
        elif p.kind == "udp":
            if ctx.internal(p.dst):
                continue
            comp = ctx.component(p.src)
            if p.dport == 53 and p.src in coredns_ips:
                continue
            add(comp, p.ts, "udp", ip_names.get(p.dst, p.dst), p.dst, p.dport, left_cluster(p))

    # 2차: 노드 자신(containerd·kubelet)이 밖으로 보낸 것. 파드 패킷이 NAT되어 나간 사본은 뺀다.
    for p in packets:
        if not (p.iface == "eth0" and p.direction == "Out" and p.src in ctx.node_ips):
            continue
        if ctx.internal(p.dst):
            continue
        if p.kind == "syn" and p.seq not in pod_syn_seqs:
            add("node", p.ts, "tcp", ip_names.get(p.dst, p.dst), p.dst, p.dport, True)
        elif p.kind in ("udp", "dns_query") and (p.sport, p.dst, p.dport) not in pod_udp_keys:
            add("node", p.ts, "udp", p.qname or p.dst, p.dst, p.dport, True)

    for row in agg.values():
        row["ip"] = " ".join(sorted(row["ip"], key=lambda a: tuple(int(x) for x in a.split("."))))
    order = {s: i for i, s in enumerate(STAGES)}
    return sorted(
        agg.values(),
        key=lambda r: (r["component"], order[r["stage"]], r["destination"], r["protocol"]),
    )


def analyze_dir(run_dir: Path) -> list[dict]:
    meta = json.loads((run_dir / "meta.json").read_text())
    stages = json.loads((run_dir / "stages.json").read_text())
    ctx = Context.from_meta(meta)
    ctx.pods = load_pods(run_dir / "pods.jsonl")
    packets = parse((run_dir / "capture.txt").read_text().splitlines())
    rows = findings(packets, ctx, stages)
    with (run_dir / "findings.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FINDING_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return rows


def matrix(rows: list[dict], components: list[str]) -> dict[str, dict[str, int]]:
    """구성 요소 × 단계별로 서로 다른 외부 목적지(프로토콜·목적지·포트) 수."""
    seen: dict[tuple, set] = defaultdict(set)
    for r in rows:
        seen[(r["component"], r["stage"])].add((r["protocol"], r["destination"], r["port"]))
    comps = list(components) + sorted({r["component"] for r in rows} - set(components))
    return {c: {s: len(seen[(c, s)]) for s in STAGES} for c in comps}
