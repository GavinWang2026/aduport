# AduPort 示例文档

这是一个 **Markdown** 示例，用于测试转换功能。

## 功能列表

- PDF 转 Markdown
- DOCX 转 Markdown
- Markdown 转 DOCX / PDF

## 代码示例

```python
from aduport.core.converter import convert

result = convert(b"# Hello", "md", "docx")
```

## 表格

| 格式 | 说明 |
|------|------|
| md | Markdown 文本 |
| docx | Word 文档 |
| pdf | 便携式文档 |
