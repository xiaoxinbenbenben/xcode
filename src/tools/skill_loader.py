from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from warnings import warn

from src.tools.common import PROJECT_ROOT

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class SkillMeta:
    # Skill 元信息保持很薄，只覆盖当前阶段真正需要的索引字段。
    name: str
    description: str
    path: str
    base_dir: str
    body: str
    mtime: float


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    # 当前 skill 格式固定是“frontmatter + markdown 正文”，这里用最小解析就够了。
    if not text.startswith("---\n"):
        return None

    end_index = text.find("\n---\n", 4)
    if end_index < 0:
        return None

    header_text = text[4:end_index]
    body = text[end_index + 5 :].lstrip("\n")
    metadata: dict[str, str] = {}
    for raw_line in header_text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            return None
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, body


def _expand_skill_arguments(body: str, args: str) -> str:
    # Skills 只做一层轻量参数展开，不在这里引入更复杂的模板系统。
    normalized_args = args.strip()
    if "$ARGUMENTS" in body:
        return body.replace("$ARGUMENTS", normalized_args)
    if not normalized_args:
        return body
    return f"{body.rstrip()}\n\nARGUMENTS:\n{normalized_args}\n"


class SkillLoader:
    def __init__(self, skills_root: Path | None = None) -> None:
        self.skills_root = skills_root or PROJECT_ROOT / "skills"
        self._skills: dict[str, SkillMeta] = {}
        self._last_scan_marker = -1.0

    def _compute_scan_marker(self) -> float:
        # mtime 刷新只需要一个最小正确性：目录或任意 skill 文件变化时重新扫描。
        if not self.skills_root.exists():
            return 0.0

        marker = self.skills_root.stat().st_mtime
        for path in self.skills_root.rglob("SKILL.md"):
            marker = max(marker, path.stat().st_mtime)
        return marker

    def _should_refresh_on_call(self) -> bool:
        # 这个开关只影响“调用前自动刷新”，不影响显式 scan()。
        raw_value = os.getenv("SKILLS_REFRESH_ON_CALL", "true").strip().casefold()
        if raw_value in {"1", "true", "yes", "on"}:
            return True
        if raw_value in {"0", "false", "no", "off"}:
            return False
        raise ValueError("SKILLS_REFRESH_ON_CALL 必须是 true 或 false。")

    def refresh_if_stale(self) -> None:
        if not self._should_refresh_on_call():
            if self._last_scan_marker < 0:
                self.scan()
            return

        marker = self._compute_scan_marker()
        if marker != self._last_scan_marker:
            self.scan()

    def scan(self) -> list[SkillMeta]:
        skills: dict[str, SkillMeta] = {}
        if not self.skills_root.exists():
            self._skills = {}
            self._last_scan_marker = 0.0
            return []

        # legacy 文档要求支持 skills/**/SKILL.md，所以这里递归扫描。
        for skill_path in sorted(self.skills_root.rglob("SKILL.md")):
            parsed = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
            if parsed is None:
                warn(f"跳过非法 skill 文件：{skill_path}")
                continue

            metadata, body = parsed
            name = metadata.get("name", "").strip()
            description = metadata.get("description", "").strip()
            if not SKILL_NAME_PATTERN.fullmatch(name) or not description:
                warn(f"跳过非法 skill 元信息：{skill_path}")
                continue

            if skill_path.is_relative_to(PROJECT_ROOT):
                relative_path = skill_path.relative_to(PROJECT_ROOT)
            else:
                relative_path = Path("skills") / skill_path.parent.relative_to(self.skills_root) / "SKILL.md"
            base_dir = relative_path.parent.as_posix()
            if name in skills:
                # duplicate name 先按 legacy 要求保留后发现的版本，同时给开发期一个最小提醒。
                warn(f"发现重复 skill 名称，保留后者：{name}")
            skills[name] = SkillMeta(
                name=name,
                description=description,
                path=relative_path.as_posix(),
                base_dir=base_dir,
                body=body,
                mtime=skill_path.stat().st_mtime,
            )

        self._skills = skills
        self._last_scan_marker = self._compute_scan_marker()
        return self.list_skills()

    def list_skills(self) -> list[SkillMeta]:
        self.refresh_if_stale()
        return [self._skills[name] for name in sorted(self._skills)]

    def get_skill(self, name: str) -> SkillMeta | None:
        self.refresh_if_stale()
        return self._skills.get(name)

    def render_skill(self, name: str, args: str = "") -> SkillMeta | None:
        skill = self.get_skill(name)
        if skill is None:
            return None

        # 渲染后的正文只影响当前调用，不回写缓存。
        return SkillMeta(
            name=skill.name,
            description=skill.description,
            path=skill.path,
            base_dir=skill.base_dir,
            body=_expand_skill_arguments(skill.body, args),
            mtime=skill.mtime,
        )


def read_skills_prompt_char_budget() -> int:
    raw_value = os.getenv("SKILLS_PROMPT_CHAR_BUDGET", "12000").strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("SKILLS_PROMPT_CHAR_BUDGET 必须是正整数。") from exc
    if value <= 0:
        raise ValueError("SKILLS_PROMPT_CHAR_BUDGET 必须是正整数。")
    return value


@lru_cache(maxsize=1)
def get_default_skill_loader() -> SkillLoader:
    # Skill loader 和 L1 技能目录应该共享同一份索引视图，避免两套缓存状态。
    return SkillLoader(PROJECT_ROOT / "skills")
