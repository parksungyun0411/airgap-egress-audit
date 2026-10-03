"""감사 결과를 표와 그림으로 만든다. cpu-sllm-k8s의 리포트 생성기(표·히트맵)를 옮겨 왔다.

  egress-audit report --run before=results/<id>/before --run after=results/<id>/after \
      --out reports/<id>
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from egressaudit.analyze import STAGES, matrix
from egressaudit.gate import read_findings

INK = "#1f2933"
MUTED = "#616e7c"
COMPONENTS = ["llm", "embed", "qdrant", "rag", "node"]
# reports/에 같이 옮기는 원자료
RAW_FILES = [
    "capture.txt",
    "coredns.log",
    "pods.jsonl",
    "stages.json",
    "meta.json",
    "versions.txt",
    "images.txt",
    "policy-check.txt",
    "values.yaml",
    "first-request.json",
    "gate.txt",
    "findings.csv",
    "kind-cluster.yaml",
    "log-llm.txt",
    "log-embed.txt",
    "log-qdrant.txt",
    "log-rag.txt",
]


def markdown_table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def heatmap(m: dict[str, dict[str, int]], title: str, path: Path) -> None:
    """한 색상(Blues) 순차 척도. 칸마다 값을 같이 적어 색에만 의존하지 않는다."""
    comps = list(m)
    values = [[m[c][s] for s in STAGES] for c in comps]
    vmax = max([v for row in values for v in row] + [1])
    fig, ax = plt.subplots(figsize=(1.5 * len(STAGES) + 2.2, 0.6 * len(comps) + 1.6))
    ax.imshow(values, cmap="Blues", aspect="auto", vmin=0, vmax=vmax)
    ax.set_xticks(range(len(STAGES)), STAGES)
    ax.set_yticks(range(len(comps)), comps)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    for i, row in enumerate(values):
        for j, v in enumerate(row):
            ax.text(
                j,
                i,
                str(v),
                ha="center",
                va="center",
                fontsize=10,
                color="white" if v > vmax * 0.6 else INK,
            )
    ax.tick_params(colors=MUTED, labelsize=9)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_section(label: str, run_dir: Path, out: Path) -> list[str]:
    rows = read_findings(run_dir / "findings.csv")
    m = matrix(rows, COMPONENTS)
    heatmap(m, f"external call attempts: {label} (distinct destinations)", out / f"{label}.png")
    gate = (
        (run_dir / "gate.txt").read_text(encoding="utf-8").strip()
        if (run_dir / "gate.txt").exists()
        else "(게이트 미실행)"
    )
    stages = json.loads((run_dir / "stages.json").read_text())
    idle = round(stages["idle_end"] - stages["first_request_end"])
    to_running = round(stages["pods_running"] - stages["install_start"])
    to_ready = round(stages["pods_ready"] - stages["pods_running"])
    lines = [
        f"## {label}",
        "",
        (
            f"원자료: [`{label}/`]({label}/) · 설치→Running {to_running}초"
            f" · Running→Ready {to_ready}초 · 유휴 관찰 {idle}초"
        ),
        "",
        f"![{label}]({label}.png)",
        "",
        "구성 요소 × 단계별 서로 다른 외부 목적지 수:",
        "",
        markdown_table(["구성 요소", *STAGES], [[c, *[m[c][s] for s in STAGES]] for c in m]),
        "",
    ]
    if rows:
        lines += [
            "시도 목록:",
            "",
            markdown_table(
                [
                    "구성 요소",
                    "단계",
                    "프로토콜",
                    "목적지",
                    "IP",
                    "포트",
                    "시도 수",
                    "처음 본 시각(초)",
                ],
                [
                    [
                        r["component"],
                        r["stage"],
                        r["protocol"],
                        r["destination"],
                        r["ip"],
                        r["port"],
                        r["attempts"],
                        r["left_node"],
                        r["first_seen_s"],
                    ]
                    for r in rows
                ],
            ),
            "",
        ]
    else:
        lines += ["외부 목적지로 향한 시도가 관찰되지 않았다.", ""]
    lines += ["게이트:", "", "```text", gate, "```", ""]
    for name in RAW_FILES:
        src = run_dir / name
        if src.exists():
            (out / label).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / label / name)
    return lines


def build(runs: list[tuple[str, Path]], out: Path, title: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    for label, run_dir in runs:
        lines += run_section(label, run_dir, out)
    report = out / "REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
