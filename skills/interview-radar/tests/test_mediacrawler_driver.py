import json
import os
import sys
import time
from pathlib import Path

import pytest

from scripts.scrape.mediacrawler_driver import (
    MediaCrawlerDriver,
    MediaCrawlerNotInstalledError,
    MediaCrawlerScrapeError,
    _Result,
    _default_runner,
    _detect_home,
)


def _make_fake_home(root: Path) -> Path:
    home = root / "fake_mc"
    (home / "data" / "xhs" / "json").mkdir(parents=True)
    (home / "main.py").write_text("# fake mediacrawler entrypoint")
    return home


def test_init_raises_when_home_missing(tmp_path: Path):
    with pytest.raises(MediaCrawlerNotInstalledError):
        MediaCrawlerDriver(home=tmp_path / "does-not-exist")


def test_detect_home_uses_env_var(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_HOME", str(tmp_path))
    assert _detect_home() == tmp_path


def test_detect_home_defaults_to_dot_mediacrawler(monkeypatch):
    monkeypatch.delenv("MEDIACRAWLER_HOME", raising=False)
    assert _detect_home() == Path.home() / ".mediacrawler"


def test_default_runner_replaces_invalid_output_bytes(tmp_path: Path):
    result = _default_runner(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.buffer.write(b'bad\\xfftail')",
        ],
        tmp_path,
        5,
    )

    assert result.returncode == 0
    assert "bad" in result.stderr
    assert "tail" in result.stderr


def test_scrape_returns_newest_new_file(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"

    # Pre-existing file (should NOT be returned)
    old = out_dir / "search_contents_2026-01-01.json"
    old.write_text("[]")

    def fake_runner(cmd, cwd, timeout):
        # Simulate MediaCrawler writing a new output file
        new_file = out_dir / "search_contents_2026-06-01.json"
        new_file.write_text("[]")
        # Make sure it has a strictly newer mtime than `old`
        os.utime(new_file, (time.time() + 10, time.time() + 10))
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    result = driver.scrape_xhs(["AI 应用开发"])

    assert result.name == "search_contents_2026-06-01.json"
    assert result != old


def test_scrape_returns_existing_file_updated_by_mediacrawler(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"
    output = out_dir / "search_contents_2026-06-03.json"
    output.write_text("[]")
    old_mtime = time.time() - 100
    os.utime(output, (old_mtime, old_mtime))

    def fake_runner(cmd, cwd, timeout):
        output.write_text('[{"note_id": "fresh"}]')
        new_mtime = time.time() + 10
        os.utime(output, (new_mtime, new_mtime))
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    result = driver.scrape_xhs(["AI 应用开发"])

    assert result == output


def test_scrape_raises_on_nonzero_exit(tmp_path: Path):
    home = _make_fake_home(tmp_path)

    def fake_runner(cmd, cwd, timeout):
        return _Result(returncode=1, stderr="login expired, scan QR again")

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    with pytest.raises(MediaCrawlerScrapeError) as exc_info:
        driver.scrape_xhs(["foo"])
    assert "login expired" in str(exc_info.value)


def test_scrape_raises_when_no_new_file(tmp_path: Path):
    home = _make_fake_home(tmp_path)

    def fake_runner(cmd, cwd, timeout):
        return _Result(returncode=0)  # exits clean but writes nothing

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    with pytest.raises(MediaCrawlerScrapeError) as exc_info:
        driver.scrape_xhs(["foo"])
    assert "no new file" in str(exc_info.value).lower() or "schema" in str(exc_info.value).lower()


def test_scrape_passes_keywords_as_comma_joined(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"

    seen_cmds: list[list[str]] = []

    def fake_runner(cmd, cwd, timeout):
        seen_cmds.append(cmd)
        (out_dir / "search_contents_test.json").write_text("[]")
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    driver.scrape_xhs(["Agent", "RAG", "LangChain"])

    assert len(seen_cmds) == 1
    cmd = seen_cmds[0]
    kw_idx = cmd.index("--keywords")
    assert cmd[kw_idx + 1] == "Agent,RAG,LangChain"


def test_scrape_forces_json_save_option(tmp_path: Path):
    # MediaCrawler's default save_data_option is "jsonl"; the driver must
    # override to "json" so our adapter (which reads a JSON array) works.
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"

    seen_cmd: list[list[str]] = []

    def fake_runner(cmd, cwd, timeout):
        seen_cmd.append(cmd)
        (out_dir / "search_contents_test.json").write_text("[]")
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    driver.scrape_xhs(["foo"])

    cmd = seen_cmd[0]
    flag_idx = cmd.index("--save_data_option")
    assert cmd[flag_idx + 1] == "json"


def test_scrape_disables_comment_crawling(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"

    seen_cmd: list[list[str]] = []

    def fake_runner(cmd, cwd, timeout):
        seen_cmd.append(cmd)
        (out_dir / "search_contents_test.json").write_text("[]")
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    driver.scrape_xhs(["foo"])

    cmd = seen_cmd[0]
    flag_idx = cmd.index("--get_comment")
    assert cmd[flag_idx + 1] == "no"


def test_python_executable_prefers_local_venv(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    venv_python = home / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\nexit 0\n")
    venv_python.chmod(0o755)

    driver = MediaCrawlerDriver(home=home, runner=lambda *a, **k: _Result(0))
    assert driver.python_executable == str(venv_python)


def test_python_executable_falls_back_to_system(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    # No venv created
    driver = MediaCrawlerDriver(home=home, runner=lambda *a, **k: _Result(0))
    assert driver.python_executable == "python"


def test_python_executable_can_be_overridden(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    driver = MediaCrawlerDriver(
        home=home,
        runner=lambda *a, **k: _Result(0),
        python_executable="/custom/python",
    )
    assert driver.python_executable == "/custom/python"


def test_scrape_requires_nonempty_keywords(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    driver = MediaCrawlerDriver(home=home, runner=lambda *a, **k: _Result(0))
    with pytest.raises(ValueError):
        driver.scrape_xhs([])


def test_scrape_login_type_defaults_to_qrcode(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"

    seen: list[list[str]] = []

    def fake_runner(cmd, cwd, timeout):
        seen.append(cmd)
        (out_dir / "search_contents_x.json").write_text("[]")
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    driver.scrape_xhs(["foo"])

    cmd = seen[0]
    lt_idx = cmd.index("--lt")
    assert cmd[lt_idx + 1] == "qrcode"


def test_scrape_login_type_cookie_passes_through(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    out_dir = home / "data" / "xhs" / "json"

    seen: list[list[str]] = []

    def fake_runner(cmd, cwd, timeout):
        seen.append(cmd)
        (out_dir / "search_contents_x.json").write_text("[]")
        return _Result(returncode=0)

    driver = MediaCrawlerDriver(home=home, runner=fake_runner)
    driver.scrape_xhs(["foo"], login_type="cookie")

    cmd = seen[0]
    lt_idx = cmd.index("--lt")
    assert cmd[lt_idx + 1] == "cookie"


def test_scrape_rejects_unknown_login_type(tmp_path: Path):
    home = _make_fake_home(tmp_path)
    driver = MediaCrawlerDriver(home=home, runner=lambda *a, **k: _Result(0))
    with pytest.raises(ValueError):
        driver.scrape_xhs(["foo"], login_type="biometric")
