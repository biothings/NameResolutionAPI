import logging
import os
import pathlib
from collections.abc import Iterable

import git

logger = logging.getLogger(__name__)

UNKNOWN_VERSION = "Unknown"
VERSION_FILE_NAME = "version.txt"
VERSION_FILE_ENV_VAR = "NAMERES_VERSION_FILE"
CONTAINER_VERSION_FILE = pathlib.Path("/home/nameres/configuration") / VERSION_FILE_NAME


def read_version_file(version_file_paths: Iterable[pathlib.Path] | None = None) -> str | None:
    """Read the build-time version file when the app is running from a packaged image."""
    candidate_paths = []
    configured_path = os.getenv(VERSION_FILE_ENV_VAR)
    if configured_path:
        candidate_paths.append(pathlib.Path(configured_path))

    candidate_paths.extend(version_file_paths or [pathlib.Path.cwd() / VERSION_FILE_NAME, CONTAINER_VERSION_FILE])

    for version_file_path in candidate_paths:
        try:
            version = version_file_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        if version:
            return version

    return None


def get_github_commit_hash(source_path: pathlib.Path | None = None) -> str:
    """Retrieve the current GitHub commit hash using gitpython."""
    try:
        repo_path = source_path or pathlib.Path(__file__).resolve()
        repo = git.Repo(repo_path, search_parent_directories=True)

        if repo.bare:
            logger.error("Git repository not found in directory: %s", repo.working_tree_dir)
            return UNKNOWN_VERSION

        return repo.head.commit.hexsha
    except Exception as exc:
        logger.error("Error getting GitHub commit hash: %s", exc)
        return UNKNOWN_VERSION


def get_version() -> str:
    return read_version_file() or get_github_commit_hash()
