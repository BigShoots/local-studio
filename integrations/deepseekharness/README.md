# DeepSeekHarness integration

These helpers expose Local Studio recipes to DeepSeekHarness with the same
automatic model-switch behavior as the existing LM Studio integration.

- `sync-localstudio-models.py` checks the controller recipe catalog every 30
  seconds and keeps the `localstudio` provider's `models` list in
  `~/.dsh/settings.yaml` current.
- `localstudio-switch-proxy.py` presents an OpenAI-compatible endpoint on
  `127.0.0.1:1236`. Before forwarding inference, it launches the requested
  recipe and evicts any other Local Studio model.
- `lan-proxy.py` exposes DSH's loopback listener to the trusted LAN without
  timing out persistent event streams. The connection timeout applies only
  while opening the upstream socket, so UI state survives idle periods.
- `localstudio_api.py` loads the controller key from the environment or the
  controller `.env` file without copying that key into DeepSeekHarness.
- `patch-dsh-zero-usage.py` prevents failed retry records with zero usage from
  replacing the last valid context-pressure sample. Run it before DSH starts
  so the patch is reapplied after package upgrades.
- `patch-dsh-lan-directory-picker.py` allows a trusted LAN client to invoke
  the server's native workspace folder picker while other host RPC methods
  remain loopback-only. Run it before DSH starts so package upgrades retain
  the workspace picker exception.

The provider entry should use `http://127.0.0.1:1236/v1`, the OpenAI
completions API, and `max_completion_tokens`. Give the provider a fixed local
client key such as `local-studio`; the switch proxy replaces it with the real
controller credential. Set `compat.supportsReasoningEffort` to `true` so DSH
shows the reasoning selector. Qwen3.8 recipes expose Off, Minimal, Low, Medium,
High, and Max. They map to vLLM's native `off`, `auto`, `low`, `medium`, `high`,
and `max` values, matching Local Studio's built-in chat. DSH calls Local
Studio's Auto setting Minimal because Auto is not a DSH selector ID.

Run the helpers alongside DeepSeekHarness:

```bash
export LOCALSTUDIO_CONTROLLER_ENV=/path/to/local-studio/.env
python3 ~/.dsh/localstudio-switch-proxy.py &
python3 ~/.dsh/lan-proxy.py &
python3 ~/.dsh/sync-localstudio-models.py --watch &
dsh web
```

The catalog contains runnable recipes, rather than every unfinished download.
After a downloaded model is turned into a serve recipe in Local Studio, it
appears in DeepSeekHarness on the next watcher interval. Switching the selected
model loads that recipe; switching again unloads the previous one.
