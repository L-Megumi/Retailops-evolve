from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from .task_contract import BOUNDARY, NEGATION, affirmative_mentions


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True)
class CodePluginRegistration:
    plugin_id: str
    cues: tuple[str, ...]
    source_sha256: str
    source_path: str
    keyword_groups: tuple[tuple[str, ...], ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.plugin_id or len(self.plugin_id) > 80:
            raise ValueError("plugin_id must contain 1-80 characters")
        if not 1 <= len(self.cues) <= 12:
            raise ValueError("a plugin registration must contain 1-12 cues")
        normalized = [_normalize(cue) for cue in self.cues]
        if any(len(cue) < 4 or len(cue) > 80 for cue in normalized):
            raise ValueError("each plugin cue must contain 4-80 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("plugin cues must be unique")
        if len(self.keyword_groups) > 8:
            raise ValueError("a plugin registration may contain at most 8 keyword groups")
        normalized_groups: list[tuple[str, ...]] = []
        for group in self.keyword_groups:
            if not 2 <= len(group) <= 4:
                raise ValueError("each keyword group must contain 2-4 required tokens")
            normalized_group = tuple(_normalize(token) for token in group)
            if any(not 1 <= len(token) <= 40 for token in normalized_group):
                raise ValueError("keyword-group tokens must contain 1-40 characters")
            if len(normalized_group) != len(set(normalized_group)):
                raise ValueError("tokens within a keyword group must be unique")
            normalized_groups.append(normalized_group)
        if len(normalized_groups) != len(set(normalized_groups)):
            raise ValueError("keyword groups must be unique")
        if len(self.source_sha256) != 64:
            raise ValueError("source_sha256 must be a 64-character digest")

    def matches(self, instruction: str) -> bool:
        text = _normalize(instruction)
        literal = any(affirmative_mentions(text, re.escape(_normalize(cue)))[0] for cue in self.cues)
        grouped = any(
            all(_normalize(token) in clause for token in group) and not NEGATION.search(clause)
            for clause in BOUNDARY.split(text) for group in self.keyword_groups
        )
        return literal or grouped

    def to_dict(self) -> dict[str, object]:
        return {
            "plugin_id": self.plugin_id,
            "cues": list(self.cues),
            "source_sha256": self.source_sha256,
            "source_path": self.source_path,
            "keyword_groups": [list(group) for group in self.keyword_groups],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CodePluginRegistration":
        cues = payload.get("cues")
        if not isinstance(cues, list):
            raise ValueError("plugin cues must be a list")
        raw_groups = payload.get("keyword_groups", [])
        if not isinstance(raw_groups, list) or any(
            not isinstance(group, list) for group in raw_groups
        ):
            raise ValueError("keyword_groups must be a list of token lists")
        return cls(
            plugin_id=str(payload.get("plugin_id", "")),
            cues=tuple(str(cue) for cue in cues),
            source_sha256=str(payload.get("source_sha256", "")),
            source_path=str(payload.get("source_path", "")),
            keyword_groups=tuple(
                tuple(str(token) for token in group) for group in raw_groups
            ),
            reason=str(payload.get("reason", "")),
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> "CodePluginRegistration":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
