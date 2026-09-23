"""Installed-wheel checks. Run only with a clean environment and temporary XDG paths."""

from __future__ import annotations

import asyncio
import fcntl
import importlib.util
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from importlib import metadata, resources
from pathlib import Path

from textual.widgets import ListView

from dndref import __version__
from dndref.config import ApplicationPaths
from dndref.importer import import_dataset, load_dataset
from dndref.search import SearchMode, SearchQuery, SearchService
from dndref.storage.database import MIGRATIONS_DIR, Database
from dndref.ui.app import BrowserApp
from dndref.ui.screens import (
    AboutScreen,
    ChoiceScreen,
    FilterScreen,
    HelpScreen,
    SourceBrowserScreen,
)


def cli(*arguments: str, expected: int = 0) -> str:
    result = subprocess.run(
        [str(Path(sys.executable).with_name("dndref")), *arguments],
        cwd=tempfile.gettempdir(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected, (arguments, result.stdout, result.stderr)
    return result.stdout + result.stderr


def dumb_terminal_smoke() -> None:
    primary, secondary = pty.openpty()
    fcntl.ioctl(secondary, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    process = subprocess.Popen(
        [str(Path(sys.executable).with_name("dndref")), "--images", "auto"],
        stdin=secondary,
        stdout=secondary,
        stderr=secondary,
        cwd=tempfile.gettempdir(),
        start_new_session=True,
    )
    os.close(secondary)
    output = bytearray()
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([primary], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(primary, 65536))
                except OSError:
                    break
            if time.monotonic() > deadline - 2:
                os.write(primary, b"\x11")  # Ctrl+Q
        assert process.wait(timeout=5) == 0
        assert b"\x1b_G" not in output and b"\x1bP" not in output
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        os.close(primary)


async def tui_smoke(database: Database) -> None:
    for size in ((140, 40), (100, 30), (80, 24), (60, 20)):
        app = BrowserApp(database)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.35)
            assert app.state.results, size
            assert app.query_one("#image-panel").display is False
            for key, category in (
                ("1", "items"),
                ("2", "spells"),
                ("3", "feats"),
                ("4", "classes"),
            ):
                await pilot.press(key)
                await pilot.pause(0.2)
                assert app.category.value == category and app.state.results, (size, category)
            await pilot.press("ctrl+f", "w", "i", "z", "a", "r", "d", "escape")
            await pilot.pause(0.25)
            assert app.state.query == "wizard" and app.state.results
            await pilot.press("f2")
            await pilot.pause(0.25)
            assert app.mode is SearchMode.ALL_TEXT and app.state.results
            await pilot.press("ctrl+f", "ctrl+a", "backspace", "escape")
            await pilot.pause(0.2)
            await pilot.press("e")
            assert isinstance(app.screen, FilterScreen)
            await pilot.press("escape", "s")
            assert isinstance(app.screen, FilterScreen)
            await pilot.press("escape", "p")
            assert isinstance(app.screen, ChoiceScreen)
            await pilot.press("escape", "g")
            assert app.group_alternate_sources is False
            await pilot.press("g", "b")
            assert isinstance(app.screen, SourceBrowserScreen)
            await pilot.press("escape", "?")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            app.action_show_about()
            await pilot.pause()
            assert isinstance(app.screen, AboutScreen)
            await pilot.press("escape")
            if size == (140, 40):
                await pilot.press("2")
                app.query_one("#search-input").value = "Fireball"
                await pilot.pause(0.4)
                assert app.state.results
                await pilot.press("v")
                assert isinstance(app.screen, ChoiceScreen)
                before = app._selected_variant_id
                await pilot.press("down", "enter")
                await pilot.pause(0.2)
                assert app._selected_variant_id != before
                app.query_one("#result-list", ListView).focus()
                await pilot.press("p")
                screen = app.screen
                assert isinstance(screen, ChoiceScreen)
                screen.query_one("#choice-list", ListView).index = len(screen.labels) - 1
                await pilot.press("enter")
                await pilot.pause(0.2)
                assert app.state.editions == ()
                await pilot.press("e", "down", "space", "enter")
                await pilot.pause(0.2)
                assert app.state.editions == ("2024",)
                await pilot.press("s", "down", "space", "enter")
                await pilot.pause(0.2)
                assert app.state.sources
                await pilot.press("b")
                assert isinstance(app.screen, SourceBrowserScreen)
                await pilot.press("enter")
                assert app.screen.stage == "categories"
                await pilot.press("escape", "escape")
            await pilot.press("ctrl+q")


def main() -> None:
    mode, fixture = sys.argv[1:]
    assert Path(__import__("dndref").__file__).is_relative_to(sys.prefix)
    assert metadata.version("dnd-reference") == __version__ == "0.1.0"
    assert bool(importlib.util.find_spec("PIL")) == (mode == "images")
    assert bool(importlib.util.find_spec("textual_image")) == (mode == "images")
    if mode == "images":
        import PIL.Image  # noqa: F401
        import textual_image.widget  # noqa: F401
    assert cli("--version").strip() == f"dndref {__version__}"
    assert "validate" in cli("--help")
    assert "dndref 0.1.0" in subprocess.check_output(
        [sys.executable, "-m", "dndref", "--version"],
        text=True,
        cwd=tempfile.gettempdir(),
    )
    assert "error" in cli("validate", "/missing/dndref-dataset", expected=2)
    assert "error" in cli("import", "/missing/dndref-dataset", expected=2)
    assert "foundation initialized" in cli("--images", "off")
    assert "foundation initialized" in cli("--images", "auto")
    assert "foundation initialized" in subprocess.check_output(
        [sys.executable, "-m", "dndref", "--images", "off"],
        text=True,
        cwd=tempfile.gettempdir(),
    )

    paths = ApplicationPaths.default()
    assert paths.database_path.exists()
    assert all(
        path.is_dir() for path in (paths.config_dir, paths.data_dir, paths.cache_dir, paths.log_dir)
    )
    database = Database(paths.database_path)
    assert database.initialize() == ()
    with database.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations")] == [
            1,
            2,
            3,
            4,
            5,
        ]
        assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 4036
        assert connection.execute("SELECT COUNT(*) FROM entry_search").fetchone()[0] == 4036
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    service = SearchService(database)
    assert service.search(SearchQuery("items", "Longsword")).total_count
    assert service.search(SearchQuery("spells", "Fireball")).total_count

    for pack in ("srd-5.2.1", "official-5etools-2024"):
        with resources.as_file(resources.files("dndref").joinpath("datasets", pack)) as path:
            assert path.is_dir()
            assert "valid" in cli("validate", str(path)).lower()
            assert "Import plan" in cli("import", str(path), "--dry-run")

    fixture_path = Path(fixture)
    assert "valid" in cli("validate", str(fixture_path)).lower()
    assert "Import plan" in cli("import", str(fixture_path), "--dry-run")
    assert "8 added" in cli("import", str(fixture_path))
    assert "no changes" in cli("import", str(fixture_path))
    assert service.search(SearchQuery("items", "Rapier")).total_count
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 3

    # Reproduce an installed database at the immediately preceding migration level.
    with tempfile.TemporaryDirectory() as temporary:
        old_migrations = Path(temporary) / "old-migrations"
        old_migrations.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))[:3]:
            shutil.copy2(path, old_migrations / path.name)
        older = Database(Path(temporary) / "older.sqlite3", old_migrations)
        assert older.initialize() == (1, 2, 3)
        import_dataset(older, load_dataset(fixture_path))
        upgraded = Database(older.path)
        assert upgraded.initialize() == (4, 5)
        assert upgraded.initialize() == ()
        assert SearchService(upgraded).search(SearchQuery("items", "Rapier")).total_count
        with upgraded.connection() as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM entry_search").fetchone()[0] == 8

    if mode == "base":
        dumb_terminal_smoke()
    asyncio.run(tui_smoke(database))
    print(f"{mode} installed-wheel smoke passed")


if __name__ == "__main__":
    main()
