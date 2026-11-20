"""把模板 + 引擎 + 签库 + 校准数据打包成单文件网页。

用法：
    python tools/build.py            # 产出 index.html（双击即用，也是 GitHub Pages 入口）
    python tools/build.py --check    # 只做体积与占位符检查
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "src" / "index.template.html"
ENGINE = ROOT / "engine" / "yapai_engine.js"
DATA = ROOT / "data" / "yapai_125.json"
CALIB = ROOT / "data" / "calibration.json"
OUT = ROOT / "index.html"


def build(check_only: bool = False) -> int:
    template = TEMPLATE.read_text(encoding="utf-8")
    engine = ENGINE.read_text(encoding="utf-8")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    calib = json.loads(CALIB.read_text(encoding="utf-8"))

    if len(data) != 125:
        print(f"✗ 签库条目数 {len(data)} ≠ 125")
        return 1
    for marker in ("{{ENGINE_JS}}", "{{DATA_JSON}}", "{{CALIBRATION_JSON}}"):
        if marker not in template:
            print(f"✗ 模板缺少占位符 {marker}")
            return 1

    html = (template
            .replace("{{ENGINE_JS}}", engine)
            .replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False))
            .replace("{{CALIBRATION_JSON}}", json.dumps(calib, ensure_ascii=False)))

    if "{{" in html:
        left = html[html.index("{{"):html.index("{{") + 40]
        print(f"✗ 仍有未替换占位符: {left}")
        return 1

    size_kb = len(html.encode("utf-8")) / 1024
    print(f"模板 {len(template) / 1024:.1f} KB + 引擎 {len(engine) / 1024:.1f} KB "
          f"+ 签库 {len(json.dumps(data, ensure_ascii=False)) / 1024:.1f} KB → {size_kb:.1f} KB")
    if check_only:
        return 0
    OUT.write_text(html, encoding="utf-8")
    print(f"✓ 已生成 {OUT.relative_to(ROOT)}（单文件、离线可用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(build("--check" in sys.argv))
