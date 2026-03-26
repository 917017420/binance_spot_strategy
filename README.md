# Binance Spot Strategy

Lightweight Python tooling for two related jobs:

- a public-data Binance Spot scanner for `USDT` pairs
- a cautious live-control-plane layer for preflight, reconcile, and local state repair

This package is still intentionally conservative. It is not a fully automated trading system.

## What is here today

### Scanner

- Runs with public Binance market data
- Scans Binance Spot `USDT` pairs
- Filters out stablecoin-like pairs, leveraged tokens, and low-liquidity symbols
- Fetches `1h` and `4h` OHLCV candles
- Computes `EMA20`, `EMA50`, `EMA200`, `ATR(14)`, `20-bar high/low`, and `20-bar average volume`
- Classifies BTC regime as `risk_on`, `neutral`, or `risk_off`
- Scores symbols and emits shortlist-style actions such as:
  - `BUY_READY_BREAKOUT`
  - `BUY_READY_PULLBACK`
  - `WATCH_ONLY`
- Writes `data/output/latest_scan.json` and `data/output/latest_scan.txt`

### Live control-plane helpers

- `submit-preflight` checks credentials, private-mode flags, single-active-trade lock state, and market minimums before a real submit path is allowed
- `exchange-state-reconcile` compares local inflight order tracking with remote Binance open orders before live submit
- `order-refresh-reconcile` re-fetches a Binance order by client order id and re-applies local fill reconciliation
- `live-execution-snapshot`, `control-plane-brief`, and single-active-trade commands summarize safety state kept in `data/execution/`
- Live submit remains disabled unless both `BINANCE_ENABLE_PRIVATE` and `BINANCE_ENABLE_ORDER_SUBMIT` are enabled

## Safety model

Default behavior is safe-by-default:

- scanner mode never places live orders
- adapter preview mode records intent but does not call real submit APIs
- real submit requires API key, API secret, private mode, and explicit order-submit enablement
- local state is reconciled through JSON/JSONL files under `data/execution/`
- control-plane commands are meant to block or surface mismatches, not silently heal everything

## Important disclaimer

Use at your own risk. Nothing here is financial advice.

Even with the control-plane checks, this project is still a lightweight local toolset. It does not provide exchange-grade guarantees, failover, or custody protections.

## Requirements

- Python `3.11+`
- internet access for Binance market data

Install dependencies:

```bash
cd tools/binance_spot_strategy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Optional environment file:

```bash
cp .env.example .env
```

Sample strategy config:

```bash
config/strategy.example.yaml
```

The scanner works without credentials.

Live/private commands require valid Binance credentials plus explicit enable flags.

## Common commands

From `tools/binance_spot_strategy`:

```bash
python3 -m src.main scan --top 5
python3 -m src.main --log-level INFO scan --top 5 --max-symbols 40
python3 -m src.main scan --symbol BTC/USDT
python3 -m src.main runtime-start --action-mode live --sleep-seconds 60
python3 -m src.main runtime-status
python3 -m src.main runtime-stop --reason operator_stop --wait
python3 -m src.main auto-runner-loop --action-mode live --forever
python3 -m src.main request-runner-stop --reason operator_stop
python3 -m src.main clear-runner-stop
python3 -m src.main submit-preflight --symbol BTC/USDT --quote-amount 25 --reference-price 60000
python3 -m src.main exchange-state-reconcile
python3 -m src.main order-refresh-reconcile --symbol BTC/USDT --client-order-id my-client-id
python3 -m src.main live-execution-snapshot
python3 -m src.main control-plane-brief
python3 -m src.main --help
```

Note: `--log-level`, `--config`, and `--env-file` are global options, so place them before the subcommand.

## Key subcommands

- `scan` — generate shortlist reports from public market data
- `confirm-dry-run` — parse a confirmation-style command into a dry-run execution
- `monitor-positions` — evaluate persisted positions and suggested actions
- `runtime-start` / `runtime-stop` / `runtime-status` — preferred resident-runtime operator commands
- `auto-runner-once` / `auto-runner-loop` — lower-level cycle and loop commands kept for compatibility
- `request-runner-stop` / `clear-runner-stop` — lower-level stop-signal controls used by the resident runtime
- `submit-preflight` — validate a potential live order without sending it
- `exchange-state-reconcile` — compare local execution state to remote open orders/balances
- `order-refresh-reconcile` — refresh a specific order fact and re-apply local reconciliation
- `repair-single-active-trade` — repair conflicting single-active-trade lock state
- `reconcile-control-plane` — clean up state after exits, repairs, or unlocks

## Background loop ergonomics

Preferred resident operation now uses the `runtime-*` commands:

- `runtime-start` runs the loop in resident mode with a clearer service-style story
- `runtime-stop` requests graceful loop shutdown through the stop-signal file
- `runtime-stop --wait` blocks until the loop becomes inactive or the wait times out, which is safer for supervised restarts
- `runtime-status` gives a concise operator view of runtime heartbeat, sleep window, stop age, and control-plane ownership
- if a stop signal is still present, `runtime-start` refuses to launch until you clear it or pass `--clear-stop-signal`

The older `auto-runner-loop` path still works and remains the compatibility layer underneath.

`auto-runner-loop` can still be used directly like a resident service loop:

- `--forever` keeps cycling until fuse health blocks the runner or a stop signal is requested
- `request-runner-stop` writes a graceful stop signal under `data/execution/runner_stop.json`
- `clear-runner-stop` clears that signal before the next resident start
- while sleeping between cycles, the runner updates heartbeat and loop-sleep fields in `runner_state.json`

Example:

```bash
python3 -m src.main runtime-start --action-mode live --sleep-seconds 60
python3 -m src.main runtime-status
python3 -m src.main runtime-stop --reason nightly_maintenance --wait

# Compatible lower-level commands remain available:
python3 -m src.main auto-runner-loop --action-mode live --forever
python3 -m src.main request-runner-stop --reason nightly_maintenance
python3 -m src.main control-plane-brief
```

`runtime-start` is intentionally foreground and is meant to be supervised by `tmux`, `systemd`, `nohup`, or a similar process supervisor.

## systemd service setup

This repo now includes a first-pass `systemd` setup under `deploy/systemd/` plus helper scripts in `scripts/`.

Use `scripts/install_systemd_service.sh` to render a concrete unit file and matching runtime env file:

```bash
sudo ./scripts/install_systemd_service.sh \
  --service-user "$USER" \
  --service-group "$(id -gn)" \
  --repo-dir "$(pwd)" \
  --python "$(command -v python3)" \
  --config "$(pwd)/config/strategy.example.yaml" \
  --env-file "$(pwd)/.env"
```

By default this writes:

- `/etc/systemd/system/binance-spot-strategy.service`
- `/etc/binance-spot-strategy/binance-spot-strategy.env`

The rendered unit keeps `runtime-start` in the foreground and uses the existing graceful runtime controls:

- `ExecStart` → `scripts/systemd_runtime.sh start` → `python3 -m src.main runtime-start ...`
- `ExecStop` → `scripts/systemd_runtime.sh stop` → `python3 -m src.main runtime-stop --wait ...`
- `Restart=on-failure` so clean operator stops do not auto-restart, but crashes still restart under `systemd`

The rendered env file sets `BINANCE_STRATEGY_CLEAR_STOP_SIGNAL=true` by default. That keeps `systemctl start` and `systemctl restart` operational after a prior graceful stop by explicitly reusing the existing `runtime-start --clear-stop-signal` flag. If you want every start to remain blocked until an operator manually clears the stop signal, set that value to `false`.

Typical operator flow:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now binance-spot-strategy.service
systemctl status binance-spot-strategy.service
journalctl -u binance-spot-strategy.service -f
sudo systemctl stop binance-spot-strategy.service
```

The direct runtime commands still remain the authoritative control plane view:

```bash
python3 -m src.main --config config/strategy.example.yaml --env-file .env runtime-status
python3 -m src.main --config config/strategy.example.yaml --env-file .env control-plane-brief
./scripts/systemd_runtime.sh clear-stop
```

Operational notes:

- Keep one supervised instance only; the control plane remains file-based and single-process oriented.
- The `systemd` unit supervises the foreground resident loop; it does not daemonize the app internally.
- A stale runtime heartbeat still requires manual inspection before forcing restart decisions.
- The service currently writes state under `data/execution/` in the repo, so install it on durable local storage.

## Output and local state

### Reports

- `data/output/latest_scan.json`
- `data/output/latest_scan.txt`

### Execution state

The control plane stores local state in `data/execution/`, including files such as:

- `live_submit_state.json`
- `live_inflight_state.json`
- `positions.json`
- `executed_orders.jsonl`
- `position_events.jsonl`
- `active_trade_releases.jsonl`

These files are part of the operating model, not just debug artifacts.

## Current limitations

- The scanner is heuristic shortlist generation, not a validated strategy.
- The control plane is file-based and intended for a single local operator/process; it is not multi-process safe.
- Reconciliation is polling-based, not websocket-driven, so fills and remote state can be observed late.
- Preflight checks market minimums, but it does not fully model every Binance rule or precision edge case.
- Exchange-state reconciliation currently focuses on open-order alignment and basic balance visibility; it is not a full account inventory audit.
- Order refresh depends on symbols and client order ids that were previously recorded locally.
- Documentation and commands are evolving with the hardening work; expect more guardrails before trusting unattended live use.
- Resident mode is still a supervised foreground process, not a self-daemonizing service with restart orchestration.

## Testing

From `tools/binance_spot_strategy`:

```bash
python3 -m compileall -q src tests
pytest -q
```
