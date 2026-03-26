from __future__ import annotations

from src.config import load_settings


def test_load_settings_applies_live_quote_amount_env_override(tmp_path, monkeypatch):
    monkeypatch.delenv('AUTO_ENTRY_LIVE_ORDER_QUOTE_AMOUNT', raising=False)
    config_path = tmp_path / 'strategy.yaml'
    env_path = tmp_path / '.env'
    config_path.write_text('auto_entry:\n  live_order_quote_amount: 4.5\n', encoding='utf-8')
    env_path.write_text('AUTO_ENTRY_LIVE_ORDER_QUOTE_AMOUNT=7.25\n', encoding='utf-8')

    settings = load_settings(config_path=str(config_path), env_path=str(env_path))

    assert settings.auto_entry.live_order_quote_amount == 7.25


def test_load_settings_applies_exit_env_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv('POSITION_EXIT_TP1_REDUCE_PCT', raising=False)
    monkeypatch.delenv('POSITION_EXIT_TRAILING_DRAWDOWN_PCT', raising=False)
    monkeypatch.delenv('POSITION_EXIT_ENABLE_TRAILING_ON_TP2', raising=False)
    config_path = tmp_path / 'strategy.yaml'
    env_path = tmp_path / '.env'
    config_path.write_text(
        'exit:\n  tp1_reduce_pct: 25\n  trailing_drawdown_pct: 5.5\n  enable_trailing_on_tp2: true\n',
        encoding='utf-8',
    )
    env_path.write_text(
        'POSITION_EXIT_TP1_REDUCE_PCT=18\nPOSITION_EXIT_TRAILING_DRAWDOWN_PCT=3.25\nPOSITION_EXIT_ENABLE_TRAILING_ON_TP2=false\n',
        encoding='utf-8',
    )

    settings = load_settings(config_path=str(config_path), env_path=str(env_path))

    assert settings.exit.tp1_reduce_pct == 18.0
    assert settings.exit.trailing_drawdown_pct == 3.25
    assert settings.exit.enable_trailing_on_tp2 is False
