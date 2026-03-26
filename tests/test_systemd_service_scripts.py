from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_install_systemd_service_renders_unit_and_env(tmp_path):
    python_bin = tmp_path / 'python'
    python_bin.write_text('#!/usr/bin/env bash\nexit 0\n', encoding='utf-8')
    python_bin.chmod(python_bin.stat().st_mode | stat.S_IXUSR)

    config_path = tmp_path / 'strategy.yaml'
    config_path.write_text('runtime: {}\n', encoding='utf-8')

    env_file_path = tmp_path / '.env'
    env_file_path.write_text('BINANCE_API_KEY=dummy\n', encoding='utf-8')

    systemd_dir = tmp_path / 'systemd'
    env_dir = tmp_path / 'env'

    result = subprocess.run(
        [
            str(REPO_ROOT / 'scripts' / 'install_systemd_service.sh'),
            '--service-name', 'custom-runtime',
            '--service-user', 'trader',
            '--service-group', 'trader',
            '--repo-dir', str(REPO_ROOT),
            '--python', str(python_bin),
            '--config', str(config_path),
            '--env-file', str(env_file_path),
            '--action-mode', 'live',
            '--sleep-seconds', '75',
            '--sleep-heartbeat-seconds', '9',
            '--log-level', 'DEBUG',
            '--clear-stop-signal', 'false',
            '--stop-reason', 'maintenance_window',
            '--stop-timeout-seconds', '101',
            '--stop-poll-seconds', '3',
            '--systemd-dir', str(systemd_dir),
            '--env-dir', str(env_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    unit_path = systemd_dir / 'custom-runtime.service'
    runtime_env_path = env_dir / 'custom-runtime.env'

    assert unit_path.exists()
    assert runtime_env_path.exists()
    assert f'Installed systemd unit: {unit_path}' in result.stdout
    assert f'Runtime env file: {runtime_env_path} (written)' in result.stdout

    unit_text = unit_path.read_text(encoding='utf-8')
    assert 'User=trader' in unit_text
    assert 'Group=trader' in unit_text
    assert f'WorkingDirectory={REPO_ROOT}' in unit_text
    assert f'Environment="BINANCE_STRATEGY_RUNTIME_ENV_FILE={runtime_env_path}"' in unit_text
    assert f'ExecStart={REPO_ROOT}/scripts/systemd_runtime.sh start' in unit_text
    assert f'ExecStop={REPO_ROOT}/scripts/systemd_runtime.sh stop' in unit_text

    env_text = runtime_env_path.read_text(encoding='utf-8')
    assert f'BINANCE_STRATEGY_REPO_DIR={REPO_ROOT}' in env_text
    assert f'BINANCE_STRATEGY_PYTHON={python_bin}' in env_text
    assert f'BINANCE_STRATEGY_CONFIG={config_path}' in env_text
    assert f'BINANCE_STRATEGY_ENV_FILE={env_file_path}' in env_text
    assert 'BINANCE_STRATEGY_ACTION_MODE=live' in env_text
    assert 'BINANCE_STRATEGY_SLEEP_SECONDS=75' in env_text
    assert 'BINANCE_STRATEGY_SLEEP_HEARTBEAT_SECONDS=9' in env_text
    assert 'BINANCE_STRATEGY_LOG_LEVEL=DEBUG' in env_text
    assert 'BINANCE_STRATEGY_CLEAR_STOP_SIGNAL=false' in env_text
    assert 'BINANCE_STRATEGY_STOP_REASON=maintenance_window' in env_text
    assert 'BINANCE_STRATEGY_STOP_TIMEOUT_SECONDS=101' in env_text
    assert 'BINANCE_STRATEGY_STOP_POLL_SECONDS=3' in env_text


def test_systemd_runtime_wrapper_uses_expected_runtime_commands(tmp_path):
    fake_python = tmp_path / 'python'
    command_log = tmp_path / 'commands.jsonl'
    fake_python.write_text(
        '#!/usr/bin/env python3\n'
        'import json\n'
        'import os\n'
        'import sys\n'
        'with open(os.environ["TEST_WRAPPER_LOG"], "a", encoding="utf-8") as handle:\n'
        '    handle.write(json.dumps(sys.argv[1:]) + "\\n")\n',
        encoding='utf-8',
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    runtime_env_path = tmp_path / 'runtime.env'
    runtime_env_path.write_text('', encoding='utf-8')

    base_env = {
        **os.environ,
        'BINANCE_STRATEGY_RUNTIME_ENV_FILE': str(runtime_env_path),
        'BINANCE_STRATEGY_REPO_DIR': str(REPO_ROOT),
        'BINANCE_STRATEGY_PYTHON': str(fake_python),
        'BINANCE_STRATEGY_CONFIG': str(tmp_path / 'strategy.yaml'),
        'BINANCE_STRATEGY_ENV_FILE': str(tmp_path / '.env'),
        'BINANCE_STRATEGY_ACTION_MODE': 'paper',
        'BINANCE_STRATEGY_SLEEP_SECONDS': '66',
        'BINANCE_STRATEGY_SLEEP_HEARTBEAT_SECONDS': '7',
        'BINANCE_STRATEGY_LOG_LEVEL': 'WARNING',
        'BINANCE_STRATEGY_CLEAR_STOP_SIGNAL': 'true',
        'BINANCE_STRATEGY_STOP_REASON': 'systemd_stop',
        'BINANCE_STRATEGY_STOP_TIMEOUT_SECONDS': '91',
        'BINANCE_STRATEGY_STOP_POLL_SECONDS': '4',
        'TEST_WRAPPER_LOG': str(command_log),
    }

    for command in ('start', 'stop', 'status', 'clear-stop'):
        subprocess.run(
            [str(REPO_ROOT / 'scripts' / 'systemd_runtime.sh'), command],
            check=True,
            cwd=REPO_ROOT,
            env=base_env,
            capture_output=True,
            text=True,
        )

    calls = [json.loads(line) for line in command_log.read_text(encoding='utf-8').splitlines()]

    assert calls[0] == [
        '-m', 'src.main',
        '--config', str(tmp_path / 'strategy.yaml'),
        '--env-file', str(tmp_path / '.env'),
        '--log-level', 'WARNING',
        'runtime-start',
        '--action-mode', 'paper',
        '--sleep-seconds', '66',
        '--sleep-heartbeat-seconds', '7',
        '--clear-stop-signal',
    ]
    assert calls[1] == [
        '-m', 'src.main',
        '--config', str(tmp_path / 'strategy.yaml'),
        '--env-file', str(tmp_path / '.env'),
        '--log-level', 'WARNING',
        'runtime-stop',
        '--reason', 'systemd_stop',
        '--wait',
        '--timeout-seconds', '91',
        '--poll-seconds', '4',
    ]
    assert calls[2] == [
        '-m', 'src.main',
        '--config', str(tmp_path / 'strategy.yaml'),
        '--env-file', str(tmp_path / '.env'),
        '--log-level', 'WARNING',
        'runtime-status',
    ]
    assert calls[3] == [
        '-m', 'src.main',
        '--config', str(tmp_path / 'strategy.yaml'),
        '--env-file', str(tmp_path / '.env'),
        '--log-level', 'WARNING',
        'clear-runner-stop',
    ]
