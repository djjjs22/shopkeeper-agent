"""Shell-out driver for MediaCrawler 小红书 search.

Verified against NanmiCoder/MediaCrawler @ 2026-06-01. Key facts driving
the implementation:

- MediaCrawler CLI: `python main.py --platform xhs --lt qrcode --type search
  --keywords "a,b,c" --save_data_option json`
- Default `SAVE_DATA_OPTION` is `jsonl`; we override to `json` because our
  `normalize_xhs.py` adapter consumes JSON arrays.
- Output file (relative to MediaCrawler home):
  `data/xhs/json/search_<item_type>_<YYYY-MM-DD>.json`. For our crawler type
  (`search`) and item type (`contents`) the pattern is
  `data/xhs/json/search_contents_*.json`.
- MediaCrawler needs Playwright + ~30 deps; users typically install them
  into a venv at `<home>/venv/`. We auto-detect that venv's python and use
  it for the shell-out — system `python` won't have the deps.
- Login state is cached by MediaCrawler itself (in its own dir) once the
  user scans the QR code; subsequent runs reuse it until expiry.

If MediaCrawler changes its CLI or output layout, only this file needs to
be touched.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class MediaCrawlerNotInstalledError(FileNotFoundError):
    pass


class MediaCrawlerScrapeError(RuntimeError):
    pass


@dataclass
class _Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(cmd: list[str], cwd: Path, timeout: int) -> _Result:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return _Result(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _detect_home() -> Path:
    env = os.environ.get("MEDIACRAWLER_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".mediacrawler"


def _detect_python(home: Path) -> str:
    """Find the python executable that has MediaCrawler's deps installed.

    Priority:
    1. `<home>/venv/bin/python` — the conventional path from MediaCrawler's
       README pip-install instructions
    2. `<home>/.venv/bin/python` — alternative venv name
    3. system `python` — last-resort fallback (likely missing deps; we still
       try so the error from MediaCrawler is surfaced to the user)
    """
    for candidate in (home / "venv" / "bin" / "python", home / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return "python"


class MediaCrawlerDriver:
    """Drives MediaCrawler from this skill. Login is the user's job; everything
    after login (scrape + locate output JSON) is automated by this driver."""

    def __init__(
        self,
        home: Path | None = None,
        runner: Callable[[list[str], Path, int], _Result] | None = None,
        python_executable: str | None = None,
    ):
        self.home = (home or _detect_home()).expanduser()
        if not self.home.is_dir():
            raise MediaCrawlerNotInstalledError(
                f"MediaCrawler not found at {self.home}. "
                "Install it (https://github.com/NanmiCoder/MediaCrawler) and "
                "either set $MEDIACRAWLER_HOME or clone to ~/.mediacrawler/."
            )
        self.runner = runner or _default_runner
        self.python_executable = python_executable or _detect_python(self.home)

    @property
    def output_dir(self) -> Path:
        return self.home / "data" / "xhs" / "json"

    def scrape_xhs(
        self,
        keywords: list[str],
        timeout: int = 600,
        login_type: str = "qrcode",
    ) -> Path:
        """Run MediaCrawler xhs search and return the path to the freshly-produced
        notes JSON.

        login_type:
        - "qrcode" (default): expects MediaCrawler to display a QR code, user
          must have valid cached login state from a prior scan, OR be willing
          to scan now. Currently unreliable due to Xiaohongshu anti-bot.
        - "cookie": MediaCrawler reads `config.COOKIES` (set to
          "web_session=<value>") and injects it into the browser context.
          More reliable but requires user to extract cookie from a normal
          logged-in browser session.
        - "phone": SMS verification (not supported by this driver).

        Raises MediaCrawlerScrapeError on non-zero exit, missing output, or any
        other observable failure.
        """
        if not keywords:
            raise ValueError("keywords must be non-empty")
        if login_type not in ("qrcode", "cookie", "phone"):
            raise ValueError(f"unsupported login_type: {login_type}")

        cmd = [
            self.python_executable,
            str(self.home / "main.py"),
            "--platform",
            "xhs",
            "--lt",
            login_type,
            "--type",
            "search",
            "--keywords",
            ",".join(keywords),
            # Override MediaCrawler's default jsonl output so our adapter can
            # consume the JSON array directly.
            "--save_data_option",
            "json",
            # InterviewRadar consumes notes, not comments. Disabling comments
            # keeps runs small and reduces unnecessary platform requests.
            "--get_comment",
            "no",
        ]

        out_dir = self.output_dir
        before_mtimes = (
            {p: p.stat().st_mtime_ns for p in out_dir.glob("search_contents_*.json")}
            if out_dir.is_dir()
            else {}
        )

        try:
            result = self.runner(cmd, self.home, timeout)
        except subprocess.TimeoutExpired as exc:
            raise MediaCrawlerScrapeError(
                f"MediaCrawler did not finish within {timeout}s; "
                "anti-bot challenge or hanging session?"
            ) from exc

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "")[-500:]
            raise MediaCrawlerScrapeError(
                f"MediaCrawler exited {result.returncode}. "
                "Common causes: login expired (re-scan QR), anti-bot block, "
                f"or CLI/schema change. tail: {tail}"
            )

        if not out_dir.is_dir():
            raise MediaCrawlerScrapeError(
                f"MediaCrawler exited 0 but output dir {out_dir} does not exist. "
                "Output schema may have changed."
            )

        candidates = sorted(
            (
                p
                for p in out_dir.glob("search_contents_*.json")
                if p not in before_mtimes or p.stat().st_mtime_ns > before_mtimes[p]
            ),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        if not candidates:
            raise MediaCrawlerScrapeError(
                f"MediaCrawler exited 0 but produced no new file in {out_dir}. "
                "Output schema may have changed."
            )
        return candidates[0]
