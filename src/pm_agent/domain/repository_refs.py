from __future__ import annotations

import re
from dataclasses import dataclass

_REFERENCE = re.compile(
    r"(?<![\w.-])(?:https?://github\.com/|git@github\.com:|@)"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?"
    r"(?=$|[\s,;:!?)}\]])"
)


@dataclass(frozen=True)
class RepositoryReference:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def extract_github_references(text: str) -> list[RepositoryReference]:
    references: list[RepositoryReference] = []
    seen: set[str] = set()
    for match in _REFERENCE.finditer(text):
        name = match.group("repo").rstrip(".,")
        if name.endswith(".git"):
            name = name[:-4]
        reference = RepositoryReference(match.group("owner"), name)
        normalized = reference.slug.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        references.append(reference)
    return references
