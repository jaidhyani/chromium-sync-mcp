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
| `browser_tabs` | Get open tabs from all synced devices |
| `browser_history` | Search browsing history with optional filters |
| `browser_bookmarks` | Get bookmarks, optionally filtered by folder |
| `browser_search_bookmarks` | Search bookmarks by title or URL |
| `browser_select` | Select which browser to use (when multiple installed) |

## Configuration

### Auto-detection

The server automatically detects installed browsers in this order:
1. Brave
2. Chrome
3. Chromium

If multiple browsers are found, you'll be prompted to select one.

### Environment Variable

Override auto-detection by setting `CHROMIUM_PROFILE_PATH`:

```bash
export CHROMIUM_PROFILE_PATH=~/.config/google-chrome/Default
```

### Saved Preference

When prompted to select a browser, choose `save_default: true` to save your preference to `~/.config/chromium-sync/profile`.

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

## License

Apache 2.0
