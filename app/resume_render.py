"""简历草稿 → 可打印的 HTML。

为什么走 HTML 而不是服务端直出 PDF：

- **中文字体**：服务端生成 PDF 得往镜像里塞字体文件，缺字体就是一片方块；
  交给浏览器渲染则用系统字体，这个坑直接消失。
- **能预览**：用户先看到效果再打印，比「下载完发现排版崩了」体验好。
- **改版式不动代码**：样式全在 templates/resume.html 里，改样式不用碰 Python。

模板对齐的是「深蓝章节标题 + 通栏下划线 + 姓名居中 + 三栏对齐行」这一套
常见的中文技术简历版式。

注意 autoescape 必须开：简历内容来自用户输入，直接插 HTML 就是 XSS。
"""

import copy
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

_env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

# 时间区间的各种写法：2024.09-2028.06 / 2024.09 – 2028.06 / 2024.09 至 2028.06
_PERIOD_SEP = re.compile(r"\s*[-–—~～至]+\s*")


def _normalize(draft: dict) -> dict:
    """统一时间区间的写法为全角波浪号。

    中文简历习惯写「2024.09～2028.06」，而模型从对话里抽出来的常常是
    「2024.09-2028.06」。在渲染层统一，比指望模型每次都记住格式可靠。
    """
    data = copy.deepcopy(draft or {})
    for key in ("education", "projects", "experience"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("period"):
                item["period"] = _PERIOD_SEP.sub("～", str(item["period"]))
    return data


def render(draft: dict, missing: list[str] | None = None, embed: bool = False) -> str:
    """把草稿渲染成完整 HTML 页面。

    missing 是还缺的必填项（显示在页顶提示里）；
    embed=True 用于 iframe 内嵌预览——那种场景下不需要「按 Ctrl+P」这类操作提示。
    """
    return _env.get_template("resume.html").render(
        draft=_normalize(draft), missing=missing or [], embed=embed
    )
