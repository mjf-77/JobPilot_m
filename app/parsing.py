"""文档解析：把上传的简历文件转成纯文本。

目前只支持 PDF（简历的主流格式）。pypdf 提取的是文字流——
**多栏排版或表格的阅读顺序可能错乱**，如果后续发现质量不够，换 pdfplumber。
"""

import io

from pypdf import PdfReader

# 截断上限：防止一份超长文档把上下文预算直接撑爆
MAX_CHARS = 20000


def pdf_to_text(data: bytes) -> str:
    """PDF 字节 → 纯文本（按页拼接，跳过空白页）。"""
    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)[:MAX_CHARS]
