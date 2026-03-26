from __future__ import annotations

from types import SimpleNamespace

from src.auto_runner import _run_scan_step
from src.live_queue_admission_policy import derive_live_queue_admission_policy
from src.config import Settings, UniverseSettings
from src.auto_runner_preview_samples import build_preview_sample_candidates


class _FakeExchange:
    def close(self):
        return None


def test_run_scan_step_uses_settings_max_symbols_when_not_overridden(monkeypatch):
    settings = Settings(universe=UniverseSettings(max_symbols=8))
    scanned_symbols: list[str] = []

    monkeypatch.setattr('src.auto_runner.load_settings', lambda config_path, env_file: settings)
    monkeypatch.setattr('src.auto_runner.create_exchange', lambda settings: _FakeExchange())
    monkeypatch.setattr(
        'src.auto_runner.build_symbol_universe',
        lambda exchange, settings, top=None: ([f'SYM{i}/USDT' for i in range(10)], [], {f'SYM{i}/USDT': 1_000_000.0 for i in range(10)} | {'BTC/USDT': 2_000_000.0}),
    )
    monkeypatch.setattr('src.auto_runner.fetch_ohlcv_dataframe', lambda *args, **kwargs: object())
    monkeypatch.setattr(
        'src.auto_runner.evaluate_market_regime',
        lambda *args, **kwargs: SimpleNamespace(regime='risk_on', indicators_1h=SimpleNamespace(change_24h_pct=1.0)),
    )

    template = build_preview_sample_candidates(regime='risk_on')[0]

    def _fake_scan_symbol(exchange, symbol, quote_volume_24h, settings_arg, regime):
        scanned_symbols.append(symbol)
        candidate = template.model_copy(deep=True)
        candidate.symbol = symbol
        return candidate

    monkeypatch.setattr('src.auto_runner._scan_symbol', _fake_scan_symbol)
    monkeypatch.setattr('src.auto_runner.split_priority_and_secondary', lambda analyses, top: (analyses, []))

    _run_scan_step(config_path='config/strategy.example.yaml', env_file='.env', max_scan_symbols=None)

    assert scanned_symbols == [f'SYM{i}/USDT' for i in range(8)]


def test_run_scan_step_promotes_top_priority_candidate_to_live_route(monkeypatch):
    settings = Settings(universe=UniverseSettings(max_symbols=1))

    monkeypatch.setattr('src.auto_runner.load_settings', lambda config_path, env_file: settings)
    monkeypatch.setattr('src.auto_runner.create_exchange', lambda settings: _FakeExchange())
    monkeypatch.setattr(
        'src.auto_runner.build_symbol_universe',
        lambda exchange, settings, top=None: (['LINK/USDT'], [], {'LINK/USDT': 1_500_000.0, 'BTC/USDT': 2_000_000.0}),
    )
    monkeypatch.setattr('src.auto_runner.fetch_ohlcv_dataframe', lambda *args, **kwargs: object())
    monkeypatch.setattr(
        'src.auto_runner.evaluate_market_regime',
        lambda *args, **kwargs: SimpleNamespace(regime='risk_on', indicators_1h=SimpleNamespace(change_24h_pct=1.0)),
    )

    candidate = build_preview_sample_candidates(regime='risk_on')[0]
    candidate.symbol = 'LINK/USDT'
    candidate.execution_stage = 'MANUAL_CONFIRMATION'
    candidate.attention_level = 'HIGH'

    monkeypatch.setattr('src.auto_runner._scan_symbol', lambda *args, **kwargs: candidate.model_copy(deep=True))
    monkeypatch.setattr('src.auto_runner.split_priority_and_secondary', lambda analyses, top: (analyses, []))
    monkeypatch.setattr(
        'src.auto_runner.execute_route_candidate',
        lambda candidate, route, mode, total_equity_quote, settings=None: SimpleNamespace(
            symbol=candidate.symbol,
            route=route,
            mode=mode or 'none',
            status='planned' if route == 'live' else 'armed',
            message='preview',
            details={},
        ),
    )
    monkeypatch.setattr(
        'src.auto_runner.build_route_lifecycle_view',
        lambda candidate, route_exec: SimpleNamespace(
            execution_status=route_exec.status,
            execution_mode=route_exec.mode,
            position_init_status='awaiting_matching_mode',
            position_path=None,
            position_event_path=None,
            entry_action_path=None,
            execution_path=None,
            notes=[],
        ),
    )
    monkeypatch.setattr(
        'src.auto_runner.derive_enqueue_admission_precheck',
        lambda **kwargs: derive_live_queue_admission_policy(
            symbol=kwargs['symbol'],
            route=kwargs['route'],
            stale_count=0,
            cooldown_count=0,
            inflight_pending_count=0,
        ),
    )

    _, _, _, _, shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates = _run_scan_step(
        config_path='config/strategy.example.yaml',
        env_file='.env',
        action_mode='live',
    )

    assert shortlist_symbols == ['LINK/USDT']
    assert route_summaries[0]['route'] == 'live'
    assert execution_previews[0]['execution_status'] == 'planned'
    assert readiness_summaries[0]['route'] == 'live'
    if queued_candidates:
        assert queued_candidates[0]['route'] == 'live'
        assert queued_candidates[0]['candidate_snapshot']['execution_stage'] == 'IMMEDIATE_ATTENTION'
