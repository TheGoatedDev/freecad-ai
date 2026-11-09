# ACP Chat for FreeCAD

AI chat panel for FreeCAD. Talks to any [Agent Client Protocol](https://agentclientprotocol.com) agent (Claude by default). The panel runs a small built-in MCP server (loopback, random port, bearer token) so the agent can run Python in your live session.

## Install

1. Link this repo into your Mod folder and restart FreeCAD:
   `ln -s "$PWD" ~/Library/Application\ Support/FreeCAD/v1-1/Mod/acp-chat`
2. Click **AI Chat** on the toolbar. The panel starts the agent.

The default agent needs Node (`npx`) and a Claude Code login.

## Context

Two toggles under the message box decide what goes with each message. Both are captured when you press send.

- **Include selection** (on by default) sends the selected faces, edges, vertices and objects as text: type, area or length, centroid, normal or radius, and pick point. While it is on, chips show the live selection. Click a chip's × to deselect that item.
- **Include view** sends a PNG of the 3D view, if the agent accepts images.

The toggle states are saved between sessions.

## Model and permissions

Two buttons sit next to send. Each shows what the agent offers through ACP session config options.

- **Permission** (shield) picks the permission mode, such as Manual, Accept edits, Plan, Auto or Bypass permissions.
- **Model** opens the model list, an Effort submenu and on/off settings such as Fast mode. A bolt icon means Fast mode is on.

Your choices are saved and applied again to each new chat.

## Settings

Tools > Edit parameters > `BaseApp/Preferences/Mod/ACPChat`:

| Key | Default |
|---|---|
| `AgentCommand` | `npx -y @agentclientprotocol/claude-agent-acp` |

Any ACP agent works, e.g. `gemini --experimental-acp`.

## Test

`python3 -m unittest discover tests`
