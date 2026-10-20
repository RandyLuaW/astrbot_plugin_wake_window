# Wake Window: keep the conversation going after one @mention

> AstrBot plugin · Pass-through extends the window, per group

## The problem

Wake-up detection is per message: every group message needs an @mention or wake prefix, otherwise the event is dropped.

So the real experience is: you @ it → it replies once → you follow up → silence. The conversation breaks.

## Features

- Remembers the last time each group got a reply and lets plain messages through inside a window
- Each pass-through **extends** the window, so an ongoing conversation never closes
- Outside the window, behaviour is unchanged
- Doesn't interrupt when someone @s another user
- Commands (wake prefix) always pass
- `merge_previous_seconds` pulls in messages sent just before the mention
- Per-group, optionally limited via `groups`

## Install

### Option 1: Plugin marketplace (recommended)

Search "群聊接话窗口" in the AstrBot WebUI plugin marketplace.

### Option 2: Download from Releases

Grab the zip from [Releases](../../releases), then WebUI → Plugins → Upload.

### Option 3: Clone manually

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_wake_window.git
```

No extra Python dependencies.

## Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | See Chinese README |
| `window_seconds` | int | `120` | See Chinese README |
| `wake_cooldown_seconds` | int | `90` | See Chinese README |
| `merge_previous_seconds` | int | `90` | See Chinese README |
| `merge_max_messages` | int | `3` | See Chinese README |
| `groups` | list | `[]` | See Chinese README |
| `log` | bool | `false` | See Chinese README |

## Notes

- `wake_prefix` comes from the **global** config, not this plugin's
- Cooldown trades per-message replies against broken conversations

## License

MIT
