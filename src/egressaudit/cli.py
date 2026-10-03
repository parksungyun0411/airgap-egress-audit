"""egress-audit 명령.

analyze  실행 폴더의 캡처를 findings.csv로 바꾼다.
gate     findings.csv를 허용 목록과 비교해 새 외부 호출이 있으면 종료 코드 1로 끝난다.
report   여러 실행(before/after/leak)을 reports/<run-id>/REPORT.md로 묶는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from egressaudit import gate, report
from egressaudit.analyze import analyze_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="egress-audit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze")
    a.add_argument("--run-dir", type=Path, required=True)

    g = sub.add_parser("gate")
    g.add_argument("--findings", type=Path, required=True)
    g.add_argument("--allowlist", type=Path, default=Path("policy/allowlist.toml"))
    g.add_argument("--out", type=Path, help="결과 문구를 이 파일에도 쓴다")

    r = sub.add_parser("report")
    r.add_argument("--run", action="append", required=True, help="label=run_dir")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--title", default="airgap-egress-audit 결과")

    args = parser.parse_args(argv)
    if args.cmd == "analyze":
        rows = analyze_dir(args.run_dir)
        print(f"{len(rows)} rows -> {args.run_dir / 'findings.csv'}")
        return 0
    if args.cmd == "gate":
        violations = gate.check(
            gate.read_findings(args.findings), gate.load_allowlist(args.allowlist)
        )
        text = gate.render(violations)
        print(text)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        return 1 if violations else 0
    runs = []
    for item in args.run:
        label, _, path = item.partition("=")
        runs.append((label, Path(path)))
    print(report.build(runs, args.out, args.title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
