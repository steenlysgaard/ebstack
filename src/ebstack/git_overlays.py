from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from platformdirs import user_cache_dir

from .errors import EbstackError


def cache_root() -> Path:
    return Path(user_cache_dir("ebstack"))


def materialize_easyconfigs_prs(
    prs: tuple[str, ...], *, repo_url: str, cache_dir: Path | None = None
) -> tuple[Path, ...]:
    return materialize_prs(
        prs,
        repo_url=repo_url,
        repo_name="easybuild-easyconfigs",
        overlays_name="overlays",
        changed_root="easybuild/easyconfigs",
        returned_root="easybuild/easyconfigs",
        cache_dir=cache_dir,
    )


def materialize_easyblocks_prs(
    prs: tuple[str, ...], *, repo_url: str, cache_dir: Path | None = None
) -> tuple[Path, ...]:
    return materialize_prs(
        prs,
        repo_url=repo_url,
        repo_name="easybuild-easyblocks",
        overlays_name="easyblock-overlays",
        changed_root="easybuild/easyblocks",
        returned_root="easybuild/easyblocks",
        cache_dir=cache_dir,
    )


def materialize_prs(
    prs: tuple[str, ...],
    *,
    repo_url: str,
    repo_name: str,
    overlays_name: str,
    changed_root: str,
    returned_root: str,
    cache_dir: Path | None = None,
) -> tuple[Path, ...]:
    root = cache_dir or cache_root()
    repo_dir = root / repo_name
    overlays_dir = root / overlays_name
    overlays_dir.mkdir(parents=True, exist_ok=True)

    if not prs:
        return ()

    if not (repo_dir / ".git").is_dir():
        run_git(
            ["clone", "--filter=blob:none", "--no-checkout", repo_url, str(repo_dir)]
        )

    overlays: list[Path] = []
    run_git(
        ["-C", str(repo_dir), "fetch", "origin", "+develop:refs/remotes/origin/develop"]
    )

    for pr in prs:
        pr_ref = f"refs/pr/{pr}"
        overlay = overlays_dir / f"pr-{pr}"
        overlay_root = overlay / returned_root

        run_git(["-C", str(repo_dir), "fetch", "origin", f"+pull/{pr}/head:{pr_ref}"])
        if overlay.exists():
            shutil.rmtree(overlay)
        overlay_root.mkdir(parents=True, exist_ok=True)

        changed = git_stdout(
            [
                "-C",
                str(repo_dir),
                "diff",
                "--name-only",
                "--diff-filter=AMCR",
                f"origin/develop...{pr_ref}",
                "--",
                changed_root,
            ]
        ).splitlines()
        if not changed:
            raise EbstackError(f"PR #{pr} does not change files under {changed_root}")

        for changed_file in changed:
            target = overlay / changed_file
            target.parent.mkdir(parents=True, exist_ok=True)
            content = git_stdout(
                ["-C", str(repo_dir), "show", f"{pr_ref}:{changed_file}"]
            )
            target.write_text(content, encoding="utf-8")

        overlays.append(overlay_root)

    return tuple(overlays)


def run_git(args: list[str]) -> None:
    try:
        completed = subprocess.run(
            ["git", *args], text=True, capture_output=True, check=False
        )
    except OSError as err:
        raise EbstackError(f"Failed to run git: {err}") from err
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise EbstackError(f"git {' '.join(args)} failed: {stderr}")


def git_stdout(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], text=True, capture_output=True, check=False
        )
    except OSError as err:
        raise EbstackError(f"Failed to run git: {err}") from err
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise EbstackError(f"git {' '.join(args)} failed: {stderr}")
    return completed.stdout
