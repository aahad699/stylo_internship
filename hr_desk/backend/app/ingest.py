"""Read policy files and split them on headings."""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from uuid import uuid4

from .aliases import embed_input
from .config import DATA_ROOT
from .models import ChunkDraft, PolicyDraft

POLICY_DIR = DATA_ROOT / "policies"
_OFFICE_SUFFIXES = (".pdf", ".docx")


def load_office_policies() -> list[PolicyDraft]:
    """Real PDF and Word files placed in the policies folder by HR."""
    from .passages import office_policy

    if not POLICY_DIR.exists():
        return []
    policies = []
    paths = [
        path
        for path in sorted(POLICY_DIR.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in _OFFICE_SUFFIXES
    ]
    for path in paths:
        policy = office_policy(path)
        if policy is None or not policy.chunks:
            continue
        policies.append(policy)
    return policies


def _sections(body: str) -> list[tuple[str, str]]:
    heading = ""
    buffer: list[str] = []
    found: list[tuple[str, str]] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            found.append((heading, text))

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            buffer = []
            heading = line[3:].strip()
        else:
            buffer.append(line)
    flush()
    if found:
        return found
    text = body.strip()
    return [("", text)] if text else []


def make_chunks(policy: PolicyDraft) -> list[ChunkDraft]:
    from .passages import _split_section

    chunks: list[ChunkDraft] = []
    for heading, text in _sections(policy.body):
        label = f"{policy.title}, {heading}" if heading else policy.title
        for piece in _split_section(text):
            embed_text = embed_input(policy.title[:200], heading[:600], heading, piece)
            chunks.append(
                ChunkDraft(
                    section_label=label,
                    heading=heading or policy.title,
                    body=piece,
                    embed_text=embed_text,
                )
            )
    return chunks


def folder_filenames() -> dict[str, str]:
    """Map each folder policy id to the file name currently on disk."""
    if not POLICY_DIR.exists():
        return {}
    from .passages import file_policy_id

    found: dict[str, str] = {}
    for path in POLICY_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in _OFFICE_SUFFIXES:
            continue
        found[file_policy_id(path.name, path.read_bytes())] = path.name
    return found


def find_policy_file(policy_id: str) -> Path | None:
    """The folder file whose hash is this id, if the policy was read from disk."""
    if not policy_id.startswith("doc-") or not POLICY_DIR.exists():
        return None
    from .passages import file_policy_id

    for path in POLICY_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in _OFFICE_SUFFIXES:
            continue
        if file_policy_id(path.name, path.read_bytes()) == policy_id:
            return path
    return None


def remove_policy_files(policy_id: str) -> None:
    """Remove files belonging to a discarded or expired draft."""
    source = find_policy_file(policy_id)
    if source is not None and _inside(source, POLICY_DIR):
        source.unlink()
    media = DATA_ROOT / "policy_media" / policy_id
    if media.exists() and _inside(media, DATA_ROOT / "policy_media"):
        shutil.rmtree(media)


def replacement_policy(
    title: str,
    audience: str,
    effective_from: date,
    effective_to: date | None,
    text: str,
    filename: str,
    payload: bytes,
) -> tuple[PolicyDraft, Path | None]:
    """Build the policy that replaces an old one. PDF and Word keep the full reader."""
    suffix = Path(filename).suffix.lower() if filename else ""
    if payload and suffix in _OFFICE_SUFFIXES:
        from .passages import policy_from_bytes

        stored_name = _destination_name(filename)
        try:
            policy = policy_from_bytes(stored_name, payload)
        except Exception as exc:
            raise ValueError("unreadable") from exc
        if policy is None or not policy.chunks:
            raise ValueError("unreadable")
        _retitle(policy, title, audience, effective_from, effective_to)
        policy.source_name = Path(filename).name
        return policy, POLICY_DIR / stored_name
    body = text.strip()
    if payload and filename:
        try:
            body = payload.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("unreadable") from exc
    if not body:
        raise ValueError("empty")
    policy = PolicyDraft(
        id="upload-" + uuid4().hex[:16],
        title=title,
        audience=audience,
        effective_from=effective_from,
        effective_to=effective_to,
        body=body,
        source_name=Path(filename).name if filename else "",
    )
    policy.chunks = make_chunks(policy)
    return policy, None


def write_policy_file(path: Path, payload: bytes) -> None:
    if not _inside(path, POLICY_DIR):
        raise ValueError("outside")
    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _retitle(
    policy: PolicyDraft,
    title: str,
    audience: str,
    effective_from: date,
    effective_to: date | None,
) -> None:
    old = policy.title
    policy.title = title
    policy.audience = audience
    policy.effective_from = effective_from
    policy.effective_to = effective_to
    for chunk in policy.chunks or []:
        if old and (chunk.section_label == old or chunk.section_label.startswith(old + ", ")):
            chunk.section_label = title + chunk.section_label[len(old) :]
        lines = chunk.embed_text.split("\n")
        if lines and lines[0] == old:
            lines[0] = title
            chunk.embed_text = "\n".join(lines)


def _destination_name(filename: str) -> str:
    safe = _safe_filename(filename)
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    return f"{stem}-{uuid4().hex[:8]}{suffix}"


def _safe_filename(name: str) -> str:
    base = Path(name or "").name
    suffix = Path(base).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        suffix = ".pdf"
    stem = re.sub(r"[^A-Za-z0-9._ ()-]+", "", Path(base).stem).strip().strip(".")
    if not stem:
        stem = "policy"
    return f"{stem[:80]}{suffix}"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
