---
name: lovstudio-academic-translator
description: 将英文论文、arXiv、期刊/会议文章及技术 PDF 翻译为中文 PDF 或可发布的中文 Markdown。用户提出“翻译英文 PDF”“翻译论文”“论文超级翻译官”“保留图片、公式和原版式”“原文译文对照”“页数对应”或“PDF 图文排版不要乱”时使用。
license: MIT
metadata:
  author: lovstudio
  version: "0.1.0"
  tags:
    - academic
    - pdf
    - translation
    - paper
---

# 论文 PDF 精译

默认交付保版式中文 PDF：原图、图表、公式和页面对象优先留在原位，中文文本替换或覆盖到相邻版面。文本重排版只用于快速阅读，不得把它当作保版式译本。

## Triggers

### Activate when

- 用户上传英文论文、arXiv 或技术 PDF，希望得到中文 PDF，并要求图表、公式、页码或目录尽量保留。
- 用户说“翻译英文 PDF”“论文超级翻译官”“保留图片和原版式”，或要求逐页原文/译文对照。

### Do not activate when

- 用户只需翻译一段纯文本、网页或 Markdown，不涉及 PDF 排版；使用普通翻译流程。

## 先选路线

1. 扫描件先 OCR；可抽取文本的 PDF 才进入翻译。
2. 用户要求保留图片、公式、表格、版式或页码对应时，优先用 `pdf2zh / PDFMathTranslate`。从原 PDF 页对象翻译，输出 `mono`（中文）与 `dual`（对照）版本。
3. 用户只需快速中文阅读稿时，才使用 [scripts/translate_paper_pdf.py](scripts/translate_paper_pdf.py)；它按页抽取文字并重新排版，图片和精确布局不会保留。
4. 同时需要公众号/Markdown 成稿时，再导出 Markdown，并按 [references/output-contract.md](references/output-contract.md) 核对图文锚点。

## 保版式 PDF 工作流

1. 确认来源标题、作者、版本、总页数和页尺寸；记录图表、目录、公式、参考文献起止页。
2. 先以含图的正文页做 1–2 页试跑，检查图片是否保留、文字是否回填到合适位置、页尺寸是否一致。
3. 全量运行；默认单线程以稳定为先。原图内嵌文字不会自动变成中文，只有用户明确要求时才 OCR、翻译并重绘该图片。
4. 目录页需保留或重建为可见的中文蓝色链接、点线引导和内部跳转；不要只叠透明点击区域。
5. 参考文献优先保留原文条目、缩进、URL 和页码。书目信息被机翻成连续散文或破坏条目结构时，替回原文参考文献页。

```bash
uv tool install --python 3.12 pdf2zh

ALI_API_KEY="$DASHSCOPE_API_KEY" pdf2zh \
  "/path/to/paper.pdf" -li en -lo zh -s qwen-mt -t 1 \
  -o "/path/to/output"
```

将输出重命名为 `论文中文译本 - <原文件名> - <日期> - vNN.pdf`；若交付对照版，使用同名前缀加 `-facing-pages`。不把个人、项目或沟通过程的背景写进论文正文、页脚或文件名。

## 快速逐页译本与无密钥模式

脚本支持 `target-only`（同页数中文）、`facing-pages`（原文/译文交替）和 `bilingual-expanded`（同逻辑页数、页面加高）。这些模式仅适用于不要求保图保版的快速稿。

```bash
python3 scripts/translate_paper_pdf.py "/path/to/paper.pdf" \
  --mode target-only --translator agent --output-dir "/path/to/output"
```

`agent` 会导出 `.translations.json`。当前会话逐页填入中文、保持页边界和术语一致，再用 `--translation-json` 渲染。密集论文优先建立术语表，保留章节号、引用、公式、图表标签、URL、代码、数值、单位及专有名词。

## 验收

1. `pdfinfo`：中文单页版页数等于源 PDF；对照版页数等于两倍；扩展双语版逻辑页数相同、页面高度更大。
2. 渲染检查首页、至少一页含图正文、目录页、密集公式/表格页和参考文献页。检查中文字体、图片、裁切、溢出、重叠与乱码。
3. 全文检查提示词残留、代码围栏、待翻译标记、API 错误和模型解释；发现后清理缓存或重译受影响页，再重新渲染。
4. 用 [scripts/document_inventory.py](scripts/document_inventory.py) 对 Markdown 源稿与译稿验收图、表、公式、代码和链接；再运行项目的格式检查与 `git diff --check`。

## 资源

- [references/output-contract.md](references/output-contract.md)：PDF/Markdown 的图文、目录、书目和页数验收规则。
- [scripts/translate_paper_pdf.py](scripts/translate_paper_pdf.py)：快速逐页重排版与 Agent JSON 交接。
- [scripts/document_inventory.py](scripts/document_inventory.py)：Markdown 图文结构差异检查。
