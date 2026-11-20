"""牙牌神数引擎验证：规则单测 + 概率标定 + 125 数覆盖 + JS/Python 一致性。

用法：
    python tests/verify.py                  # 默认 100 万次排牌
    python tests/verify.py --sims 200000 --draws 50000
    python tests/verify.py --no-node        # 跳过 JS 一致性检查

产出 data/calibration.json：实测分布 / 标定阈值 / 覆盖统计（前端读它显示“校准说明”）。
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import yapai_engine as E  # noqa: E402

TARGET = {"下下": 6.0, "中下": 25.9, "中平": 33.4, "上中": 22.2, "上上": 12.5}
TIERS = ["上上", "上中", "中平", "中下", "下下"]
BOTTOM_UP = ["下下", "中下", "中平", "上中", "上上"]


def tile(name: str, top: int, bottom: int) -> E.Tile:
    return E.Tile(-1, name, top, bottom)


# --------------------------------------------------------------------------- #
# 一、规则单测：开数九格逐格验证
# --------------------------------------------------------------------------- #
RULE_CASES = [
    ("不同", 6, [("高脚七", 1, 6), ("二四", 2, 4), ("八点", 3, 5)], "六面 1~6 各一"),
    ("五子", 5, [("天牌", 6, 6), ("天牌", 6, 6), ("高脚七", 1, 6)], "五个六点"),
    ("合巧", 4, [("人牌", 4, 4), ("人牌", 4, 4), ("和牌", 1, 3)], "四个四点 + 1+3"),
    ("分相", 3, [("长三", 3, 3), ("九点", 3, 6), ("天牌", 6, 6)], "三个三、三个六"),
    ("马军", 3, [("人牌", 4, 4), ("梅花", 5, 5), ("天牌", 6, 6)], "双四双五双六"),
    ("对子", 3, [("天牌", 6, 6), ("天牌", 6, 6), ("么二", 1, 2)], "两张天牌"),
    ("么二三", 3, [("地牌", 1, 1), ("板凳", 2, 2), ("长三", 3, 3)], "双么双二双三"),
    ("二三靠", 3, [("板凳", 2, 2), ("长三", 3, 3), ("天牌", 6, 6)], "双二双三双六"),
    ("正快", 1, [("人牌", 4, 4), ("七点", 3, 4), ("天牌", 6, 6)], "三个四，余 3+6+6=15"),
    ("无开", 0, [("和牌", 1, 3), ("九点", 3, 6), ("五点", 2, 3)], "三个三但余 1+6+2=9"),
]


def rule_unit_tests() -> list[str]:
    problems = []
    for want_name, want_value, cards, note in RULE_CASES:
        name, value = E.detect_window([tile(*c) for c in cards])
        if (name, value) != (want_name, want_value):
            problems.append(f"[规则] {note}: 期望 {want_name}({want_value})，实得 {name}({value})")
    # 牌同一性：牌库中两张天牌下标不同，仍须判为对子
    tian = [t for t in E.DECK if t.name == "天牌"]
    if E.detect_window(tian + [t for t in E.DECK if t.name == "么二"])[0] != "对子":
        problems.append("[规则] 对子未按“同名同点”判定")
    # 窗口数：32 张牌 → 30 个窗口
    rnd = E.play_round(random.Random(1))
    if len(rnd.windows) != 30 or len(rnd.row) != 32:
        problems.append(f"[规则] 窗口数 {len(rnd.windows)} / 牌数 {len(rnd.row)} 不符")
    # 总开 = 各窗开数之和
    if rnd.total != sum(w["value"] for w in rnd.windows):
        problems.append("[规则] 总开不等于各窗之和")
    return problems


# --------------------------------------------------------------------------- #
# 二、查表 + 快速模拟（numpy：按 21 种牌型建 21³ 表，滑动 30 窗）
# --------------------------------------------------------------------------- #
def build_table() -> tuple[np.ndarray, list[E.Tile], np.ndarray]:
    reps: list[E.Tile] = []
    seen = set()
    for t in E.DECK:
        if t.ident not in seen:
            seen.add(t.ident)
            reps.append(t)
    reps.sort(key=lambda t: (-t.points, t.name))
    n = len(reps)
    table = np.zeros((n, n, n), dtype=np.int8)
    for i in range(n):
        for j in range(n):
            for k in range(n):
                table[i, j, k] = E.detect_window([reps[i], reps[j], reps[k]])[1]
    pos = {t.ident: idx for idx, t in enumerate(reps)}
    deck_ids = np.array([pos[t.ident] for t in E.DECK])
    # 交叉验证：查表结果必须与逐张判定一致（随机 2 万组三张牌）
    rng = random.Random(11)
    for _ in range(20000):
        trio = rng.sample(E.DECK, 3)
        want = E.detect_window(trio)[1]
        got = table[deck_ids[trio[0].index], deck_ids[trio[1].index], deck_ids[trio[2].index]]
        if want != got:
            raise AssertionError(f"查表与逐张判定不一致: {trio} {want} vs {got}")
    if n != 21:
        raise AssertionError(f"牌型数应为 21，实得 {n}")
    return table, reps, deck_ids


def simulate(table: np.ndarray, deck_ids: np.ndarray, n_rows: int, seed: int = 20260101) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.empty(n_rows, dtype=np.int32)
    chunk = 200_000
    filled = 0
    while filled < n_rows:
        m = min(chunk, n_rows - filled)
        perm = np.argsort(rng.random((m, 32)), axis=1)
        seq = deck_ids[perm]
        total = np.zeros(m, dtype=np.int32)
        for w in range(30):
            total += table[seq[:, w], seq[:, w + 1], seq[:, w + 2]]
        out[filled:filled + m] = total
        filled += m
    return out


# --------------------------------------------------------------------------- #
# 三、标定：保持开数算法不动，用分位数把品阶边界对齐目标分布
# --------------------------------------------------------------------------- #
def thresholds_from(model: str, totals: np.ndarray) -> dict:
    """标定口径：开数算法一字不改，只用四个整数切点把品阶频率对齐目标分布。

    穷举切点组合（下下上界 / 中下上界 / 中平上界 / 上中上界），取最大偏差最小者。
    """
    if model == "book":
        return dict(E.BOOK_THRESHOLDS)
    cdf = np.bincount(totals, minlength=int(totals.max()) + 1).cumsum() / totals.size
    hi = int(np.searchsorted(cdf, 0.97)) + 1          # 上中上界的合理上限
    best = None
    for c1 in range(0, hi):                            # 下下上界
        p1 = cdf[c1] - 0.0
        for c2 in range(c1 + 1, hi + 1):               # 中下上界
            p2 = cdf[c2] - cdf[c1]
            for c3 in range(c2 + 1, hi + 2):           # 中平上界
                p3 = cdf[c3] - cdf[c2]
                for c4 in range(c3 + 1, hi + 3):       # 上中上界
                    p4 = cdf[c4] - cdf[c3]
                    p5 = 1.0 - cdf[c4]
                    dev = max(abs(p1 * 100 - TARGET["下下"]), abs(p2 * 100 - TARGET["中下"]),
                              abs(p3 * 100 - TARGET["中平"]), abs(p4 * 100 - TARGET["上中"]),
                              abs(p5 * 100 - TARGET["上上"]))
                    if best is None or dev < best[0]:
                        best = (dev, c1, c2, c3, c4)
    _, c1, c2, c3, c4 = best
    return {"下下": 0, "中下": c1 + 1, "中平": c2 + 1, "上中": c3 + 1, "上上": c4 + 1}


def dist(totals: np.ndarray, th: dict) -> dict:
    def g(x: int) -> str:
        for tier in TIERS:
            if x >= th[tier]:
                return tier
        return "下下"

    counts = Counter(g(int(x)) for x in totals)
    return {t: round(counts.get(t, 0) / totals.size * 100, 2) for t in TIERS}


# --------------------------------------------------------------------------- #
# 四、Python/JS 一致性
# --------------------------------------------------------------------------- #
PARITY_JS = """const fs=require('fs');const E=require(process.argv[2]);
const input=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const out=input.map(x=>E.grade(x,process.argv[4]));
process.stdout.write(JSON.stringify(out));
"""


def js_parity(totals: np.ndarray) -> list[str]:
    problems: list[str] = []
    script = ROOT / "tests" / "_parity_tmp.js"
    data_tmp = ROOT / "tests" / "_parity_tmp.json"
    script.write_text(PARITY_JS, encoding="utf-8")
    sample = [int(x) for x in np.unique(totals)][:400]
    data_tmp.write_text(json.dumps(sample), encoding="utf-8")
    engine = str(ROOT / "engine" / "yapai_engine.js")
    for mode in ("calibrated", "book"):
        py = [E.grade(x, mode) for x in sample]
        res = subprocess.run(["node", str(script), engine, str(data_tmp), mode],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            problems.append(f"[JS] 执行失败({mode}): {res.stderr.strip()[:300]}")
            continue
        got = json.loads(res.stdout)
        if got != py:
            bad = [(x, a, b) for x, a, b in zip(sample, py, got) if a != b][:5]
            problems.append(f"[JS] 品阶口径不一致({mode}): {bad}")
    # 开数判定一致性：21³ 组合全量比对
    reps: list[E.Tile] = []
    seen = set()
    for t in E.DECK:
        if t.ident not in seen:
            seen.add(t.ident)
            reps.append(t)
    cases = [[[r.name, r.top, r.bottom] for r in (reps[i], reps[j], reps[k])]
             for i in range(len(reps)) for j in range(len(reps)) for k in range(len(reps))]
    py_vals = [E.detect_window([tile(*c) for c in trio])[1] for trio in cases]
    js_check = """const fs=require('fs');const E=require(process.argv[2]);
const cases=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const out=cases.map(t=>E.detectWindow(t.map(c=>({name:c[0],top:c[1],bottom:c[2],key:c[0]+c[1]+'-'+c[2]}))).value);
process.stdout.write(JSON.stringify(out));
"""
    script.write_text(js_check, encoding="utf-8")
    data_tmp.write_text(json.dumps(cases), encoding="utf-8")
    res = subprocess.run(["node", str(script), engine, str(data_tmp)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        problems.append(f"[JS] 开数比对失败: {res.stderr.strip()[:300]}")
    elif json.loads(res.stdout) != py_vals:
        diff = [(cases[i], py_vals[i], json.loads(res.stdout)[i])
                for i in range(len(cases)) if py_vals[i] != json.loads(res.stdout)[i]][:5]
        problems.append(f"[JS] 开数判定不一致: {diff}")
    script.unlink(missing_ok=True)
    data_tmp.unlink(missing_ok=True)
    return problems


# --------------------------------------------------------------------------- #
# 五、主流程
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=1_000_000, help="排牌模拟次数")
    ap.add_argument("--draws", type=int, default=100_000, help="三连排抽样次数（覆盖 125 数）")
    ap.add_argument("--no-node", action="store_true")
    args = ap.parse_args(argv)

    started = time.time()
    failures: list[str] = []

    failures += rule_unit_tests()

    table, reps, deck_ids = build_table()
    totals = simulate(table, deck_ids, args.sims)

    calibrated = thresholds_from("calibrated", totals)
    E.CALIBRATED_THRESHOLDS.update(calibrated)
    book = dict(E.BOOK_THRESHOLDS)

    d_cal = dist(totals, calibrated)
    d_book = dist(totals, book)

    print("=" * 80)
    print(f"牙牌神数 · 引擎验证    模拟 {args.sims:,} 次排牌    {args.sims / (time.time() - started):,.0f} 行/秒")
    print("=" * 80)
    print(f"总开分布：均值 {totals.mean():.2f}  标准差 {totals.std():.2f}  "
          f"最小 {totals.min()}  最大 {totals.max()}  0开占比 {np.mean(totals == 0) * 100:.2f}%")
    print()
    print("目标分布（牙牌.txt）  :", "  ".join(f"{t} {TARGET[t]:5.1f}%" for t in TIERS))
    print("标定口径实测          :", "  ".join(f"{t} {d_cal[t]:5.2f}%" for t in TIERS))
    print("原书阈值实测          :", "  ".join(f"{t} {d_book[t]:5.2f}%" for t in TIERS))
    print("标定阈值  上上≥%d 上中≥%d 中平≥%d 中下≥%d 下下≥%d" %
          (calibrated["上上"], calibrated["上中"], calibrated["中平"], calibrated["中下"], calibrated["下下"]))
    max_dev = max(abs(d_cal[t] - TARGET[t]) for t in TIERS)
    print(f"标定最大偏差 {max_dev:.2f} 个百分点", "✓" if max_dev < 2.5 else "✗ 超差")
    if max_dev >= 2.5:
        failures.append(f"[标定] 分布偏差 {max_dev:.2f}pp ≥ 2.5pp")

    # 125 数覆盖
    rng = random.Random(7)
    combos: Counter[str] = Counter()
    for _ in range(args.draws):
        tiers = []
        for _ in range(3):
            while True:
                r = E.play_round(rng)
                if r.total > 0:
                    tiers.append(E.grade(r.total, "calibrated"))
                    break
        combos[" ".join(tiers)] += 1
    oracle = E.load_oracle()
    missing = [k for k in oracle if k not in combos]
    pmin = min(combos.values()) / args.draws * 100
    print()
    print(f"三连排抽样 {args.draws:,} 次：命中 {len(combos)}/125 数，最少组合概率 {pmin:.3f}%")
    print("  ✓ 125 数全部可达" if not missing else f"  ⚠ 未覆盖：{missing[:6]}（样本量不足，非致命）")

    # 可复现性
    a = E.draw_three(random.Random(42))
    b = E.draw_three(random.Random(42))
    same = [x["round"].total for x in a["rounds"]] == [x["round"].total for x in b["rounds"]]
    print(f"同种子可复现：{'✓' if same else '✗'}")
    if not same:
        failures.append("[复现] 同种子结果不一致")

    parity: list[str] = []
    if not args.no_node:
        parity = js_parity(totals)
        failures += parity
        print(f"Python/JS 口径一致：{'✓（品阶 + 21³ 开数全量比对）' if not parity else '✗'}")

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sims": args.sims,
        "mean_total": round(float(totals.mean()), 3),
        "sd_total": round(float(totals.std()), 3),
        "min_total": int(totals.min()),
        "max_total": int(totals.max()),
        "zero_rate": round(float(np.mean(totals == 0)) * 100, 3),
        "target": TARGET,
        "measured_calibrated": d_cal,
        "measured_book": d_book,
        "thresholds_calibrated": calibrated,
        "thresholds_book": book,
        "max_deviation_pp": round(max_dev, 3),
        "draws": args.draws,
        "combos_seen": len(combos),
        "rarest_combo_pct": round(pmin, 4),
        "elapsed_sec": round(time.time() - started, 1),
        "failures": failures,
    }
    out = ROOT / "data" / "calibration.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已写出 {out.relative_to(ROOT)}")
    print("验证结果：", "✓ 全部通过" if not failures else f"✗ {len(failures)} 项未通过")
    for f in failures:
        print("   - " + f)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
