"""牙牌神数 · 确定性占算引擎（Python 参考实现）

规则依据：《牙牌神数》开数九格 + 占法五等品阶（西川斋整理本）
实现要点：
  1. 32 张牙牌洗乱后一字排开，取 30 个相邻三张的滑动窗口（牌序 0..31）。
  2. 每个窗口只看“最高一开”：不同6 > 五子5 > 合巧4 > 分相/马军/对子/么二三/二三靠3 > 正快1，互不叠加。
  3. 30 个窗口开数相加为该次“总开”。0 开须重占（原书【求占秘要】）。
  4. 连排三次（始/中/末）合成一数，共 125 数。

品阶判定两套口径：
  - "book"       ：原书阈值 12 / 10 / 8 / 5 / 1
  - "calibrated" ：保持开数算法不变，用分位数标定品阶边界，
                   使单次品阶概率与《牙牌.txt》给出的目标分布一致
                   （下下6% 中下25.9% 中平33.4% 上中22.2% 上上12.5%）

纯标准库，无第三方依赖；带随机源注入，便于可复现测试。
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- #
# 一、牌库：文牌 11 种×2（22 张）+ 武牌 10 张 = 32 张
# --------------------------------------------------------------------------- #
DECK_TILES = [
    # 文牌（成对）
    ("天牌", 6, 6), ("天牌", 6, 6),
    ("地牌", 1, 1), ("地牌", 1, 1),
    ("人牌", 4, 4), ("人牌", 4, 4),
    ("和牌", 1, 3), ("和牌", 1, 3),
    ("梅花", 5, 5), ("梅花", 5, 5),
    ("长三", 3, 3), ("长三", 3, 3),
    ("板凳", 2, 2), ("板凳", 2, 2),
    ("斧头", 5, 6), ("斧头", 5, 6),
    ("红头十", 4, 6), ("红头十", 4, 6),
    ("高脚七", 1, 6), ("高脚七", 1, 6),
    ("铜锤六", 1, 5), ("铜锤六", 1, 5),
    # 武牌（各一张）
    ("九点", 3, 6), ("九点", 4, 5),
    ("八点", 2, 6), ("八点", 3, 5),
    ("七点", 2, 5), ("七点", 3, 4),
    ("二四", 2, 4),
    ("五点", 2, 3), ("五点", 1, 4),
    ("么二", 1, 2),
]

TIERS_TOP_DOWN = ("上上", "上中", "中平", "中下", "下下")

# 开数九格（分值 → 名目），同一窗口取最高分
PATTERN_VALUES = {
    "不同": 6,
    "五子": 5,
    "合巧": 4,
    "分相": 3,
    "马军": 3,
    "对子": 3,
    "么二三": 3,
    "二三靠": 3,
    "正快": 1,
    "无开": 0,
}

# 原书阈值（上界含）：total >= 12 上上 ...
BOOK_THRESHOLDS = {"上上": 12, "上中": 10, "中平": 8, "中下": 5, "下下": 1}

# 标定阈值（由 tests/calibrate.py 以 400 万次模拟分位数求得，见 data/calibration.json）
CALIBRATED_THRESHOLDS = {"上上": 21, "上中": 15, "中平": 9, "中下": 3, "下下": 0}

GRADE_MODES = {
    "book": BOOK_THRESHOLDS,
    "calibrated": CALIBRATED_THRESHOLDS,
}

# 品阶 → 吉凶倾向（供“始中末”解读使用）
TIER_NATURE = {
    "上上": {"index": 0, "tone": "大吉", "score": 4},
    "上中": {"index": 1, "tone": "吉", "score": 3},
    "中平": {"index": 2, "tone": "平", "score": 2},
    "中下": {"index": 3, "tone": "滞", "score": 1},
    "下下": {"index": 4, "tone": "凶", "score": 0},
}


@dataclass(frozen=True)
class Tile:
    index: int      # 在 32 张牌库中的位置
    name: str
    top: int
    bottom: int

    @property
    def faces(self) -> tuple[int, int]:
        return (self.top, self.bottom)

    @property
    def points(self) -> int:
        return self.top + self.bottom

    @property
    def ident(self) -> tuple[str, int, int]:
        """牌的同一性：两张同名同点的牌才算“对子”（不看牌库下标）。"""
        return (self.name, self.top, self.bottom)

    def as_dict(self) -> dict:
        return {"index": self.index, "name": self.name, "top": self.top, "bottom": self.bottom}


DECK: list[Tile] = [Tile(i, n, a, b) for i, (n, a, b) in enumerate(DECK_TILES)]


# --------------------------------------------------------------------------- #
# 二、开数判定
# --------------------------------------------------------------------------- #
def _faces_of(cards: Sequence[Tile]) -> list[int]:
    out: list[int] = []
    for c in cards:
        out.extend(c.faces)
    return out


def detect_window(cards: Sequence[Tile]) -> tuple[str, int]:
    """返回窗口内最高一开的 (名目, 分值)。互不叠加。"""
    assert len(cards) == 3
    faces = _faces_of(cards)
    cnt = Counter(faces)
    ordered = sorted(faces)

    # 1. 不同 6：六面恰为 1..6 各一
    if ordered == [1, 2, 3, 4, 5, 6]:
        return "不同", 6

    # 2. 五子 5：五面相通，余一面不同
    if sorted(cnt.values()) == [1, 5]:
        return "五子", 5

    # 3. 合巧 4：四面相同，余两面之和恰等于该点数
    for value, count in cnt.items():
        if count == 4:
            rest = list(faces)
            for _ in range(4):
                rest.remove(value)
            if sum(rest) == value:
                return "合巧", 4

    # 4~8. 并列三开，按书序取较高者（同分，取书写在前者）
    # 4. 分相：三面同、另三面亦同
    if sorted(cnt.values()) == [3, 3] and len(cnt) == 2:
        return "分相", 3
    # 5. 马军：双四、双五、双六
    if ordered == [4, 4, 5, 5, 6, 6]:
        return "马军", 3
    # 6. 对子：窗口内两张完全相同的牌
    idents = [c.ident for c in cards]
    if idents[0] == idents[1] or idents[1] == idents[2] or idents[0] == idents[2]:
        return "对子", 3
    # 7. 么二三：双么、双二、双三
    if ordered == [1, 1, 2, 2, 3, 3]:
        return "么二三", 3
    # 8. 二三靠：双二、双三、双六
    if ordered == [2, 2, 3, 3, 6, 6]:
        return "二三靠", 3

    # 9. 正快 1：三面相同，余三面之和 >= 14
    for value, count in cnt.items():
        if count == 3:
            rest = list(faces)
            for _ in range(3):
                rest.remove(value)
            if sum(rest) >= 14:
                return "正快", 1

    return "无开", 0


# --------------------------------------------------------------------------- #
# 三、一次排牌（30 个滑动窗口）
# --------------------------------------------------------------------------- #
@dataclass
class Round:
    row: list[Tile]
    windows: list[dict] = field(default_factory=list)
    total: int = 0
    pattern_counts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "row": [t.as_dict() for t in self.row],
            "windows": self.windows,
            "total": self.total,
            "pattern_counts": self.pattern_counts,
        }


def shuffle_deck(rng: random.Random | None = None) -> list[Tile]:
    rng = rng or random
    row = list(DECK)
    rng.shuffle(row)
    return row


def play_round(rng: random.Random | None = None, row: Sequence[Tile] | None = None) -> Round:
    row = list(row) if row is not None else shuffle_deck(rng)
    result = Round(row=row)
    counts: Counter[str] = Counter()
    total = 0
    for start in range(len(row) - 2):          # 30 个窗口
        name, value = detect_window(row[start:start + 3])
        result.windows.append({
            "start": start,
            "tiles": [row[start].index, row[start + 1].index, row[start + 2].index],
            "pattern": name,
            "value": value,
        })
        counts[name] += 1
        total += value
    result.total = total
    result.pattern_counts = dict(counts)
    return result


def grade(total: int, mode: str = "calibrated") -> str:
    if mode not in GRADE_MODES:
        raise ValueError(f"未知品阶口径: {mode}")
    th = GRADE_MODES[mode]
    for tier in TIERS_TOP_DOWN:
        if total >= th[tier]:
            return tier
    return "下下"


def one_valid_round(rng: random.Random | None = None, mode: str = "calibrated") -> dict:
    """排一次；总开为 0 时按原书须重占。"""
    attempts = 0
    while True:
        attempts += 1
        rnd = play_round(rng)
        if rnd.total > 0:
            tier = grade(rnd.total, mode)
            return {"round": rnd, "tier": tier, "rerolls": attempts - 1}


def draw_three(rng: random.Random | None = None, mode: str = "calibrated") -> dict:
    """连排三次，得 始/中/末 三品阶。"""
    labels = ("始", "中", "末")
    rounds = [one_valid_round(rng, mode) for _ in range(3)]
    tiers = [r["tier"] for r in rounds]
    return {
        "mode": mode,
        "labels": list(labels),
        "tiers": tiers,
        "pattern": " ".join(tiers),
        "rounds": rounds,
    }


# --------------------------------------------------------------------------- #
# 四、125 数签库
# --------------------------------------------------------------------------- #
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "yapai_125.json"


def load_oracle(path: Path = DATA_PATH) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {" ".join([e["initial"], e["middle"], e["final"]]): e for e in data}


def render_text(result: dict, oracle: dict[str, dict] | None = None) -> str:
    """把一次占算渲染成文本签面。"""
    oracle = oracle or load_oracle()
    entry = oracle.get(result["pattern"])
    lines = []
    lines.append(f"所问：{result.get('question') or '（未填写）'}")
    lines.append(f"品阶：始{result['tiers'][0]} · 中{result['tiers'][1]} · 末{result['tiers'][2]}")
    for label, rnd in zip(result["labels"], result["rounds"]):
        lines.append(f"  {label}数：{rnd['round'].total} 开 → {rnd['tier']}")
    if entry:
        lines.append("")
        lines.append(f"第 {entry['id']:03d} 数 · {entry['category']} · {entry['title']}")
        if entry.get("motto"):
            lines.append(f"【断意】{entry['motto']}")
        lines.append("【诗曰】" + "".join(entry["poem"]))
        lines.append("【解曰】" + entry["jie"])
        lines.append("【断曰】" + entry["duan"])
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="牙牌神数 · 命令行占算")
    parser.add_argument("-q", "--question", default="", help="心中所问之事")
    parser.add_argument("-s", "--seed", type=int, default=None, help="随机种子（可复现）")
    parser.add_argument("-m", "--mode", default="calibrated", choices=sorted(GRADE_MODES),
                        help="品阶口径：calibrated=标定分布 / book=原书阈值")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    rng = random.Random(args.seed)
    result = draw_three(rng, args.mode)
    result["question"] = args.question
    entry = load_oracle().get(result["pattern"])
    result["entry"] = entry

    if args.json:
        payload = {
            "question": args.question,
            "mode": args.mode,
            "tiers": result["tiers"],
            "pattern": result["pattern"],
            "totals": [r["round"].total for r in result["rounds"]],
            "rerolls": [r["rerolls"] for r in result["rounds"]],
            "entry_id": entry["id"] if entry else None,
            "rounds": [r["round"].as_dict() for r in result["rounds"]],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
