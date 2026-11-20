"""从 corpus/《牙牌神数》整理本 Markdown 抽取 125 数签库，并与旧 JSON 交叉校验。

用法：
    python tools/extract_data.py            # 抽取 + 校验，写出 data/yapai_125.json
    python tools/extract_data.py --check    # 只校验，不写
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "corpus" / "《牙牌神数》简体横排-西川斋整理.md"
OUT = ROOT / "data" / "yapai_125.json"
LEGACY = ROOT / "_legacy" / "yapai_shenshu_data.json"

TIERS = ("上上", "上中", "中平", "中下", "下下")
HEAD_RE = re.compile(r"^### 第\s*(\d{3})\s*数[：:]\s*(上上|上中|中平|中下|下下)\s+(上上|上中|中平|中下|下下)\s+(上上|上中|中平|中下|下下)\s*$")
MOTTO_RE = re.compile(r"^>\s*\*\*【断意】\s*(.+?)\*\*\s*$")
JIE_RE = re.compile(r"\*\*【解曰】\*\*\s*(.*)$")
DUAN_RE = re.compile(r"\*\*【断曰】\*\*\s*(.*)$")
POEM_RE = re.compile(r"^\*\*诗曰\*\*[：:]?\s*(.*)$")
NOISE_RE = re.compile(r"^(<!--.*-->|---+|>+\s*)$")


def split_poem(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[。！？；])", text) if p.strip()]
    return parts or [text]


def parse_md(path: Path = MD) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[dict] = []
    cur: dict | None = None
    section = None

    def flush() -> None:
        nonlocal cur
        if cur is None:
            return
        if not cur["poem"] or not cur["jie"] or not cur["duan"]:
            missing = [k for k in ("poem", "jie", "duan") if not cur[k]]
            raise ValueError(f"第 {cur['id']:03d} 数缺少字段: {missing}")
        entries.append(cur)
        cur = None

    for raw in lines:
        line = raw.rstrip()
        m = HEAD_RE.match(line)
        if m:
            flush()
            num, a, b, c = m.groups()
            cur = {
                "id": int(num),
                "initial": a,
                "middle": b,
                "final": c,
                "title": f"{a} {b} {c}",
                "category": f"{a}数",
                "motto": "",
                "poem": [],
                "poem_raw": "",
                "jie": "",
                "duan": "",
            }
            section = None
            continue
        if cur is None:
            continue
        if NOISE_RE.match(line.strip()):
            continue
        stripped = line.strip()
        m = MOTTO_RE.match(stripped)
        if m:
            cur["motto"] = m.group(1).strip()
            section = None
            continue
        m = POEM_RE.match(stripped)
        if m:
            section = "poem"
            rest = m.group(1).strip()
            if rest:
                cur["poem"].extend(split_poem(rest))
            continue
        m = JIE_RE.search(stripped)
        if m:
            section = "jie"
            cur["jie"] = m.group(1).strip()
            continue
        m = DUAN_RE.search(stripped)
        if m:
            section = "duan"
            cur["duan"] = m.group(1).strip()
            continue
        if stripped.startswith("#"):
            section = None
            continue
        if section == "poem":
            cur["poem"].extend(split_poem(stripped.lstrip("> ").strip()))
        elif section in ("jie", "duan"):
            cur[section] = (cur[section] + stripped.lstrip("> ").strip()).strip()

    flush()
    for e in entries:
        e["poem"] = [p for p in e["poem"] if p]
        e["poem_raw"] = "".join(e["poem"])
    return entries


def validate(entries: list[dict]) -> list[str]:
    problems: list[str] = []
    if len(entries) != 125:
        problems.append(f"条目数 {len(entries)} != 125")
    if [e["id"] for e in entries] != list(range(1, 126)):
        problems.append("id 非 1..125 连续")
    combos = {f"{a} {b} {c}" for a in TIERS for b in TIERS for c in TIERS}
    have = {e["title"] for e in entries}
    if have != combos:
        problems.append(f"品阶组合缺失 {sorted(combos - have)} 多余 {sorted(have - combos)}")
    idx = {e["title"]: e for e in entries}
    for a in TIERS:                      # 卷次结构：1-25 始上上，26-50 始上中 ……
        block = [idx[f"{a} {b} {c}"]["id"] for b in TIERS for c in TIERS]
        start = 1 + TIERS.index(a) * 25
        if sorted(block) != list(range(start, start + 25)):
            problems.append(f"{a}数卷 id 区间不连续")
    for e in entries:
        if not e["poem"] or not e["jie"] or not e["duan"]:
            problems.append(f"第 {e['id']:03d} 数有空字段")
        if e["poem_raw"] and len(e["poem_raw"]) < 12:
            problems.append(f"第 {e['id']:03d} 数诗曰过短: {e['poem_raw']}")
    return problems


def cross_check(entries: list[dict], legacy_path: Path = LEGACY) -> list[str]:
    if not legacy_path.exists():
        return [f"（跳过）未找到旧数据 {legacy_path.name}"]
    legacy = {e["title"]: e for e in json.loads(legacy_path.read_text(encoding="utf-8"))}
    diffs: list[str] = []
    for e in entries:
        old = legacy.get(e["title"])
        if old is None:
            diffs.append(f"第 {e['id']:03d} 数旧数据缺失")
            continue
        for field in ("jie", "duan"):
            if old.get(field, "").strip() != e[field].strip():
                diffs.append(f"第 {e['id']:03d} 数 {field} 与旧数据不一致")
        if re.sub(r"\s", "", old.get("poem_raw", "")) != re.sub(r"\s", "", e["poem_raw"]):
            diffs.append(f"第 {e['id']:03d} 数诗曰与旧数据不一致")
        if (old.get("motto") or "").strip() != e["motto"]:
            diffs.append(f"第 {e['id']:03d} 数断意不一致 sql={old.get('motto')!r}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv
    entries = parse_md()
    problems = validate(entries)
    diffs = cross_check(entries)
    print(f"解析条目: {len(entries)}")
    for p in problems:
        print("  [结构] " + p)
    for d in diffs[:20]:
        print("  [对照] " + d)
    if len(diffs) > 20:
        print(f"  [对照] ……其余 {len(diffs) - 20} 条省略")
    if problems:
        print("结构校验未通过，已中止写出。")
        return 1
    if not check_only:
        OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写出 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
