"""tcpdump 텍스트(`-nn -tt -i any`)를 패킷 이벤트로 바꾼다.

캡처는 kind 노드 컨테이너의 네트워크 네임스페이스에서 돈다. 파드가 보낸 패킷은
파드 쪽 veth에 `In`으로 먼저 잡히므로, NetworkPolicy가 그 뒤에 버리더라도 시도는 남는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 1791010806.607479 vethf02ccfb0 In  IP 10.244.0.5.49060 > 172.66.147.243.80: Flags [S], seq 17...
LINE = re.compile(
    r"^(?P<ts>\d+\.\d+)\s+(?P<iface>\S+)\s+(?P<dir>In|Out|P|M|B)\s+IP\s+"
    r"(?P<src>\d+\.\d+\.\d+\.\d+)\.(?P<sport>\d+)\s+>\s+"
    r"(?P<dst>\d+\.\d+\.\d+\.\d+)\.(?P<dport>\d+):\s+(?P<rest>.*)$"
)
SYN = re.compile(r"Flags \[S\], seq (?P<seq>\d+)")
DNS_QUERY = re.compile(
    r"^(?P<id>\d+)\+?\s+(?:\[\S+\]\s+)?(?P<qtype>A|AAAA|PTR|CNAME|SRV|TXT|HTTPS|SVCB)\?\s+(?P<name>\S+)\s"
)
DNS_ANSWER = re.compile(r"^(?P<id>\d+)\*?-?\|?\$?\s+(?:\S+\s+)?\d+/\d+/\d+")
A_RECORD = re.compile(r"\bA (\d+\.\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class Packet:
    ts: float
    iface: str
    direction: str
    src: str
    sport: int
    dst: str
    dport: int
    kind: str  # syn | udp | dns_query | dns_answer
    seq: int | None = None
    dns_id: int | None = None
    qname: str | None = None
    answers: tuple[str, ...] = ()


def parse_line(line: str) -> Packet | None:
    m = LINE.match(line.strip())
    if not m:
        return None
    base = {
        "ts": float(m["ts"]),
        "iface": m["iface"],
        "direction": m["dir"],
        "src": m["src"],
        "sport": int(m["sport"]),
        "dst": m["dst"],
        "dport": int(m["dport"]),
    }
    rest = m["rest"]
    if (s := SYN.search(rest)) is not None:
        return Packet(**base, kind="syn", seq=int(s["seq"]))
    if rest.startswith("Flags"):
        return None
    if base["dport"] == 53 and (q := DNS_QUERY.match(rest)) is not None:
        return Packet(**base, kind="dns_query", dns_id=int(q["id"]), qname=q["name"].rstrip("."))
    if base["sport"] == 53 and (a := DNS_ANSWER.match(rest)) is not None:
        return Packet(
            **base, kind="dns_answer", dns_id=int(a["id"]), answers=tuple(A_RECORD.findall(rest))
        )
    # 캡처 필터가 SYN과 UDP만 남기므로, 여기까지 온 나머지는 UDP다.
    return Packet(**base, kind="udp")


def parse(lines) -> list[Packet]:
    out = []
    for line in lines:
        p = parse_line(line)
        if p is not None:
            out.append(p)
    return out
