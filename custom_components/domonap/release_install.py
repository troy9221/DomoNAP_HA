"""Install a Domonap GitHub release zip into custom_components/domonap."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

COMPONENT_MARKER = "custom_components/domonap/"


def find_component_prefix(names: list[str]) -> str | None:
    """Return the zip folder prefix that contains the integration files."""
    for raw in names:
        name = raw.replace("\\", "/")
        idx = name.find(COMPONENT_MARKER)
        if idx != -1:
            return name[: idx + len(COMPONENT_MARKER)]
    return None


def _safe_member_path(prefix: str, filename: str, dest: Path) -> Path | None:
    name = filename.replace("\\", "/")
    if not name.startswith(prefix) or name.endswith("/"):
        return None
    relative = name[len(prefix) :]
    if not relative or relative.startswith("/"):
        return None
    parts = Path(relative).parts
    if ".." in parts or "__pycache__" in parts:
        return None
    target = (dest / relative).resolve()
    dest_root = dest.resolve()
    if target != dest_root and not str(target).startswith(str(dest_root) + "/"):
        return None
    return target


def install_component_from_zip(
    data: bytes,
    dest: Path,
    keep_backup: bool = False,
) -> None:
    """Replace ``dest`` with ``custom_components/domonap`` from a GitHub zip."""
    dest = dest.resolve()
    if not dest.exists() or not dest.is_dir():
        raise ValueError(f"Integration directory does not exist: {dest}")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        prefix = find_component_prefix(
            [info.filename for info in archive.infolist()]
        )
        if not prefix:
            raise ValueError(
                "GitHub archive does not contain custom_components/domonap"
            )

        tmp = dest.parent / f".{dest.name}.tmp_install"
        backup = dest.parent / f".{dest.name}.bak"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)

        try:
            extracted = 0
            for info in archive.infolist():
                target = _safe_member_path(prefix, info.filename, tmp)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted += 1
            if extracted == 0:
                raise ValueError("GitHub archive did not contain integration files")
            if not (tmp / "manifest.json").is_file():
                raise ValueError("Extracted archive is missing manifest.json")

            if backup.exists():
                shutil.rmtree(backup)
            dest.rename(backup)
            try:
                tmp.rename(dest)
            except Exception:
                backup.rename(dest)
                raise
            if not keep_backup:
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
