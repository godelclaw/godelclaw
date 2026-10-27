# Run an independent agent instance

The templates in this directory separate the cognitive loop, Telegram transport,
operator controls and optional execution gateway. Each has its own lifetime;
restarting cognition need not disconnect the operator channel.

These instructions describe the runtime on `main`. Begin with
the [project setup](../README.md#get-started). The durable Telegram service also
needs a built CeTTa service host and its configured `channel` application root;
this repository supplies the client, command definitions and launch wrapper,
not an automatic installer for that host bundle.

## Choose an instance name and layout

Use a short instance name such as `research`. It identifies services and state,
while the agent's conversational identity comes from its private prompt.
Repeat the layout with a different name for another instance.

| Template convention | Example for `research` |
|---|---|
| Checkout `%h/pettaclaw-%i` | `~/pettaclaw-research` |
| Channel environment | `~/.config/pettaclaw/research-channel.env` |
| Runtime environment | `~/.config/pettaclaw/research-runtime.env` |
| Bot credential | `~/.config/cetta-telegram-credentials/research/token` |
| Transport state | systemd's user state directory, `cetta-telegram-research/` |
| Private sockets | systemd's user runtime directory, `cetta-telegram-research/client.sock` and `operator.sock` |

`%h`, `%i`, `%t` and `%S` are systemd specifiers. The `pettaclaw` filenames are
retained interface names; the project is GödelClaw. If your checkout lives
elsewhere, adapt the unit's `WorkingDirectory` and `ExecStart` together.

Initialize with the same instance name that the services will use:

```sh
cd ~/pettaclaw-research
METTACLAW_INSTANCE=research ./initialize.sh
```

Edit `config/local.toml`, `config/secrets.env` and `memory/soul.md` for this
instance, then repeat initialization. Use separate credentials and state for
each instance. The templates share code, not agent memories.

## Configure the channel and runtime

The channel environment must supply these values:

| Setting | Meaning |
|---|---|
| `CETTA_TELEGRAM_SERVICE_BIN` | Absolute path to the built durable Telegram service executable. |
| `CETTA_TELEGRAM_SERVICE_ROOT` | Application root containing the configured `channel` program for that service. |
| `METTACLAW_TELEGRAM_OPERATOR_IDS` | Comma-separated operator user IDs. |
| `METTACLAW_TELEGRAM_PRIMARY_CHAT_ID` | Default private destination for this instance. |
| `METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS` | Additional permitted chats, when needed. |

Store the bot token as the sole content of the credential file. systemd supplies
it through `LoadCredential`; the service launcher passes its filename to the
host. Keep credentials and both environment files private, outside Git.
`EnvironmentFile` values must contain actual paths: shell variables and `~`
are not expanded there.

In `config/local.toml`, set `[telegram]` to `transport = "durable"`,
`client = "metta"`, and `service_socket` to this instance's **client** socket.
This socket is also used by the command responder. Configure identical routing
and authorization values in the channel environment and `config/secrets.env`.
The separate operator socket is reserved for the service's administrative
interface.

The runtime environment can supply instance-specific engine, mode, model,
lifecycle and gateway settings. Use the same values for cognition and the
command responder. Both run through `run.sh`, which loads the generated `.env`
and then `config/secrets.env`; avoid conflicting values across these files.
With `METTACLAW_SKIP_INITIALIZE=1`, rerun initialization after changing the TOML.

## Install the reusable services

Copy these templates into your user systemd directory:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/cetta-telegram@.socket systemd/cetta-telegram@.service \
  systemd/pettaclaw-telegram-control@.service systemd/pettaclaw@.service \
  ~/.config/systemd/user/
```

The [cognition template](pettaclaw@.service) explicitly starts and orders itself
after its socket, transport and command responder. Its startup check requires
the client socket. A missing transport withholds provider admission; leaving
cognition active does not by itself establish that the agent is making progress.

Set `METTACLAW_INSTANCE=research` in `research-runtime.env` too, so the command
responder uses the same instance name. Keep provider credentials in the
instance's `config/secrets.env`. The transport and responder remain independent
of cognition: the example uses startup dependencies, without `PartOf` or a
shared service control group.

The start limit stops a persistent startup failure after five attempts within
two minutes. Inspect `journalctl --user -u pettaclaw@research.service`, fix the
cause, then use `systemctl --user reset-failed pettaclaw@research.service` before
starting cognition again. Mode changes preserve the selected evaluator; automatic
framework fallback requires `METTACLAW_ENGINE_FALLBACK=petta`.

Once the configured files and host bundle are in place:

```sh
systemctl --user daemon-reload
systemctl --user enable --now cetta-telegram@research.socket
systemctl --user enable --now cetta-telegram@research.service
systemctl --user enable --now pettaclaw-telegram-control@research.service
systemctl --user enable --now pettaclaw@research.service
```

Check each unit's status and journal separately. `/modes` should answer through
the independent responder, including while cognition is stopped. `/mode iter`
or `/mode omega` selects the loop for the next supervised cognition process.
Both require the durable channel; the responder always executes on CeTTa. Iter
also needs the `cetta` engine: with `petta` selected, its launch refuses.

## Start at boot and check recovery

User units normally start when the user manager starts. To run them after boot
without an interactive login, enable lingering for the account that owns them:

```sh
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

Expect `Linger=yes`. This keeps the user manager available after logout too;
see the upstream [loginctl documentation](https://www.freedesktop.org/software/systemd/man/252/loginctl.html).
Enabling a unit and starting it are separate operations; the installation
commands above do both. Check the configured instance before and after a reboot:

```sh
systemctl --user is-enabled cetta-telegram@research.socket \
  cetta-telegram@research.service pettaclaw-telegram-control@research.service \
  pettaclaw@research.service
systemctl --user status cetta-telegram@research.socket \
  cetta-telegram@research.service pettaclaw-telegram-control@research.service \
  pettaclaw@research.service
test -S "$XDG_RUNTIME_DIR/cetta-telegram-research/client.sock"
journalctl --user -b -u cetta-telegram@research.service \
  -u pettaclaw-telegram-control@research.service -u pettaclaw@research.service
```

Follow three boundaries when checking recovery:

1. Transport: the private socket exists, polling succeeds, and the independent
   command responder can answer an operator control while cognition is stopped.
2. Cognition: inspect the configured `METTACLAW_COGNITIVE_HEALTH_PATH`. Compare
   fresh started/completed/failed counters and timestamps; distinguish a
   configured rest or quota cooldown from repeated failed admission. A fresh
   completed provider turn is evidence beyond `active (running)`.
3. Input: unacknowledged input survives restart and is incorporated into the
   next prepared request. Input arriving after preparation stays queued for the
   next turn; an explicit stop interrupts unexecuted work. Successful history recording
   acknowledges only the input actually prepared for that turn.

The same-process `tests/input_batch_refresh_probe.metta` checks preparation
across a failed turn and retains later input after acknowledgement. Its queue
and acknowledgement model is in `verification/InputBatch.lean`. The independent
channel integration fixture exercises restart, controls and retained input.
Run these with disposable state and a local mock channel; they do not require
contacting a production bot.

When upgrading several instances, use one reviewed runtime manifest and the
[grouped updater](../README.md#choose-a-cognitive-mode). Keep each instance's
prompt, credentials, memory and settings separate. Repeat the boot and progress
checks for every instance; one healthy peer does not establish another's health.

## Optional services and existing deployment examples

The [execution gateway](../src/execution_gateway/README.md) has its own
`pettaclaw-gateway@.service` template and configuration. Start it separately,
then configure the instance's gateway socket and agent ID. It is optional for
the supplied loops.

Keep the installed unit and its Python module paths independent of a development
checkout. Once `gateway-research.json` and `gateway-research.env` exist, preview
the binding to the deployed root:

```sh
python3 scripts/install_execution_gateway.py --instance research \
  --runtime-root "$HOME/pettaclaw-research"
```

Repeat with `--apply --backup-dir PATH` to install it, using a new backup
directory. The installer copies the unit, repairs instance links, and updates
the gateway and supplied read-adapter module paths. Custom tools and queue state
stay intact. It preserves whether the service was enabled and does not restart
it. Run `systemctl --user daemon-reload`, then enable/start the instance as
needed. Before restarting an existing instance, let pending jobs finish.

A running PID does not establish restart readiness. Check `LoadState=loaded`
with `systemctl --user show pettaclaw-gateway@research.service`, and exercise a
configured read tool after a controlled restart. A successful `tools` response
alone does not exercise the separate tool subprocess's imports.

The non-template units and deployment watcher files predate this reusable
layout. They encode a particular installation and are not defaults to copy
unchanged. Likewise, `src/sibling.py` currently contains a fixed two-peer map;
it is not a general multi-instance registry. Ordinary mode operation does not
require peer control. Adapt that optional integration separately if you need it.

For expected request, checkpoint and restart behavior, see the
[mode guide](../modes/README.md). For fixture-based checks of the service boundary,
see `tests/test_durable_telegram_service.py` and
`tests/test_mode_control_boundary.py`.
