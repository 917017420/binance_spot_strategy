# Binance Spot Strategy

[English](#english) | [中文](#中文)

---

## English

### Overview

Lightweight Python tooling for two related jobs:

- a public-data Binance Spot scanner for `USDT` pairs
- a cautious live control-plane layer for preflight, reconcile, resident monitoring, and local state repair

This package is still intentionally conservative. It is **not** a fully automated trading system.

### What is here today

#### Scanner

- Uses public Binance market data
- Scans Binance Spot `USDT` pairs
- Filters out stablecoin-like pairs, leveraged tokens, and low-liquidity symbols
- Fetches `1h` and `4h` OHLCV candles
- Computes `EMA20`, `EMA50`, `EMA200`, `ATR(14)`, `20-bar high/low`, and `20-bar average volume`
- Classifies BTC regime as `risk_on`, `neutral`, or `risk_off`
- Scores symbols and emits shortlist-style actions such as:
  - `BUY_READY_BREAKOUT`
  - `BUY_READY_PULLBACK`
  - `WATCH_ONLY`
- Adds runway / upside and reward-risk gating to reduce low-payoff setups
- Writes `data/output/latest_scan.json` and `data/output/latest_scan.txt`

#### Live control-plane helpers

- `submit-preflight` checks credentials, private-mode flags, single-active-trade lock state, and market minimums before a real submit path is allowed
- `exchange-state-reconcile` compares local inflight order tracking with remote Binance open orders before live submit
- `order-refresh-reconcile` re-fetches a Binance order by client order id and re-applies local fill reconciliation
- `live-execution-snapshot`, `control-plane-brief`, and single-active-trade commands summarize safety state kept in `data/execution/`
- Resident runtime commands (`runtime-start`, `runtime-stop`, `runtime-status`) keep the loop supervised in foreground/service mode
- Live submit remains disabled unless both `BINANCE_ENABLE_PRIVATE` and `BINANCE_ENABLE_ORDER_SUBMIT` are enabled

### Safety model

Default behavior is safe-by-default:

- scanner mode never places live orders
- adapter preview mode records intent but does not call real submit APIs
- real submit requires API key, API secret, private mode, and explicit order-submit enablement
- local state is reconciled through JSON/JSONL files under `data/execution/`
- control-plane commands are designed to block or surface mismatches, not silently heal everything

### Important disclaimer

Use at your own risk. Nothing here is financial advice.

Even with the control-plane checks, this project is still a lightweight local toolset. It does not provide exchange-grade guarantees, failover, or custody protections.

### Requirements

- Python `3.11+`
- internet access for Binance market data

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Optional environment file:

```bash
cp .env.example .env
```

Available config examples:

- `config/strategy.example.yaml`
- `config/strategy.live.yaml`

The scanner works without credentials.

Live/private commands require valid Binance credentials plus explicit enable flags.

### Common commands

From the repository root:

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

### Key subcommands

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

### Background loop ergonomics

Preferred resident operation now uses the `runtime-*` commands:

- `runtime-start` runs the loop in resident mode with a clearer service-style story
- `runtime-stop` requests graceful loop shutdown through the stop-signal file
- `runtime-stop --wait` blocks until the loop becomes inactive or the wait times out, which is safer for supervised restarts
- `runtime-status` gives a concise operator view of runtime heartbeat, sleep window, stop age, and control-plane ownership
- if a stop signal is still present, `runtime-start` refuses to launch until you clear it or pass `--clear-stop-signal`

The older `auto-runner-loop` path still works and remains the compatibility layer underneath.

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

### systemd service setup

This repo includes a first-pass `systemd` setup under `deploy/systemd/` plus helper scripts in `scripts/`.

Use `scripts/install_systemd_service.sh` to render a concrete unit file and matching runtime env file:

```bash
sudo ./scripts/install_systemd_service.sh \
  --service-user "$USER" \
  --service-group "$(id -gn)" \
  --repo-dir "$(pwd)" \
  --python "$(command -v python3)" \
  --config "$(pwd)/config/strategy.live.yaml" \
  --env-file "$(pwd)/.env"
```

By default this writes:

- `/etc/systemd/system/binance-spot-strategy.service`
- `/etc/binance-spot-strategy/binance-spot-strategy.env`

The rendered unit keeps `runtime-start` in the foreground and uses the existing graceful runtime controls:

- `ExecStart` → `scripts/systemd_runtime.sh start` → `python3 -m src.main runtime-start ...`
- `ExecStop` → `scripts/systemd_runtime.sh stop` → `python3 -m src.main runtime-stop --wait ...`
- `Restart=on-failure` so clean operator stops do not auto-restart, but crashes still restart under `systemd`

Typical operator flow:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now binance-spot-strategy.service
systemctl status binance-spot-strategy.service
journalctl -u binance-spot-strategy.service -f
sudo systemctl stop binance-spot-strategy.service
```

### Output and local state

#### Reports

- `data/output/latest_scan.json`
- `data/output/latest_scan.txt`

#### Execution state

The control plane stores local state in `data/execution/`, including files such as:

- `live_submit_state.json`
- `live_inflight_state.json`
- `positions.json`
- `executed_orders.jsonl`
- `position_events.jsonl`
- `active_trade_releases.jsonl`

These files are part of the operating model, not just debug artifacts.

### Current limitations

- The scanner is heuristic shortlist generation, not a validated strategy.
- The control plane is file-based and intended for a single local operator/process; it is not multi-process safe.
- Reconciliation is polling-based, not websocket-driven, so fills and remote state can be observed late.
- Preflight checks market minimums, but it does not fully model every Binance rule or precision edge case.
- Exchange-state reconciliation currently focuses on open-order alignment and basic balance visibility; it is not a full account inventory audit.
- Order refresh depends on symbols and client order ids that were previously recorded locally.
- Documentation and commands are evolving with the hardening work; expect more guardrails before trusting unattended live use.
- Resident mode is still a supervised foreground process, not a self-daemonizing service with restart orchestration.

### Testing

From the repository root:

```bash
python3 -m compileall -q src tests
pytest -q
```

---

## 中文

### 项目概览

这是一个轻量级 Python 工具集，主要做两件事：

- 基于 Binance Spot `USDT` 交易对的公共数据扫描
- 一个偏保守的实盘控制平面，用于 preflight、reconcile、常驻监控和本地状态修复

这个项目依然保持**保守设计**，**不是**一个可以无脑托管的全自动交易系统。

### 当前已经具备的能力

#### 扫描器

- 使用 Binance 公共市场数据
- 扫描 Binance Spot `USDT` 交易对
- 过滤稳定币风格交易对、杠杆代币和低流动性标的
- 拉取 `1h` 与 `4h` 的 OHLCV K 线
- 计算 `EMA20`、`EMA50`、`EMA200`、`ATR(14)`、`20-bar high/low`、`20-bar average volume`
- 识别 BTC 市场状态：`risk_on` / `neutral` / `risk_off`
- 对标的打分，并输出类似如下动作：
  - `BUY_READY_BREAKOUT`
  - `BUY_READY_PULLBACK`
  - `WATCH_ONLY`
- 已引入 runway / upside 与 reward-risk 约束，尽量减少“看着强但赔率差”的候选
- 输出到 `data/output/latest_scan.json` 与 `data/output/latest_scan.txt`

#### 实盘控制平面辅助能力

- `submit-preflight`：在真实下单前检查凭据、private mode、single-active-trade 锁状态与交易所最小下单限制
- `exchange-state-reconcile`：真实 submit 前，比对本地 inflight 状态与 Binance 远端 open orders
- `order-refresh-reconcile`：按 client order id 重新拉取 Binance 订单事实并重新执行本地成交对账
- `live-execution-snapshot`、`control-plane-brief` 以及 single-active-trade 相关命令：汇总 `data/execution/` 下的安全状态
- `runtime-start` / `runtime-stop` / `runtime-status`：用于常驻值班运行的推荐命令
- 只有在同时开启 `BINANCE_ENABLE_PRIVATE` 与 `BINANCE_ENABLE_ORDER_SUBMIT` 时，真实 submit 才允许执行

### 安全模型

默认行为是 **safe-by-default**：

- 扫描模式不会下真实单
- adapter preview 模式只记录意图，不调用真实 submit API
- 真实 submit 需要 API key、API secret、private mode 和显式 submit 开关同时满足
- 本地状态通过 `data/execution/` 下的 JSON / JSONL 文件做 reconcile
- 控制平面命令主要用于阻断或暴露不一致，而不是偷偷替你“自动修好一切”

### 重要免责声明

风险自负，本文档与项目内容均**不构成任何金融建议**。

即便有控制平面保护，这个项目本质上仍是一个轻量级本地工具集，不提供交易所级别的保证、故障切换或托管保护。

### 环境要求

- Python `3.11+`
- 可访问 Binance 市场数据的网络环境

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置

可选环境文件：

```bash
cp .env.example .env
```

可用配置示例：

- `config/strategy.example.yaml`
- `config/strategy.live.yaml`

纯扫描无需凭据。

涉及 live / private 的命令需要有效 Binance 凭据，并显式开启对应开关。

### 常用命令

在仓库根目录执行：

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

注意：`--log-level`、`--config`、`--env-file` 是全局参数，要放在子命令前面。

### 关键子命令说明

- `scan` —— 基于公共市场数据生成 shortlist 报告
- `confirm-dry-run` —— 将确认式指令解析成 dry-run 执行
- `monitor-positions` —— 对已持久化仓位做评估并给出建议动作
- `runtime-start` / `runtime-stop` / `runtime-status` —— 推荐的常驻运行控制命令
- `auto-runner-once` / `auto-runner-loop` —— 兼容保留的底层循环命令
- `request-runner-stop` / `clear-runner-stop` —— 常驻 runtime 的底层 stop-signal 控制命令
- `submit-preflight` —— 不发单，只校验 live order 是否满足前置条件
- `exchange-state-reconcile` —— 对比本地执行状态与远端 open orders / balances
- `order-refresh-reconcile` —— 刷新指定订单事实并重新执行本地 reconcile
- `repair-single-active-trade` —— 修复 single-active-trade 锁状态冲突
- `reconcile-control-plane` —— 在 exit / repair / unlock 后清理控制平面状态

### 常驻循环运行方式

推荐使用 `runtime-*` 命令：

- `runtime-start`：以 resident 模式启动循环，语义更像 service
- `runtime-stop`：通过 stop-signal 文件请求优雅停止
- `runtime-stop --wait`：等待 resident loop 真正停下，更适合受 supervisor 管理的场景
- `runtime-status`：快速查看 heartbeat、sleep 窗口、stop age 与控制平面 ownership
- 如果 stop-signal 还在，`runtime-start` 会拒绝启动，除非先清理或加 `--clear-stop-signal`

旧的 `auto-runner-loop` 仍然可用，作为兼容层保留。

示例：

```bash
python3 -m src.main runtime-start --action-mode live --sleep-seconds 60
python3 -m src.main runtime-status
python3 -m src.main runtime-stop --reason nightly_maintenance --wait

# 兼容保留的旧命令
python3 -m src.main auto-runner-loop --action-mode live --forever
python3 -m src.main request-runner-stop --reason nightly_maintenance
python3 -m src.main control-plane-brief
```

`runtime-start` 本身是前台进程，适合交给 `tmux`、`systemd`、`nohup` 等监督。

### systemd 服务化

仓库内已提供一版 `systemd` 配置模板，位于 `deploy/systemd/`，辅助脚本位于 `scripts/`。

你可以用 `scripts/install_systemd_service.sh` 渲染实际 unit 与 runtime env 文件：

```bash
sudo ./scripts/install_systemd_service.sh \
  --service-user "$USER" \
  --service-group "$(id -gn)" \
  --repo-dir "$(pwd)" \
  --python "$(command -v python3)" \
  --config "$(pwd)/config/strategy.live.yaml" \
  --env-file "$(pwd)/.env"
```

默认会生成：

- `/etc/systemd/system/binance-spot-strategy.service`
- `/etc/binance-spot-strategy/binance-spot-strategy.env`

渲染后的 unit 会保持 `runtime-start` 以前台方式运行，并复用现有优雅停机逻辑：

- `ExecStart` → `scripts/systemd_runtime.sh start` → `python3 -m src.main runtime-start ...`
- `ExecStop` → `scripts/systemd_runtime.sh stop` → `python3 -m src.main runtime-stop --wait ...`
- `Restart=on-failure`：正常人工停止不会自动拉起，但异常崩溃会被 systemd 重启

典型运维流程：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now binance-spot-strategy.service
systemctl status binance-spot-strategy.service
journalctl -u binance-spot-strategy.service -f
sudo systemctl stop binance-spot-strategy.service
```

### 输出与本地状态

#### 报告输出

- `data/output/latest_scan.json`
- `data/output/latest_scan.txt`

#### 执行状态

控制平面的本地状态保存在 `data/execution/`，包括但不限于：

- `live_submit_state.json`
- `live_inflight_state.json`
- `positions.json`
- `executed_orders.jsonl`
- `position_events.jsonl`
- `active_trade_releases.jsonl`

这些不是简单 debug 文件，而是控制平面工作模型的一部分。

### 当前局限

- 扫描器本质上仍是启发式 shortlist 生成器，不是经过严格验证的成熟策略。
- 控制平面是文件驱动的，默认按**单机 / 单操作员 / 单进程**使用场景设计，不适合多进程并发写入。
- Reconcile 依赖轮询，不是 websocket 驱动，因此真实成交与远端状态存在滞后。
- Preflight 会检查市场最小下单限制，但还不能完整覆盖 Binance 的所有规则和精度边界。
- Exchange-state reconcile 目前主要关注 open orders 对齐和基础余额可见性，还不是完整账户审计。
- Order refresh 依赖本地已记录过的 symbol 与 client order id。
- 文档和命令仍在随着硬化过程持续演进；在完全信任 unattended live use 之前，还需要更多 guardrails。
- Resident mode 仍是“前台进程 + supervisor 托管”的模式，不是自守护 daemon。

### 测试

在仓库根目录执行：

```bash
python3 -m compileall -q src tests
pytest -q
```
