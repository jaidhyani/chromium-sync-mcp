"""MCP server for Chromium Sync.

Exposes browser data (tabs, history, bookmarks) as MCP tools for Claude Code.
Reads directly from local browser profile files.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .local import (
    CONFIG_FILE,
    Bookmark,
    Device,
    HistoryEntry,
    LocalReader,
    MultipleProfilesFound,
    Tab,
    find_all_browser_profiles,
    resolve_browser_profile,
    save_profile_choice,
)

# Create the MCP server
app = Server("chromium-sync")

# Global state
_reader: LocalReader | None = None
_pending_profiles: dict[str, Path] | None = None


def get_reader() -> LocalReader | None:
    """Get the reader, or None if profile selection is pending."""
    global _reader, _pending_profiles

    if _reader is not None:
        return _reader

    env_path = os.environ.get("CHROMIUM_PROFILE_PATH")

    try:
        profile_path = resolve_browser_profile(env_path)
        _reader = LocalReader(profile_path)
        return _reader
    except MultipleProfilesFound as e:
        _pending_profiles = e.profiles
        return None


def select_browser(browser: str, save_default: bool = False) -> str:
    """Select a browser and optionally save as default."""
    global _reader, _pending_profiles

    profiles = _pending_profiles or find_all_browser_profiles()
    browser_lower = browser.lower()

    if browser_lower not in profiles:
        available = ", ".join(profiles.keys())
        return f"Unknown browser '{browser}'. Available: {available}"

    profile_path = profiles[browser_lower]

    if save_default:
        save_profile_choice(profile_path)

    _reader = LocalReader(profile_path)
    _pending_profiles = None

    saved_msg = f" Saved to {CONFIG_FILE}" if save_default else ""
    return f"Selected {browser} ({profile_path}).{saved_msg}"


def format_profile_selection_prompt(profiles: dict[str, Path]) -> str:
    """Format the prompt for selecting a browser profile."""
    lines = [
        "Multiple browser profiles detected:\n",
    ]
    for name, path in profiles.items():
        lines.append(f"  - **{name}**: {path}")

    lines.append("\nUse the `select_browser` tool to choose one.")
    lines.append("Set `save_default: true` to remember your choice.")
    return "\n".join(lines)


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available sync tools."""
    return [
        Tool(
            name="select_browser",
            description=(
                "Select which browser to use when multiple are installed. "
                "Use this when prompted to choose between browsers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "browser": {
                        "type": "string",
                        "description": "Browser to use: 'chrome', 'chromium', or 'brave'",
                    },
                    "save_default": {
                        "type": "boolean",
                        "description": "Save this choice as the default for future sessions",
                        "default": False,
                    },
                },
                "required": ["browser"],
            },
        ),
        Tool(
            name="set_profile_path",
            description="Manually set the browser profile path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the browser profile directory",
                    },
                    "save_default": {
                        "type": "boolean",
                        "description": "Save this path as the default for future sessions",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="get_tabs_all_devices",
            description=(
                "Get open tabs from all synced devices. "
                "Returns a list of devices with their open tabs."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_tabs_local",
            description="Get open tabs from the current local browser session.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_history",
            description="Search browsing history. Supports text search and date filtering.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in URLs and titles. Optional.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return. Default 100.",
                        "default": 100,
                    },
                    "days_back": {
                        "type": "integer",
                        "description": "Only return history from the last N days. Optional.",
                    },
                },
            },
        ),
        Tool(
            name="get_bookmarks",
            description="Get bookmarks. Optionally filter by parent folder ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": (
                            "Parent folder ID to filter by. "
                            "Optional - returns all bookmarks if not specified."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="search_bookmarks",
            description="Search bookmarks by title or URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in bookmark titles and URLs.",
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    global _pending_profiles, _reader

    # Handle browser selection
    if name == "select_browser":
        browser = arguments.get("browser", "")
        save_default = arguments.get("save_default", False)
        result = select_browser(browser, save_default)
        return [TextContent(type="text", text=result)]

    # Handle manual profile path setting
    if name == "set_profile_path":
        path_str = arguments.get("path", "")
        save_default = arguments.get("save_default", False)
        path = Path(path_str)
        if not path.exists():
            return [TextContent(type="text", text=f"Path does not exist: {path}")]
        if save_default:
            save_profile_choice(path)
        _reader = LocalReader(path)
        _pending_profiles = None
        saved_msg = f" Saved to {CONFIG_FILE}" if save_default else ""
        return [TextContent(type="text", text=f"Set profile path to {path}.{saved_msg}")]

    # For all other tools, we need a reader
    reader = get_reader()

    if reader is None and _pending_profiles:
        prompt = format_profile_selection_prompt(_pending_profiles)
        return [TextContent(type="text", text=prompt)]

    if reader is None:
        return [TextContent(type="text", text="No browser profile found.")]

    if name == "get_tabs_all_devices":
        devices = reader.get_tabs()
        result = format_devices(devices)
        return [TextContent(type="text", text=result)]

    elif name == "get_tabs_local":
        tabs = reader.get_local_tabs()
        result = format_local_tabs(tabs)
        return [TextContent(type="text", text=result)]

    elif name == "get_history":
        query = arguments.get("query")
        limit = arguments.get("limit", 100)
        days_back = arguments.get("days_back")
        history = reader.get_history(query=query, limit=limit, days_back=days_back)
        result = format_history(history)
        return [TextContent(type="text", text=result)]

    elif name == "get_bookmarks":
        folder = arguments.get("folder")
        bookmarks = reader.get_bookmarks(folder_id=folder)
        result = format_bookmarks(bookmarks)
        return [TextContent(type="text", text=result)]

    elif name == "search_bookmarks":
        query = arguments.get("query", "")
        bookmarks = reader.search_bookmarks(query)
        result = format_bookmarks(bookmarks)
        return [TextContent(type="text", text=result)]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


def format_devices(devices: list[Device]) -> str:
    """Format devices and tabs for display."""
    if not devices:
        return "No devices with open tabs found."

    lines = []
    for device in devices:
        lines.append(f"\n## {device.name} ({device.device_type})")
        if not device.tabs:
            lines.append("  No open tabs")
        else:
            for tab in device.tabs:
                if tab.title:
                    lines.append(f"  - [{tab.title}]({tab.url})")
                else:
                    lines.append(f"  - {tab.url}")

    return "\n".join(lines)


def format_local_tabs(tabs: list[Tab]) -> str:
    """Format local tabs for display."""
    if not tabs:
        return "No open tabs found in local session."

    lines = [f"Found {len(tabs)} open tabs:\n"]
    for tab in tabs:
        if tab.title:
            lines.append(f"- [{tab.title}]({tab.url})")
        else:
            lines.append(f"- {tab.url}")

    return "\n".join(lines)


def format_history(history: list[HistoryEntry]) -> str:
    """Format history entries for display."""
    if not history:
        return "No history entries found."

    lines = [f"Found {len(history)} history entries:\n"]
    for entry in history:
        time_str = entry.visit_time.strftime("%Y-%m-%d %H:%M")
        visits = f" ({entry.visit_count} visits)" if entry.visit_count > 1 else ""
        lines.append(f"- [{entry.title}]({entry.url}) - {time_str}{visits}")

    return "\n".join(lines)


def format_bookmarks(bookmarks: list[Bookmark]) -> str:
    """Format bookmarks for display."""
    if not bookmarks:
        return "No bookmarks found."

    lines = [f"Found {len(bookmarks)} bookmarks:\n"]
    for bookmark in bookmarks:
        if bookmark.is_folder:
            lines.append(f"- [folder] {bookmark.title} (id: {bookmark.id})")
        else:
            lines.append(f"- [{bookmark.title}]({bookmark.url})")

    return "\n".join(lines)


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    """Entry point for the MCP server."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if _reader:
            _reader.close()


if __name__ == "__main__":
    main()
