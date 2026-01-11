# chromium-sync-mcp

MCP server for accessing browser data (tabs, history, bookmarks) from Chromium-based browsers.

Supports **Brave**, **Chrome**, and **Chromium**.

## Installation

```bash
# Using uvx (recommended)
uvx chromium-sync-mcp

# Or install with pip
pip install chromium-sync-mcp
```

### System Requirements

Requires the LevelDB library:

```bash
# Ubuntu/Debian
sudo apt-get install libleveldb-dev

# macOS
brew install leveldb

# Fedora
sudo dnf install leveldb-devel
```

## Claude Code Configuration

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "chromium-sync": {
      "command": "uvx",
      "args": ["chromium-sync-mcp"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `get_tabs_all_devices` | Get open tabs from all synced devices |
| `get_tabs_local` | Get open tabs from the local browser session |
| `get_history` | Search browsing history with optional filters |
| `get_bookmarks` | Get bookmarks, optionally filtered by folder |
| `search_bookmarks` | Search bookmarks by title or URL |
| `select_browser` | Select which browser to use (when multiple installed) |
| `set_profile_path` | Manually set the browser profile path |
| `check_sync_status` | Check what data is accessible (for debugging) |

## Configuration

### Auto-detection

The server automatically detects installed Chromium-based browsers. If multiple browsers are found, you'll be prompted to select one.

### Environment Variable

Override auto-detection by setting `CHROMIUM_PROFILE_PATH`:

```bash
export CHROMIUM_PROFILE_PATH=~/.config/google-chrome/Default
```

### Saved Preference

When prompted to select a browser, use `select_browser` with `save_default: true` to save your preference to `~/.config/chromium-sync/profile`.

## Supported Browsers

| Browser | Linux | macOS | Windows |
|---------|-------|-------|---------|
| Brave | ✓ | ✓ | ✓ |
| Chrome | ✓ | ✓ | ✓ |
| Chromium | ✓ | ✓ | ✓ |

## How It Works

This server reads directly from your browser's local profile files:

- **History**: SQLite database
- **Bookmarks**: JSON file
- **Synced Tabs**: LevelDB (contains tabs from all your synced devices)

No authentication or network requests required.

## Headless Setup (Sync Passphrase Entry)

If you're running on a headless server and need to enter your Chrome sync passphrase, use the `chromium-sync-setup` command. It launches a browser in a virtual display and provides a secure web URL for remote access.

This is a **one-time setup** per machine. Once you've entered your passphrase and sync is established, you won't need to run this again.

```bash
# If you installed via uvx (recommended)
uvx --with chromium-sync-mcp[setup] --from chromium-sync-mcp chromium-sync-setup

# If you installed via pip
pip install chromium-sync-mcp[setup]
chromium-sync-setup
```

**What it does:**
1. Starts a virtual X display (Xvnc or Xvfb)
2. Launches your browser to the sync settings page
3. Provides a secure HTTPS URL via Cloudflare tunnel

**System requirements (one of):**
- TigerVNC: `sudo apt install tigervnc-standalone-server`
- Or Xvfb + x11vnc: `sudo apt install xvfb x11vnc`

The script auto-downloads cloudflared and noVNC, so those don't need manual installation.

## License

Apache 2.0
