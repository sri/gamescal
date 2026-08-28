import os
import subprocess
from datetime import datetime
from functools import cache

from django.conf import settings


@cache
def _build_info():
    """Return Git metadata for the running checkout, if it is available."""
    sha = os.getenv("GIT_COMMIT_SHA", "")
    committed_at = os.getenv("GIT_COMMIT_DATE", "")

    if not (sha and committed_at):
        try:
            result = subprocess.run(
                ["git", "show", "-s", "--format=%h%x00%cI", "HEAD"],
                cwd=settings.BASE_DIR,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            sha, committed_at = result.stdout.strip().split("\x00", 1)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    try:
        commit_time = datetime.fromisoformat(committed_at)
    except ValueError:
        return None

    return {
        "sha": sha,
        "committed_at": commit_time,
        "committed_date": commit_time.date(),
    }


def build_info(_request):
    return {"build_info": _build_info()}
