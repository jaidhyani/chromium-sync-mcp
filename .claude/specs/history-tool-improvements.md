# History Tool Improvements

## Overview

Improve the `get_history` MCP tool to return structured JSON instead of markdown-formatted strings, and add better filtering capabilities including regex pattern matching and ISO date range filtering.

## Goals

- Return raw JSON arrays instead of markdown-wrapped strings
- Add regex pattern matching for URL/title search
- Add ISO date range filtering with `after` and `before` params
- Keep the API simple and composable

## Non-Goals

- No computed fields (domain extraction, relative time strings)
- No output format options (always JSON, let LLM format)
- No grouping or aggregation features
- No exclude patterns or min_visits filters

## Requirements

### Output Format

The tool must return a JSON array of history entry objects directly, not a markdown string. Each entry contains:

```json
{
  "url": "https://example.com/page",
  "title": "Page Title",
  "visit_time": "2026-01-11T14:30:00",
  "visit_count": 3
}
```

No metadata wrapper. Empty results return `[]`.

### Search Parameters

Two mutually exclusive search options:

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Substring match against URL and title (case-insensitive) |
| `pattern` | string | Regex match against URL and title |

If both `query` and `pattern` are provided, return an error.

Invalid regex patterns must fail with a clear error message explaining the syntax issue.

### Date Filtering

| Parameter | Type | Description |
|-----------|------|-------------|
| `after` | string | ISO date or datetime. Only entries on or after this time. |
| `before` | string | ISO date or datetime. Only entries before this time. |
| `days_back` | integer | Only entries from the last N days. (existing) |

Date format: `YYYY-MM-DD` (defaults to 00:00:00) or `YYYY-MM-DDTHH:MM:SS` for precision.

`days_back` and `after`/`before` can be combined (AND logic).

### Other Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 100 | Maximum entries to return |

## Technical Approach

1. **Modify `LocalReader.get_history()`** in `local.py`:
   - Add `pattern`, `after`, `before` parameters
   - For regex: use Python's `re` module, compile with error handling
   - For dates: parse ISO format, convert to Chromium epoch for SQL query
   - Keep existing `query` (substring) and `days_back` logic

2. **Modify `format_history()`** in `server.py`:
   - Remove markdown formatting
   - Return `json.dumps()` of the entry list
   - Format `visit_time` as ISO string

3. **Update tool schema** in `list_tools()`:
   - Add `pattern`, `after`, `before` to inputSchema
   - Update descriptions to reflect new capabilities

4. **Error handling**:
   - Invalid regex: return error with regex error message
   - Invalid date format: return error explaining expected format
   - Both `query` and `pattern`: return error explaining mutual exclusivity

## Open Questions

None. Requirements are fully specified.

## Acceptance Criteria

- [ ] `get_history` returns JSON array, not markdown string
- [ ] `query` does case-insensitive substring match on URL and title
- [ ] `pattern` does regex match on URL and title
- [ ] Providing both `query` and `pattern` returns an error
- [ ] Invalid regex returns error with explanation
- [ ] `after` filters entries >= the given date/datetime
- [ ] `before` filters entries < the given date/datetime
- [ ] Date params accept both `YYYY-MM-DD` and `YYYY-MM-DDTHH:MM:SS`
- [ ] All filters compose with AND logic
- [ ] Empty results return `[]`
- [ ] Existing `days_back` and `limit` params still work
