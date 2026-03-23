# Gong Connector

MCP server that connects Gong call transcripts and analytics to Claude. Start a Claude session, ask questions, and Claude will search your Gong calls to find answers.

## What It Does

Once installed, Claude gets four tools:

| Tool | Description |
|------|-------------|
| `list_calls` | List recent calls with metadata (title, date, duration, participants) |
| `get_call_details` | Get full transcript + metadata for a specific call |
| `search_transcripts` | Full-text search across all call transcripts |
| `get_call_analytics` | Get trackers, topics, talk ratios, key points for a call |

Transcripts are cached locally (SQLite) for fast repeated searches. Cache refreshes automatically after 1 hour.

## Setup (2 minutes)

### Prerequisites

- Python 3.10+
- Gong API key and secret (get from your team lead)

### 1. Install

```bash
pip install git+https://github.com/amacko-ocrolus/gongconnector.git
```

Or clone and install locally:

```bash
git clone https://github.com/amacko-ocrolus/gongconnector.git
cd gongconnector
pip install .
```

### 2. Add to Claude

**For Claude Code**, add to your MCP settings (`.claude/settings.json` or global settings):

```json
{
  "mcpServers": {
    "gong": {
      "command": "python",
      "args": ["-m", "gong_connector"],
      "env": {
        "GONG_API_KEY": "your-gong-api-key",
        "GONG_API_SECRET": "your-gong-api-secret"
      }
    }
  }
}
```

**For Claude Desktop**, add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "gong": {
      "command": "python",
      "args": ["-m", "gong_connector"],
      "env": {
        "GONG_API_KEY": "your-gong-api-key",
        "GONG_API_SECRET": "your-gong-api-secret"
      }
    }
  }
}
```

### 3. Use It

Start a new Claude session and ask questions like:

- "What did customers say about pricing in calls last week?"
- "Show me the transcript from the Acme Corp call on Monday"
- "Search our calls for mentions of competitor X"
- "List all calls from the past 7 days"
- "What were the key topics in call ID abc123?"

Claude will automatically use the Gong tools to find answers.

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `GONG_API_KEY` | Yes | Your Gong API access key |
| `GONG_API_SECRET` | Yes | Your Gong API access secret |

### Cache

Transcripts are cached at `~/.gong_connector/cache.db`. The cache auto-refreshes after 1 hour. To clear the cache, delete the file:

```bash
rm ~/.gong_connector/cache.db
```

## Development

```bash
git clone https://github.com/amacko-ocrolus/gongconnector.git
cd gongconnector
pip install -e .
```

Run the server directly:

```bash
export GONG_API_KEY=your-key
export GONG_API_SECRET=your-secret
python -m gong_connector
```
