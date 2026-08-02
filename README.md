# lovstudio-academic-translator

![版本](https://img.shields.io/badge/version-0.1.0-CC785C)

把英文论文、arXiv 和技术 PDF 翻译为中文，同时尽量保留图表、公式、目录与页码对应关系。

由 [LovStudio](https://lovstudio.ai) 制作，收录于 [lovstudio skills](https://github.com/lovstudio/skills)。

## 安装

```bash
npx lovstudio skills add academic-translator -g -y
```

## 使用

```text
/lovstudio-academic-translator 将这篇英文论文 PDF 翻译为保留图片和原版式的中文 PDF
```

需要保留版式时，技能优先使用 PDFMathTranslate，从原 PDF 页面对象生成中文或对照版本；仅需快速阅读时，使用逐页文本重排版流程。

## License

MIT
