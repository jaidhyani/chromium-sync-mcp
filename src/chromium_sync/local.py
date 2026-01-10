"""Local reader for Chromium/Brave browser data.

Reads sync data directly from local browser profile files:
- History: SQLite database
- Bookmarks: JSON file
- Sessions/Tabs: LevelDB
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import plyvel


# Chromium epoch starts at 1601-01-01, need to convert to Unix epoch
CHROMIUM_EPOCH_OFFSET = 11644473600000000  # microseconds


def chromium_time_to_datetime(chromium_time: int) -> datetime | None:
    """Convert Chromium timestamp to datetime."""
    if not chromium_time:
        return None
    unix_usec = chromium_time - CHROMIUM_EPOCH_OFFSET
    if unix_usec < 0:
        return None
    return datetime.fromtimestamp(unix_usec / 1_000_000)


@dataclass
class Tab:
    """Open tab from a browser session."""
    url: str
    title: str
    favicon_url: str | None = None
    last_active: datetime | None = None


@dataclass
class Device:
    """Device with open tabs."""
    id: str
    name: str
    device_type: str
    tabs: list[Tab] = field(default_factory=list)


@dataclass
class HistoryEntry:
    """Browsing history entry."""
    url: str
    title: str
    visit_time: datetime
    visit_count: int = 1


@dataclass
class Bookmark:
    """Bookmark entry."""
    id: str
    url: str
    title: str
    parent_id: str | None
    date_added: datetime | None
    is_folder: bool


CONFIG_DIR = Path.home() / ".config" / "chromium-sync"
CONFIG_FILE = CONFIG_DIR / "profile"


def get_browser_paths() -> dict[str, list[Path]]:
    """Get all possible profile paths for each browser."""
    home = Path.home()
    return {
        "brave": [
            home / ".config/BraveSoftware/Brave-Browser/Default",
            home / "Library/Application Support/BraveSoftware/Brave-Browser/Default",
            home / "AppData/Local/BraveSoftware/Brave-Browser/User Data/Default",
        ],
        "chrome": [
            home / ".config/google-chrome/Default",
            home / "Library/Application Support/Google/Chrome/Default",
            home / "AppData/Local/Google/Chrome/User Data/Default",
        ],
        "chromium": [
            home / ".config/chromium/Default",
            home / "Library/Application Support/Chromium/Default",
            home / "AppData/Local/Chromium/User Data/Default",
        ],
    }


def find_all_browser_profiles() -> dict[str, Path]:
    """Find all installed Chromium-based browser profiles.

    Returns:
        Dict mapping browser name to profile path for each installed browser.
    """
    found = {}
    for browser, paths in get_browser_paths().items():
        for path in paths:
            if path.exists():
                found[browser] = path
                break
    return found


def load_saved_profile() -> Path | None:
    """Load saved profile path from config file."""
    if CONFIG_FILE.exists():
        try:
            path = Path(CONFIG_FILE.read_text().strip())
            if path.exists():
                return path
        except Exception:
            pass
    return None


def save_profile_choice(path: Path) -> None:
    """Save profile path to config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(str(path))


class MultipleProfilesFound(Exception):
    """Raised when multiple browser profiles are found and user must choose."""

    def __init__(self, profiles: dict[str, Path]):
        self.profiles = profiles
        super().__init__(f"Multiple browser profiles found: {list(profiles.keys())}")


def resolve_browser_profile(env_path: str | None = None) -> Path:
    """Resolve which browser profile to use.

    Priority:
    1. CHROMIUM_PROFILE_PATH env var (passed as env_path)
    2. Saved preference in ~/.config/chromium-sync/profile
    3. Auto-detection (if exactly one browser found)
    4. Raises MultipleProfilesFound if multiple browsers detected

    Args:
        env_path: Value of CHROMIUM_PROFILE_PATH env var, if set.

    Returns:
        Path to the browser profile directory.

    Raises:
        MultipleProfilesFound: Multiple browsers detected, user must choose.
        ValueError: No browser profile found.
    """
    # 1. Environment variable
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        raise ValueError(f"CHROMIUM_PROFILE_PATH does not exist: {env_path}")

    # 2. Saved config
    saved = load_saved_profile()
    if saved:
        return saved

    # 3. Auto-detection
    profiles = find_all_browser_profiles()

    if not profiles:
        raise ValueError(
            "No browser profile found. "
            "Set CHROMIUM_PROFILE_PATH to your browser's profile directory."
        )

    if len(profiles) == 1:
        return next(iter(profiles.values()))

    # 4. Multiple found - need user input
    raise MultipleProfilesFound(profiles)


class LocalReader:
    """Reads browser data from local profile files."""

    def __init__(self, profile_path: Path):
        self.profile_path = profile_path
        self._temp_dir = tempfile.mkdtemp(prefix="chromium_sync_")

    def close(self):
        """Clean up temporary files."""
        if os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir)

    def _copy_locked_file(self, src: Path, name: str) -> Path:
        """Copy a potentially locked file to temp directory."""
        dest = Path(self._temp_dir) / name
        shutil.copy2(src, dest)
        return dest

    def _copy_locked_dir(self, src: Path, name: str) -> Path:
        """Copy a potentially locked directory to temp directory."""
        dest = Path(self._temp_dir) / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        # Remove lock file if present
        lock_file = dest / "LOCK"
        if lock_file.exists():
            lock_file.unlink()
        return dest

    def get_history(
        self, query: str | None = None, limit: int = 100, days_back: int | None = None
    ) -> list[HistoryEntry]:
        """Read browsing history from SQLite database."""
        history_path = self.profile_path / "History"
        if not history_path.exists():
            return []

        temp_db = self._copy_locked_file(history_path, "History.db")

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        sql = """
            SELECT url, title, visit_count, last_visit_time
            FROM urls
            WHERE 1=1
        """
        params: list = []

        if query:
            sql += " AND (url LIKE ? OR title LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])

        if days_back:
            cutoff = datetime.now().timestamp() * 1_000_000 + CHROMIUM_EPOCH_OFFSET
            cutoff -= days_back * 24 * 60 * 60 * 1_000_000
            sql += " AND last_visit_time >= ?"
            params.append(int(cutoff))

        sql += " ORDER BY last_visit_time DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        entries = []
        for url, title, visit_count, last_visit_time in rows:
            visit_dt = chromium_time_to_datetime(last_visit_time)
            if visit_dt:
                entries.append(HistoryEntry(
                    url=url or "",
                    title=title or "",
                    visit_time=visit_dt,
                    visit_count=visit_count or 1,
                ))

        return entries

    def get_bookmarks(self, folder_id: str | None = None) -> list[Bookmark]:
        """Read bookmarks from JSON file."""
        bookmarks_path = self.profile_path / "Bookmarks"
        if not bookmarks_path.exists():
            return []

        with open(bookmarks_path) as f:
            data = json.load(f)

        bookmarks = []

        def process_node(node: dict, parent_id: str | None = None):
            node_id = node.get("id", node.get("guid", ""))
            node_type = node.get("type", "")

            is_folder = node_type == "folder"
            url = node.get("url", "")
            title = node.get("name", "")

            date_added_str = node.get("date_added", "0")
            date_added = chromium_time_to_datetime(int(date_added_str)) if date_added_str else None

            # Skip if filtering by folder and this isn't in that folder
            if folder_id is None or parent_id == folder_id:
                bookmarks.append(Bookmark(
                    id=node_id,
                    url=url,
                    title=title,
                    parent_id=parent_id,
                    date_added=date_added,
                    is_folder=is_folder,
                ))

            # Process children
            for child in node.get("children", []):
                process_node(child, node_id)

        roots = data.get("roots", {})
        for root_node in roots.values():
            if isinstance(root_node, dict):
                process_node(root_node, None)

        return bookmarks

    def search_bookmarks(self, query: str) -> list[Bookmark]:
        """Search bookmarks by title or URL."""
        all_bookmarks = self.get_bookmarks()
        query_lower = query.lower()
        return [
            b for b in all_bookmarks
            if query_lower in b.title.lower() or query_lower in b.url.lower()
        ]

    def get_tabs(self) -> list[Device]:
        """Read open tabs from all synced devices via LevelDB."""
        sync_data_path = self.profile_path / "Sync Data" / "LevelDB"
        if not sync_data_path.exists():
            return []

        temp_leveldb = self._copy_locked_dir(sync_data_path, "SyncLevelDB")

        try:
            db = plyvel.DB(str(temp_leveldb), create_if_missing=False)
        except Exception:
            return []

        # Build device ID -> name mapping
        devices_map: dict[str, Device] = {}

        for key, value in db:
            if key.startswith(b"device_info-dt-"):
                device_id = key.split(b"-dt-")[1].decode("utf-8")
                device_info = self._parse_device_info(value, device_id)
                if device_info:
                    devices_map[device_id] = device_info

        # Extract sessions (tabs) for each device
        for key, value in db:
            if key.startswith(b"sessions-dt-") and b"GlobalMetadata" not in key:
                self._parse_session_entry(value, devices_map)

        db.close()

        # Filter out devices with no tabs
        return [d for d in devices_map.values() if d.tabs]

    def _parse_device_info(self, value: bytes, device_id: str) -> Device | None:
        """Parse device info from LevelDB value."""
        # Extract readable strings to find device name and type
        strings = re.findall(rb"[\x20-\x7e]{3,80}", value)

        name = None
        device_type = "unknown"

        for s in strings[1:15]:  # Check more strings
            decoded = s.decode("utf-8", errors="replace")
            if decoded == device_id:
                continue

            # Check for device type indicators in Chrome user agent strings
            if "Chrome" in decoded or "ANDROID" in decoded or "WIN" in decoded:
                if "ANDROID" in decoded:
                    device_type = "phone"
                elif "WIN" in decoded:
                    device_type = "windows"
                elif "LINUX" in decoded:
                    device_type = "linux"
                elif "MAC" in decoded:
                    device_type = "mac"

            # Get device name (first non-ID, non-Chrome string)
            if not name and len(decoded) > 2 and "Chrome" not in decoded:
                name = decoded

        return Device(
            id=device_id,
            name=name or device_id,
            device_type=device_type,
            tabs=[],
        )

    def _parse_session_entry(self, value: bytes, devices_map: dict[str, Device]):
        """Parse a session entry and add tabs to the appropriate device."""
        # Find which device this session belongs to
        for device_id, device in devices_map.items():
            if device_id.encode() in value:
                # Extract URLs and titles from the protobuf data
                urls = re.findall(rb"https?://[^\x00-\x1f\x7f-\xff\"<>]+", value)

                for url in urls:
                    url_str = url.decode("utf-8", errors="replace")
                    # Skip favicon URLs and other non-tab URLs
                    if "/favicon" in url_str or "/s/desktop/" in url_str:
                        continue
                    if url_str.startswith("https://abs.twimg.com"):
                        continue

                    # Avoid duplicates
                    existing_urls = {t.url for t in device.tabs}
                    if url_str not in existing_urls:
                        device.tabs.append(Tab(
                            url=url_str,
                            title="",  # Title extraction is harder from protobuf
                            favicon_url=None,
                            last_active=None,
                        ))
                break
