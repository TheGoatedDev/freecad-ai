# ACP Chat for FreeCAD

AI chat panel for FreeCAD. Talks to any [Agent Client Protocol](https://agentclientprotocol.com) agent (Claude by default). The agent drives your live session through the [MCP Bridge](https://github.com/CREATeNG/freecad-mcp-bridge) addon.

## Install

1. Install **MCP Bridge** from the Addon Manager.
2. Link this repo into your Mod folder and restart FreeCAD:
   `ln -s "$PWD" ~/Library/Application\ Support/FreeCAD/v1-1/Mod/acp-chat`
3. Click **AI Chat** on the toolbar. The panel starts MCP Bridge and the agent.

The default agent needs Node (`npx`) and a Claude Code login.

## Settings

Tools > Edit parameters > `BaseApp/Preferences/Mod/ACPChat`:

| Key | Default |
|---|---|
| `AgentCommand` | `npx -y @agentclientprotocol/claude-agent-acp` |
| `McpUrl` | `http://127.0.0.1:39280/mcp` |

Any ACP agent works, e.g. `gemini --experimental-acp`.

## Test

`python3 -m unittest discover tests`
