"""生成仓库内的 PDF 文档（用 Playwright/Chromium 打印，无需 pandoc / LaTeX）。

产出：
    docs/牙牌神数-整理本.pdf      ← corpus/《牙牌神数》简体横排-西川斋整理.md
    docs/签谱一百二十五数.pdf      ← data/yapai_125.json（排版成签谱）
    docs/设计说明.pdf             ← DESIGN.md

用法：
    python tools/make_pdf.py            # 全部生成
    python tools/make_pdf.py --only book
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TMP = ROOT / "tests" / "_pdf_tmp"
CORPUS_MD = ROOT / "corpus" / "《牙牌神数》简体横排-西川斋整理.md"
ORACLE = ROOT / "data" / "yapai_125.json"

CSS = """
@page { size: A4; margin: 20mm 18mm 18mm; }
* { box-sizing: border-box; }
body {
  font-family: "Songti SC", "SimSun", "Source Han Serif SC", "Noto Serif SC", serif;
  font-size: 11.2pt; line-height: 1.95; color: #241f18; margin: 0;
  text-align: justify;
}
h1, h2, h3, h4 { font-family: "KaiTi", "Kaiti SC", "STKaiti", "SimSun", serif; font-weight: 600; }
h1 { font-size: 21pt; letter-spacing: .18em; text-align: center; margin: 0 0 6pt; }
h2 { font-size: 15pt; letter-spacing: .12em; margin: 20pt 0 8pt; padding-bottom: 4pt;
     border-bottom: .6pt solid #c9b78f; page-break-after: avoid; }
h3 { font-size: 13pt; letter-spacing: .1em; margin: 14pt 0 5pt; page-break-after: avoid; }
p { margin: 0 0 7pt; }
blockquote { margin: 6pt 0; padding: 5pt 12pt; border-left: 2pt solid #c9b78f; background: #faf5e9;
             color: #4a4132; font-size: 10.6pt; }
blockquote p { margin: 0; }
hr { border: none; border-top: .5pt solid #ddd0b4; margin: 14pt 0; }
table { width: 100%; border-collapse: collapse; font-size: 10pt; margin: 8pt 0 12pt; }
th, td { border: .5pt solid #cfc0a1; padding: 4pt 6pt; text-align: left; }
th { background: #f3ead7; font-family: "KaiTi", serif; letter-spacing: .06em; }
strong { font-weight: 700; }
code { font-family: Consolas, monospace; font-size: 9.6pt; background: #f3ead7; padding: 0 2pt; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin-bottom: 3pt; }
.cover { height: 236mm; display: flex; flex-direction: column; justify-content: center;
         align-items: center; text-align: center; page-break-after: always; }
.cover .t { font-family: "KaiTi", serif; font-size: 34pt; letter-spacing: .3em; margin-bottom: 10pt; }
.cover .s { font-size: 11pt; letter-spacing: .2em; color: #6b6146; line-height: 2.4; }
.cover .line { width: 40mm; border-top: .6pt solid #b9a67e; margin: 16pt 0; }
.oracle { page-break-inside: avoid; margin-bottom: 16pt; }
.oracle .no { font-family: "KaiTi", serif; font-size: 12.5pt; letter-spacing: .12em; color: #8a6d33; }
.oracle .motto { font-family: "KaiTi", serif; color: #a8332a; letter-spacing: .08em; margin: 3pt 0 6pt; }
.oracle .poem { font-family: "KaiTi", serif; font-size: 12.5pt; line-height: 2.1; letter-spacing: .06em;
                margin: 5pt 0 8pt; }
.oracle .poem span { display: block; }
.oracle .tag { color: #8a6d33; letter-spacing: .1em; font-size: 10.4pt; }
.oracle p { font-size: 10.8pt; }
"""

COVER_TPL = """<div class="cover">
  <div class="t">{title}</div>
  <div class="line"></div>
  <div class="s">{sub}</div>
</div>"""


def md_to_html(md_text: str, title: str, sub: str) -> str:
    import markdown

    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "md_in_html"],
        output_format="html5",
    )
    # markdown 会把整理本里的 <!-- p.N --> 保留为注释，去掉以免留白
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return f"""<!DOCTYPE html><html lang="zh-Hans"><head><meta charset="utf-8">
<title>{htmllib.escape(title)}</title><style>{CSS}</style></head><body>
{COVER_TPL.format(title=title, sub=sub)}
{body}</body></html>"""


def oracle_html(data: list[dict]) -> str:
    parts = [f"""<!DOCTYPE html><html lang="zh-Hans"><head><meta charset="utf-8">
<title>牙牌神数·签谱一百二十五数</title><style>{CSS}</style></head><body>
{COVER_TPL.format(title="牙牌神数·签谱", sub="一百二十五数<br>始 · 中 · 末<br>清·何汝柽 撰　岳庆山樵 著")}
<h2>凡例</h2>
<p>一、每数由三次排牌所得品阶合成：初数为始（起因本心），中数为中（变迁借力），末数为末（终局归宿）。</p>
<p>二、一百二十五数，五五二十五，按始数分五卷：上上数（001–025）、上中数（026–050）、中平数（051–075）、中下数（076–100）、下下数（101–125）。</p>
<p>三、占得之数与所问之事语气未协时，当于字句间玩味详测之，所谓以意逆志，是为得之也。</p>
"""]
    volume = None
    for e in data:
        if e["category"] != volume:
            volume = e["category"]
            parts.append(f"<h2>卷之{['一', '二', '三', '四', '五'][['上上数', '上中数', '中平数', '中下数', '下下数'].index(volume)]}　{volume}（二十五数）</h2>")
        poem = "".join(f"<span>{htmllib.escape(l)}</span>" for l in e["poem"])
        motto = f'<div class="motto">【断意】{htmllib.escape(e["motto"])}</div>' if e["motto"] else ""
        parts.append(f"""<div class="oracle">
  <div class="no">第 {e['id']:03d} 数　{e['title']}</div>
  {motto}
  <div class="poem">{poem}</div>
  <p><span class="tag">【解曰】</span>{htmllib.escape(e['jie'])}</p>
  <p><span class="tag">【断曰】</span>{htmllib.escape(e['duan'])}</p>
</div>""")
    parts.append("</body></html>")
    return "".join(parts)


def print_pdf(html_text: str, out_pdf: Path, footer: str) -> tuple[int, float]:
    from playwright.sync_api import sync_playwright

    TMP.mkdir(exist_ok=True)
    tmp_html = TMP / (out_pdf.stem + ".html")
    tmp_html.write_text(html_text, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(tmp_html.as_uri())
        page.wait_for_timeout(300)
        page.pdf(
            path=str(out_pdf), format="A4", print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                '<table style="width:100%;font-size:8pt;color:#8b7f68;font-family:SimSun,serif">'
                '<tr><td style="text-align:left">' + htmllib.escape(footer) + '</td>'
                '<td style="text-align:right">第 <span class="pageNumber"></span> 页 / 共 '
                '<span class="totalPages"></span> 页</td></tr></table>'
            ),
            margin={"top": "16mm", "bottom": "18mm", "left": "18mm", "right": "18mm"},
        )
        browser.close()
    tmp_html.unlink(missing_ok=True)
    return len_data_pages(out_pdf), out_pdf.stat().st_size / 1024


def len_data_pages(pdf: Path) -> int:
    raw = pdf.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", raw))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["book", "oracle", "design"], default=None)
    args = ap.parse_args(argv)
    DOCS.mkdir(exist_ok=True)
    jobs = []

    if args.only in (None, "book"):
        md = CORPUS_MD.read_text(encoding="utf-8")
        jobs.append((
            DOCS / "牙牌神数-整理本.pdf",
            md_to_html(md, "牙牌神数", "清·何汝柽 撰　岳庆山樵 著<br>术数西川斋 简体横排整理"),
            "牙牌神数·整理本",
        ))
    if args.only in (None, "oracle"):
        data = json.loads(ORACLE.read_text(encoding="utf-8"))
        jobs.append((DOCS / "签谱一百二十五数.pdf", oracle_html(data), "牙牌神数·签谱"))
    if args.only in (None, "design"):
        md = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
        jobs.append((
            DOCS / "设计说明.pdf",
            md_to_html(md, "牙牌神数·设计说明", "在线占验系统<br>规则口径 · 架构 · 验证"),
            "牙牌神数·设计说明",
        ))

    for out_pdf, html_text, footer in jobs:
        pages, kb = print_pdf(html_text, out_pdf, footer)
        print(f"✓ {out_pdf.relative_to(ROOT)}　{pages} 页　{kb:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
