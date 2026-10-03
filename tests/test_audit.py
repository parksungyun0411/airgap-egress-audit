import shutil
from pathlib import Path

import pytest

from egressaudit import cli, gate
from egressaudit.analyze import Context, analyze_dir, external_name, findings, matrix, stage_of
from egressaudit.capture import parse, parse_line

FIXTURES = Path(__file__).parent / "fixtures"
STAGES = {
    "install_start": 1000.0,
    "pods_running": 1010.0,
    "pods_ready": 1050.0,
    "first_request_end": 1060.0,
    "idle_end": 1660.0,
}


@pytest.fixture
def clean_run(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(FIXTURES / "run-clean", run)
    return run


@pytest.fixture
def leak_run(clean_run):
    """깨끗한 실행에 누출 패킷을 일부러 덧붙인다."""
    with (clean_run / "capture.txt").open("a") as f:
        f.write((FIXTURES / "leak-lines.txt").read_text())
    return clean_run


@pytest.fixture
def empty_allowlist(tmp_path):
    p = tmp_path / "allow.toml"
    p.write_text("# 비어 있음\n")
    return p


def test_parse_syn_dns_query_answer_udp():
    syn = parse_line(
        "1791010806.607479 vethf02ccfb0 In  IP 10.244.0.5.49060 > 172.66.147.243.80: "
        "Flags [S], seq 1736847909, win 64240, length 0"
    )
    assert (syn.kind, syn.src, syn.dst, syn.dport, syn.seq) == (
        "syn",
        "10.244.0.5",
        "172.66.147.243",
        80,
        1736847909,
    )
    q = parse_line(
        "1791010976.400354 veth98f28ac4 In  IP 10.244.0.12.40193 > 10.96.0.10.53: "
        "12491+ A? telemetry.qdrant.io. (37)"
    )
    assert (q.kind, q.dns_id, q.qname) == ("dns_query", 12491, "telemetry.qdrant.io")
    a = parse_line(
        "1791010989.408153 veth664359bb Out IP 10.96.0.10.53 > 10.244.0.13.45693: "
        "53285 2/0/0 A 23.23.139.54, A 3.218.239.237 (107)"
    )
    assert (a.kind, a.dns_id, a.answers) == ("dns_answer", 53285, ("23.23.139.54", "3.218.239.237"))
    nx = parse_line(
        "1791010964.116481 veth49f6ad40 Out IP 10.96.0.10.53 > 10.244.0.11.55616: "
        "42254 NXDomain*- 0/1/0 (156)"
    )
    assert (nx.kind, nx.answers) == ("dns_answer", ())
    udp = parse_line(
        "1791010989.408339 veth664359bb In  IP 10.244.0.13.36403 > 1.1.1.1.123: "
        "NTPv7, unspecified, length 1"
    )
    assert (udp.kind, udp.dport) == ("udp", 123)
    # SYN-ACK이나 헤더 없는 줄은 버린다.
    assert parse_line("1.0 eth0 In IP 1.1.1.1.443 > 10.0.0.1.5: Flags [S.], seq 1, ack 2") is None
    assert parse_line("listening on any, link-type LINUX_SLL2") is None


@pytest.mark.parametrize(
    "qname, expected",
    [
        ("telemetry.qdrant.io.rag.svc.cluster.local.", "telemetry.qdrant.io"),
        ("telemetry.qdrant.io.svc.cluster.local", "telemetry.qdrant.io"),
        ("telemetry.qdrant.io", "telemetry.qdrant.io"),
        ("qdrant.rag.svc.cluster.local", None),
        ("qdrant.svc.cluster.local", None),
        ("kube-dns.kube-system.svc.cluster.local", None),
        ("20.0.244.10.in-addr.arpa", None),
    ],
)
def test_external_name(qname, expected):
    assert external_name(qname, "rag") == expected


def test_host_search_domain_is_folded_into_original_name():
    domain = ("abc.xx.internal.cloudapp.net",)
    assert external_name("huggingface.co.abc.xx.internal.cloudapp.net.", "rag", domain) == (
        "huggingface.co"
    )
    assert external_name("qdrant.rag.svc.cluster.local", "rag", domain) is None


def test_stage_boundaries():
    assert stage_of(1000.0, STAGES) == "install"
    assert stage_of(1010.0, STAGES) == "startup"
    assert stage_of(1055.0, STAGES) == "first_request"
    assert stage_of(1659.9, STAGES) == "idle"
    assert stage_of(999.0, STAGES) is None
    assert stage_of(1660.0, STAGES) is None


def test_clean_run_has_no_findings(clean_run, empty_allowlist):
    assert analyze_dir(clean_run) == []
    assert (
        cli.main(
            [
                "gate",
                "--findings",
                str(clean_run / "findings.csv"),
                "--allowlist",
                str(empty_allowlist),
            ]
        )
        == 0
    )


def test_injected_leak_is_found_and_fails_gate(leak_run, empty_allowlist, tmp_path):
    rows = analyze_dir(leak_run)
    assert [
        (
            r["component"],
            r["stage"],
            r["protocol"],
            r["destination"],
            r["port"],
            r["attempts"],
            r["left_node"],
        )
        for r in rows
    ] == [
        ("rag", "idle", "dns", "leak.example.test", 53, 2, 0),
        # 첫 SYN은 eth0 사본이 있어 밖으로 나갔고, 1초 뒤 재전송은 정책에 막혀 사본이 없다.
        ("rag", "idle", "tcp", "leak.example.test", 443, 2, 1),
    ]
    # NAT된 사본(eth0 Out)과 CoreDNS의 상위 리졸버 질의는 별도 시도로 세지 않는다.
    assert not any(r["component"] in ("node", "kube-system/kube-dns") for r in rows)
    out = tmp_path / "gate.txt"
    code = cli.main(
        [
            "gate",
            "--findings",
            str(leak_run / "findings.csv"),
            "--allowlist",
            str(empty_allowlist),
            "--out",
            str(out),
        ]
    )
    assert code == 1
    assert out.read_text().startswith("FAIL: 허용 목록 밖의 외부 호출 시도 2건")


def test_allowlist_can_accept_a_known_destination(leak_run, tmp_path):
    analyze_dir(leak_run)
    allow = tmp_path / "allow.toml"
    allow.write_text(
        '[[allow]]\ncomponent = "rag"\ndestination = "leak.example.test"\nreason = "test"\n'
    )
    rows = gate.read_findings(leak_run / "findings.csv")
    assert gate.check(rows, gate.load_allowlist(allow)) == []
    allow.write_text('[[allow]]\ncomponent = "rag"\ndestination = "leak.example.test"\nport = 53\n')
    assert [r["protocol"] for r in gate.check(rows, gate.load_allowlist(allow))] == ["tcp"]


def test_node_originated_connection_is_attributed_to_node():
    ctx = Context.from_meta(
        {
            "kube_dns_ip": "10.96.0.10",
            "pod_cidr": "10.244.0.0/16",
            "service_cidr": "10.96.0.0/16",
            "node_ips": ["172.18.0.2"],
            "namespace": "rag",
        }
    )
    packets = parse(
        [
            "1005.0 eth0  Out IP 172.18.0.2.33333 > 140.82.112.3.443: Flags [S], seq 777, win 1",
        ]
    )
    rows = findings(packets, ctx, STAGES)
    assert [(r["component"], r["stage"], r["destination"], r["port"]) for r in rows] == [
        ("node", "install", "140.82.112.3", 443)
    ]


def test_matrix_counts_distinct_destinations():
    rows = [
        {
            "component": "qdrant",
            "stage": "startup",
            "protocol": "dns",
            "destination": "a",
            "port": 53,
        },
        {
            "component": "qdrant",
            "stage": "startup",
            "protocol": "tcp",
            "destination": "a",
            "port": 443,
        },
        {
            "component": "qdrant",
            "stage": "idle",
            "protocol": "tcp",
            "destination": "a",
            "port": 443,
        },
    ]
    m = matrix(rows, ["llm", "qdrant"])
    assert m["qdrant"] == {"install": 0, "startup": 2, "first_request": 0, "idle": 1}
    assert m["llm"] == {"install": 0, "startup": 0, "first_request": 0, "idle": 0}


def test_report_builds_from_runs(clean_run, tmp_path):
    analyze_dir(clean_run)
    out = tmp_path / "report"
    assert cli.main(["report", "--run", f"before={clean_run}", "--out", str(out)]) == 0
    text = (out / "REPORT.md").read_text(encoding="utf-8")
    assert "## before" in text and "외부 목적지로 향한 시도가 관찰되지 않았다" in text
    assert (out / "before.png").stat().st_size > 0
    assert (out / "before" / "capture.txt").exists()
