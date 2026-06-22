from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from orxtra.overseer._learning import check_staleness, filter_stale_lessons

TS_INITIAL = "2020-01-01T00:00:00+00:00"
TS_LESSON = "2022-01-01T00:00:00+00:00"
TS_MODIFY = "2025-01-01T00:00:00+00:00"


def git_commit(repo_dir: Path, message: str, timestamp: str) -> None:
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": timestamp,
        "GIT_AUTHOR_DATE": timestamp,
    }
    subprocess.run(  # noqa: S603
        ["git", "commit", "-m", message],  # noqa: S607
        cwd=repo_dir, check=True, capture_output=True, env=env,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init"], cwd=repo, check=True, capture_output=True,  # noqa: S607
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],  # noqa: S607
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],  # noqa: S607
        cwd=repo, check=True, capture_output=True,
    )
    return repo


@pytest.mark.asyncio
async def test_file_unchanged_not_stale(git_repo: Path) -> None:
    f = git_repo / "foo.txt"
    f.write_text("initial", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "foo.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "add foo", TS_INITIAL)

    result = await check_staleness(["foo.txt"], git_repo, TS_LESSON)
    assert result is False


@pytest.mark.asyncio
async def test_file_changed_is_stale(git_repo: Path) -> None:
    f = git_repo / "foo.txt"
    f.write_text("initial", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "foo.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "add foo", TS_INITIAL)

    f.write_text("modified", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "foo.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "modify foo", TS_MODIFY)

    result = await check_staleness(["foo.txt"], git_repo, TS_LESSON)
    assert result is True


@pytest.mark.asyncio
async def test_multiple_source_files_one_changed_is_stale(
    git_repo: Path,
) -> None:
    a = git_repo / "a.txt"
    b = git_repo / "b.txt"
    a.write_text("a initial", encoding="utf-8")
    b.write_text("b initial", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "a.txt", "b.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "add a and b", TS_INITIAL)

    b.write_text("b modified", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "b.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "modify b", TS_MODIFY)

    result = await check_staleness(
        ["a.txt", "b.txt"], git_repo, TS_LESSON,
    )
    assert result is True


@pytest.mark.asyncio
async def test_no_source_files_not_stale(git_repo: Path) -> None:
    result = await check_staleness([], git_repo, TS_LESSON)
    assert result is False


@pytest.mark.asyncio
async def test_non_git_directory_not_stale() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "foo.txt").write_text("hello", encoding="utf-8")
        result = await check_staleness(["foo.txt"], d, TS_LESSON)
    assert result is False


@pytest.mark.asyncio
async def test_file_deleted_is_stale(git_repo: Path) -> None:
    f = git_repo / "gone.txt"
    f.write_text("will be deleted", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "gone.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "add gone", TS_INITIAL)
    f.unlink()

    result = await check_staleness(["gone.txt"], git_repo, TS_LESSON)
    assert result is True


@pytest.mark.asyncio
async def test_filter_stale_lessons_splits_correctly(
    git_repo: Path,
) -> None:
    fresh_file = git_repo / "fresh.txt"
    fresh_file.write_text("fresh content", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "fresh.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "add fresh", TS_INITIAL)

    stale_file = git_repo / "stale.txt"
    stale_file.write_text("stale initial", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "stale.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "add stale", TS_INITIAL)

    stale_file.write_text("stale modified", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "add", "stale.txt"],  # noqa: S607
        cwd=git_repo, check=True, capture_output=True,
    )
    git_commit(git_repo, "modify stale", TS_MODIFY)

    lessons = [
        {
            "source_file": "fresh.txt",
            "created_at": TS_LESSON,
        },
        {
            "source_file": "stale.txt",
            "created_at": TS_LESSON,
        },
        {
            "source_file": None,
            "created_at": TS_LESSON,
        },
    ]

    fresh, stale = await filter_stale_lessons(lessons, git_repo)
    assert len(fresh) == 2
    assert len(stale) == 1
    assert stale[0]["source_file"] == "stale.txt"
    fresh_sources = [lesson.get("source_file") for lesson in fresh]
    assert "fresh.txt" in fresh_sources
    assert None in fresh_sources


@pytest.mark.asyncio
async def test_filter_stale_lessons_empty_list(git_repo: Path) -> None:
    fresh, stale = await filter_stale_lessons([], git_repo)
    assert fresh == []
    assert stale == []


@pytest.mark.asyncio
async def test_permanent_lesson_always_fresh(git_repo: Path) -> None:
    lesson = {
        "created_at": TS_LESSON,
    }
    fresh, stale = await filter_stale_lessons([lesson], git_repo)
    assert len(fresh) == 1
    assert len(stale) == 0
    assert fresh[0] is lesson
