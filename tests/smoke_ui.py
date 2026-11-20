"""无头浏览器冒烟测试：加载单文件页面 → 起占 → 校验签面 → 抽查交互。

用法：
    python tests/smoke_ui.py                # 起占一局并截图
    python tests/smoke_ui.py --headed       # 显示窗口
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
SHOT_DIR = ROOT / "tests" / "shots"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    SHOT_DIR.mkdir(exist_ok=True)
    problems: list[str] = []
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(PAGE.as_uri())
        page.wait_for_timeout(400)
        if not page.title().startswith("牙牌神数"):
            problems.append("标题异常: " + page.title())
        tiles_before = page.locator("#deck .tile").count()
        if tiles_before:
            problems.append("初始即出现牌面（应为空）")

        # 起占（先看完整动画，截排牌与扫描过程）
        page.fill("#question", "今年换工作可否？")
        page.uncheck("#fast")
        page.click("#btn-cast")
        page.wait_for_timeout(1500)
        if page.locator("#deck .tile").count() != 32:
            problems.append(f"排牌张数 {page.locator('#deck .tile').count()} ≠ 32")
        page.screenshot(path=str(SHOT_DIR / "00-cast.png"), full_page=True)
        page.wait_for_selector("#panel-oracle.active", timeout=30000)
        page.wait_for_timeout(300)

        tiers = page.locator("#res-trio .stamp").all_inner_texts()
        if len(tiers) != 3 or any(t not in ("上上", "上中", "中平", "中下", "下下") for t in tiers):
            problems.append(f"三品阶异常: {tiers}")
        no_text = page.inner_text("#res-no")
        if not re.search(r"第 \d{3} 数", no_text):
            problems.append("签号异常: " + no_text)
        poem = page.inner_text("#res-poem")
        if len(poem.strip()) < 8:
            problems.append("诗曰为空")
        jie = page.inner_text("#res-jie")
        if len(jie.strip()) < 8:
            problems.append("解曰为空")
        narr = page.inner_text("#res-narr")
        for k in ("始", "中", "末"):
            if k not in narr:
                problems.append(f"解读缺少 {k}")
        prompt = page.inner_text("#res-prompt")
        if "【所问】今年换工作可否？" not in prompt:
            problems.append("触机提示词未带所问")
        open_detail = page.inner_text("#res-open")
        if "开" not in open_detail:
            problems.append("开数明细缺失")
        page.screenshot(path=str(SHOT_DIR / "01-oracle.png"), full_page=True)

        # 存档
        page.click("#btn-save")
        page.wait_for_timeout(200)
        page.click("#tabs button[data-tab=hist]")
        page.wait_for_timeout(200)
        if page.locator("#hist-list .hist-item").count() != 1:
            problems.append("存案未写入")
        page.screenshot(path=str(SHOT_DIR / "02-history.png"), full_page=True)
        # 从案卷回看
        page.locator("#hist-list .hist-item button[data-h]").first.click()
        page.wait_for_selector("#panel-oracle.active", timeout=5000)
        page.wait_for_timeout(200)
        if "第" not in page.inner_text("#res-no"):
            problems.append("案卷回看失败")
        if "案卷" not in page.inner_text("#res-open"):
            problems.append("案卷回看未提示缺少逐窗明细")

        # 签库：搜索 + 点选
        page.click("#tabs button[data-tab=dict]")
        page.wait_for_timeout(200)
        page.fill("#dict-search", "七十二战")
        page.wait_for_timeout(200)
        items = page.locator("#dict-list .dict-item").count()
        if items < 1:
            problems.append("签库搜索「七十二战」无结果")
        else:
            page.locator("#dict-list .dict-item").first.click()
            page.wait_for_selector("#panel-oracle.active", timeout=5000)
            if "第 005 数" not in page.inner_text("#res-no"):
                problems.append("签库点选未定位第 005 数: " + page.inner_text("#res-no"))
        page.click("#tabs button[data-tab=dict]")
        page.fill("#dict-search", "")
        page.wait_for_timeout(150)
        if page.locator("#dict-list .dict-item").count() != 125:
            problems.append(f"签库总数异常: {page.locator('#dict-list .dict-item').count()}")
        page.screenshot(path=str(SHOT_DIR / "03-dict.png"), full_page=True)

        # 原理页：阈值表与校验说明
        page.click("#tabs button[data-tab=rule]")
        page.wait_for_timeout(200)
        rule_rows = page.locator("#rule-table tr").count()
        if rule_rows != 9:
            problems.append(f"开数九格行数 {rule_rows} ≠ 9")
        if "标定阈值" not in page.inner_text("#cal-note"):
            problems.append("校准说明缺失")
        page.screenshot(path=str(SHOT_DIR / "04-rule.png"), full_page=True)

        # 原书阈值模式再占一次
        page.click("#tabs button[data-tab=cast]")
        page.check("input[name=mode][value=book]")
        page.click("#btn-cast")
        page.wait_for_selector("#panel-oracle.active", timeout=20000)
        page.wait_for_timeout(200)
        if "原书阈值" not in page.inner_text("#res-chips"):
            problems.append("原书阈值口径未生效")
        page.screenshot(path=str(SHOT_DIR / "05-book-mode.png"), full_page=True)

        # 夜读主题
        page.click("#theme-btn")
        page.wait_for_timeout(250)
        if page.evaluate("document.documentElement.dataset.theme") != "night":
            problems.append("主题切换失败")
        page.screenshot(path=str(SHOT_DIR / "06-night.png"), full_page=True)

        # 手机视口
        page.click("#theme-btn")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOT_DIR / "07-mobile.png"), full_page=True)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if overflow > 2:
            problems.append(f"移动端横向溢出 {overflow}px")

        browser.close()

    if errors:
        problems += ["浏览器报错: " + e for e in errors[:6]]
    print("冒烟测试：", "✓ 通过" if not problems else f"✗ {len(problems)} 项")
    for x in problems:
        print("   - " + x)
    print(f"截图目录：{SHOT_DIR.relative_to(ROOT)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
