# Gong Connector

MCP server that connects Gong call transcripts and analytics to Claude. Start a Claude session, ask questions, and Claude will search your Gong calls to find answers.

## What It Does

Once connected, Claude gets four tools:

| Tool | Description |
|------|-------------|
| `list_calls` | List recent calls with metadata (title, date, duration, participants) |
| `get_call_details` | Get full transcript + metadata for a specific call |
| `search_transcripts` | Full-text search across all call transcripts |
| `get_call_analytics` | Get trackers, topics, talk ratios, key points for a call |

Transcripts are cached locally (SQLite) for fast repeated searches. Cache refreshes automatically after 1 hour.

## Quick Start (1 minute, zero install)

You just need two things:
1. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
2. The shared **Gong API key and secret** (ask your team lead)

Then add this to your Claude config — **that's it, no other steps**:

### Claude Code

Add to your MCP settings (`~/.claude/settings.json` or per-project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "gong": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/amacko-ocrolus/gongconnector.git", "gong-connector"],
      "env": {
        "GONG_API_KEY": "your-gong-api-key",
        "GONG_API_SECRET": "your-gong-api-secret"
      }
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gong": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/amacko-ocrolus/gongconnector.git", "gong-connector"],
      "env": {
        "GONG_API_KEY": "your-gong-api-key",
        "GONG_API_SECRET": "your-gong-api-secret"
      }
    }
  }
}
```

Replace `your-gong-api-key` and `your-gong-api-secret` with the actual credentials, then restart Claude. The connector auto-installs on first use.

## Try It Out

Start a new Claude session and ask:

- "What did customers say about pricing in calls last week?"
- "Show me the transcript from the Acme Corp call on Monday"
- "Search our calls for mentions of competitor X"
- "List all calls from the past 7 days"
- "What were the key topics in call ID abc123?"

Claude will automatically use the Gong tools to find answers.

## Share With Your Team

Copy-paste this to Slack:

> Want to search Gong calls from Claude? Takes 1 minute:
> 1. Install uv if you don't have it: `curl -LsSf https://astral.sh/uv/install.sh | sh`
> 2. Add the Gong MCP config to your Claude settings — see the README: https://github.com/amacko-ocrolus/gongconnector
> 3. Get the Gong API key/secret from [your team lead]
> That's it — start a Claude session and ask about any Gong call!

## Alternative: Manual Install

If you prefer `pip install` over `uvx`:

```bash
pip install git+https://github.com/amacko-ocrolus/gongconnector.git
```

Then use `python` instead of `uvx` in your Claude config:

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

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `GONG_API_KEY` | Yes | Your Gong API access key |
| `GONG_API_SECRET` | Yes | Your Gong API access secret |

### Cache

Transcripts are cached at `~/.gong_connector/cache.db`. The cache auto-refreshes after 1 hour. To clear the cache:

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
