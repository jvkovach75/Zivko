from __future__ import annotations

from pathlib import Path


SEARCH_ROOTS = [
    Path(r"D:\OneDrive"),
    Path.home(),
]


def resolve_existing_path(raw_path: str | Path) -> Path:
    path = Path(str(raw_path).strip()).expanduser()
    if path.exists():
        return path

    for candidate in _repair_candidates(str(path)):
        candidate_path = Path(candidate).expanduser()
        if candidate_path.exists():
            return candidate_path

    filename = path.name
    if filename:
        for root in SEARCH_ROOTS:
            if not root.exists():
                continue
            try:
                matches = list(root.rglob(filename))
            except Exception:
                continue
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raw_parts = {part.lower() for part in path.parts if part and part not in ("\\", "/")}
                scored = []
                for match in matches:
                    score = sum(1 for part in match.parts if part.lower() in raw_parts)
                    scored.append((score, match))
                scored.sort(key=lambda item: item[0], reverse=True)
                if scored and scored[0][0] > 0:
                    return scored[0][1]

    return path


def _repair_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    seen = {value}
    encodings = ("latin1", "cp1250", "cp1252")
    for source_enc in encodings:
        for target_enc in ("utf-8", "utf8"):
            try:
                repaired = value.encode(source_enc, errors="ignore").decode(target_enc, errors="ignore")
            except Exception:
                continue
            if repaired and repaired not in seen:
                seen.add(repaired)
                candidates.append(repaired)
    return candidates
