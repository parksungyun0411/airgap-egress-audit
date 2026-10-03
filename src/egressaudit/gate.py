"""회귀 게이트: 허용 목록에 없는 외부 호출이 하나라도 있으면 실패한다.

허용 목록(policy/allowlist.toml)이 비어 있으면 '외부 호출 0건'을 요구하는 것과 같다.
"""

from __future__ import annotations

import csv
import fnmatch
import tomllib
from pathlib import Path


def load_allowlist(path: Path) -> list[dict]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data.get("allow", [])


def allowed(row: dict, rules: list[dict]) -> bool:
    for rule in rules:
        if not fnmatch.fnmatch(row["component"], rule.get("component", "*")):
            continue
        if not fnmatch.fnmatch(row["destination"], rule.get("destination", "*")):
            continue
        if "port" in rule and int(rule["port"]) != int(row["port"]):
            continue
        return True
    return False


def check(rows: list[dict], rules: list[dict]) -> list[dict]:
    """허용되지 않은 행만 돌려준다."""
    return [r for r in rows if not allowed(r, rules)]


def read_findings(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def render(violations: list[dict]) -> str:
    if not violations:
        return "PASS: 허용 목록 밖의 외부 호출 시도 0건"
    lines = [f"FAIL: 허용 목록 밖의 외부 호출 시도 {len(violations)}건"]
    for r in violations:
        lines.append(
            f"  {r['component']} [{r['stage']}] {r['protocol']} {r['destination']}:{r['port']}"
            f" x{r['attempts']}"
        )
    return "\n".join(lines)
