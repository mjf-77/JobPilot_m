"""SkillLoader：把 skills/<name>/SKILL.md 的流程性知识按需注入 Agent。

核心思想——渐进披露（progressive disclosure）：
- 路由阶段只用 list_skills()：只读 name + description（每个约 10 token），
  避免几十份 SOP 全文塞进 prompt 造成 token 浪费与指令互相干扰。
- 命中后才用 load_skill(name) 加载全文，注入该子 Agent 的 system prompt。

零第三方依赖：SKILL.md 用极简 frontmatter（--- 包裹的 key: value），手写解析。
"""

import re
from pathlib import Path

from app.config import settings

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse(path: Path) -> tuple[dict, str]:
    """拆出 (元信息, 正文)。没有 frontmatter 时元信息为空。"""
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def list_skills() -> list[dict]:
    """轻量清单：只含 name + description，供路由 prompt 使用。"""
    skills = []
    for path in sorted(settings.skills_dir.glob("*/SKILL.md")):
        meta, _ = _parse(path)#`_parse(path)` 调用内部函数，读取 SKILL.md 文件
        #`meta` 是一个字典，包含了 SKILL.md 中的 frontmatter 信息
        skills.append(
            {
                "name": meta.get("name", path.parent.name),
                #`path.parent`：这个 SKILL.md**所在的文件夹**；`.name`就是文件夹名字
                "description": meta.get("description", ""),
            }
        )
    return skills


def load_skill(name: str, filename: str = "SKILL.md") -> str:
    """按需加载 Skill 目录下的文件。

    默认加载 SKILL.md（自动剥离 frontmatter）；也可加载同目录下的附加资源
    （如 review-template.md）。一个 Skill = 一个目录：SKILL.md 是主 SOP，
    其余文件是按需读取的模板/示例——这样同一 Skill 的不同阶段各用各的 prompt，
    不会因为塞进一份文件而互相干扰。
    """
    path = settings.skills_dir / name / filename
    if not path.is_file():
        raise FileNotFoundError(f"Skill 文件不存在: {name}/{filename}")
    if filename == "SKILL.md":
        _, body = _parse(path)
        return body.strip()
    return path.read_text(encoding="utf-8").strip()
