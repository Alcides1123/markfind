"""Find the bookmark files of browsers installed on this computer.

Chromium-based browsers (Chrome, Edge, Brave, Vivaldi, Chromium) keep a
``Bookmarks`` JSON file in each profile folder under their "User Data"
directory, and list profile display names in ``Local State``. Firefox keeps
``places.sqlite`` in each profile folder and lists profiles in
``profiles.ini``.
"""

from __future__ import annotations

import configparser
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CHROMIUM_BROWSERS = ("Chrome", "Edge", "Brave", "Vivaldi", "Chromium")
BROWSERS = (*CHROMIUM_BROWSERS, "Firefox")

# Per platform: browser -> "User Data" directory, relative to a base folder.
_CHROMIUM_DIRS = {
    "win32": {
        "Chrome": "Google/Chrome/User Data",
        "Edge": "Microsoft/Edge/User Data",
        "Brave": "BraveSoftware/Brave-Browser/User Data",
        "Vivaldi": "Vivaldi/User Data",
        "Chromium": "Chromium/User Data",
    },
    "darwin": {
        "Chrome": "Google/Chrome",
        "Edge": "Microsoft Edge",
        "Brave": "BraveSoftware/Brave-Browser",
        "Vivaldi": "Vivaldi",
        "Chromium": "Chromium",
    },
    "linux": {
        "Chrome": "google-chrome",
        "Edge": "microsoft-edge",
        "Brave": "BraveSoftware/Brave-Browser",
        "Vivaldi": "vivaldi",
        "Chromium": "chromium",
    },
}


@dataclass(frozen=True)
class Source:
    """One browser profile's bookmark file."""

    browser: str
    profile: str
    path: Path

    @property
    def name(self) -> str:
        return f"{self.browser} ({self.profile})"


def _platform_key(platform: str) -> str:
    if platform == "win32" or platform == "darwin":
        return platform
    return "linux"  # and other Unix-likes, which use the same XDG layout


def _chromium_base(platform: str, env: Mapping[str, str], home: Path) -> Path:
    if platform == "win32":
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    if platform == "darwin":
        return home / "Library" / "Application Support"
    return Path(env.get("XDG_CONFIG_HOME") or home / ".config")


def _firefox_roots(platform: str, env: Mapping[str, str], home: Path) -> list[Path]:
    if platform == "win32":
        appdata = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        return [appdata / "Mozilla" / "Firefox"]
    if platform == "darwin":
        return [home / "Library" / "Application Support" / "Firefox"]
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return [
        home / ".mozilla" / "firefox",
        config / "mozilla" / "firefox",  # newer releases follow XDG
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    ]


def _chromium_profile_names(user_data: Path) -> dict[str, str]:
    """Profile folder name -> the name shown in the browser, from ``Local State``."""
    try:
        state = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
        cache = state["profile"]["info_cache"]
        return {folder: info["name"] for folder, info in cache.items() if info.get("name")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _chromium_sources(browser: str, user_data: Path) -> list[Source]:
    names = _chromium_profile_names(user_data)
    sources = []
    for bookmarks in sorted(user_data.glob("*/Bookmarks")):
        if bookmarks.is_file():
            folder = bookmarks.parent.name
            sources.append(Source(browser, names.get(folder, folder), bookmarks))
    return sources


def _firefox_profile_names(root: Path) -> dict[Path, str]:
    """Profile folder -> profile name, from ``profiles.ini``."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(root / "profiles.ini", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return {}
    names = {}
    for section in ini.sections():
        if not section.startswith("Profile") or "Path" not in ini[section]:
            continue
        path = Path(ini[section]["Path"])
        if ini[section].get("IsRelative", "1") == "1":
            path = root / path
        names[path] = ini[section].get("Name", path.name)
    return names


def _firefox_sources(root: Path) -> list[Source]:
    names = _firefox_profile_names(root)
    # Windows and macOS keep profiles in a Profiles/ subfolder; Linux doesn't.
    found = [*root.glob("*/places.sqlite"), *root.glob("Profiles/*/places.sqlite")]
    sources = []
    for places in sorted(found):
        if places.is_file():
            folder = places.parent
            sources.append(Source("Firefox", names.get(folder, folder.name), places))
    return sources


def find_sources(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Source]:
    """Bookmark files of every browser profile found, Chromium browsers first.

    The arguments default to the current computer; tests pass fake ones.
    """
    platform = _platform_key(platform or sys.platform)
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    sources: list[Source] = []
    base = _chromium_base(platform, env, home)
    for browser, relative in _CHROMIUM_DIRS[platform].items():
        sources += _chromium_sources(browser, base / relative)
    for root in _firefox_roots(platform, env, home):
        sources += _firefox_sources(root)

    # The same profile can be reachable twice, e.g. through a symlinked folder.
    seen: set[Path] = set()
    unique = []
    for source in sources:
        key = source.path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return unique
