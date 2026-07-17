from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pm_agent.domain.models import RepoSnapshot, new_id, utc_now
from pm_agent.ports.repository_context import SnapshotRequest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


IGNORED_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", "vendor", "dist", "build", ".venv", "venv",
    "env", ".idea", ".vscode", ".tox", ".eggs",
}
SECRET_NAMES = {
    ".env", ".env.local", ".env.production", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "secrets.json",
}
MAX_DEPTH = 4
MAX_ENTRIES = 500
MAX_README_CHARS = 1_500
_BINARY = re.compile(r"\.(?:png|jpe?g|gif|webp|ico|pdf|zip|gz|tar|exe|dll|so|dylib)$", re.I)


class LocalRepositoryAnalyzer:
    def build_snapshot(self, request: SnapshotRequest) -> RepoSnapshot:
        root = Path(request.repo_path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Repository path does not exist: {root}")
        branch = self._branch_name(root) or request.branch
        summary = {
            "root": str(root),
            "branch": branch,
            "languages": self._detect_languages(root),
            "manifests": self._read_manifests(root),
            "entry_points": self._entry_points(root),
            "readme_excerpt": self._read_readme(root),
            "tree": self._tree(root),
        }
        digest = hashlib.sha256(
            json.dumps(summary, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return RepoSnapshot(
            id=new_id(),
            project_id=request.project_id,
            branch=branch,
            head_ref=self._head_ref(root),
            tree_digest=digest,
            summary=summary,
            created_by_action_id=request.action_id,
            created_at=utc_now(),
        )

    @staticmethod
    def _detect_languages(root: Path) -> list[str]:
        markers = {
            "pyproject.toml": "Python",
            "requirements.txt": "Python",
            "package.json": "JavaScript/TypeScript",
            "composer.json": "PHP",
            "go.mod": "Go",
            "Cargo.toml": "Rust",
            "pom.xml": "Java",
        }
        return sorted({language for marker, language in markers.items() if (root / marker).exists()})

    @staticmethod
    def _read_manifests(root: Path) -> dict[str, Any]:
        manifests: dict[str, Any] = {}
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                with pyproject.open("rb") as handle:
                    data = tomllib.load(handle)
                manifests["pyproject.toml"] = {
                    "project": data.get("project", {}),
                    "build-system": data.get("build-system", {}),
                }
            except (OSError, ValueError) as exc:
                manifests["pyproject.toml"] = {"error": str(exc)}
        package_json = root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                manifests["package.json"] = {
                    key: data.get(key) for key in ("name", "version", "scripts", "dependencies")
                }
            except (OSError, ValueError) as exc:
                manifests["package.json"] = {"error": str(exc)}
        composer_json = root / "composer.json"
        if composer_json.exists():
            try:
                data = json.loads(composer_json.read_text(encoding="utf-8"))
                manifests["composer.json"] = {
                    key: data.get(key) for key in ("name", "description", "require", "require-dev")
                }
            except (OSError, ValueError) as exc:
                manifests["composer.json"] = {"error": str(exc)}
        requirements = root / "requirements.txt"
        if requirements.exists():
            manifests["requirements.txt"] = {
                "packages": [
                    line.split("#", 1)[0].strip()
                    for line in requirements.read_text(errors="ignore").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ][:100]
            }
        return manifests

    def _entry_points(self, root: Path) -> list[str]:
        found: list[str] = []
        for name in ("__main__.py", "main.py", "app.py", "cli.py", "manage.py"):
            for path in root.rglob(name):
                if self._ignored(root, path):
                    continue
                found.append(str(path.relative_to(root)))
                if len(found) == 10:
                    return found
        return found

    @staticmethod
    def _read_readme(root: Path) -> str:
        for name in ("README.md", "README.rst", "README.txt", "README"):
            path = root / name
            if path.exists() and path.name not in SECRET_NAMES:
                return path.read_text(errors="ignore")[:MAX_README_CHARS]
        return ""

    def _tree(self, root: Path) -> str:
        entries: list[str] = []

        def walk(path: Path, depth: int) -> None:
            if depth > MAX_DEPTH or len(entries) >= MAX_ENTRIES:
                return
            try:
                children = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name))
            except OSError:
                return
            for child in children:
                if self._ignored(root, child):
                    continue
                relative = child.relative_to(root)
                entries.append(f"{'  ' * depth}{relative.name}{'/' if child.is_dir() else ''}")
                if child.is_dir():
                    walk(child, depth + 1)
                if len(entries) >= MAX_ENTRIES:
                    entries.append("... (truncated)")
                    return

        walk(root, 0)
        return "\n".join(entries)

    @staticmethod
    def _ignored(root: Path, path: Path) -> bool:
        relative_parts = path.relative_to(root).parts
        if any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in relative_parts):
            return True
        if path.name in SECRET_NAMES or _BINARY.search(path.name):
            return True
        try:
            return path.is_file() and path.stat().st_size > 1_000_000
        except OSError:
            return True

    @staticmethod
    def _head_ref(root: Path) -> str | None:
        head = root / ".git" / "HEAD"
        if not head.exists():
            return None
        try:
            content = head.read_text().strip()
        except OSError:
            return None
        if content.startswith("ref:"):
            ref_path = root / ".git" / content.removeprefix("ref:").strip()
            if ref_path.exists():
                return ref_path.read_text().strip()
            return content.removeprefix("ref:").strip()
        return content

    @staticmethod
    def _branch_name(root: Path) -> str | None:
        head = root / ".git" / "HEAD"
        if not head.exists():
            return None
        try:
            content = head.read_text().strip()
        except OSError:
            return None
        prefix = "ref: refs/heads/"
        return content.removeprefix(prefix) if content.startswith(prefix) else None
