from __future__ import annotations

from types import SimpleNamespace

from src.auto_runner import _build_preview_for_candidates
from src.auto_runner_preview_samples import build_preview_sample_candidates
from src.config import Settings
from src.live_queue_admission_policy import derive_live_queue_admission_policy


def _blocking_state(lock_reason: str, *, active_symbol: str | None = None, source_details: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        lock=SimpleNamespace(
            blocking=True,
            active_symbol=active_symbol,
            lock_reason=lock_reason,
            lock_owner='single_active_trade_state',
            source_details=source_details or {},
        )
    )


def test_preview_surfaces_live_admission_precheck(monkeypatch):
    monkeypatch.setattr(
        'src.live_queue_admission_policy.build_single_active_trade_state',
        lambda base_dir=None: _blocking_state(
            'live_domain_symbol_conflict',
            active_symbol='ETH/USDT',
            source_details={'observed_symbols': ['TRX/USDT', 'ETH/USDT']},
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
    monkeypatch.setattr(
        'src.auto_runner.evaluate_auto_entry',
        lambda *args, **kwargs: SimpleNamespace(allow=True, route='live', severity='info', score=120, reasons=[]),
    )
    monkeypatch.setattr(
        'src.auto_runner.execute_route_candidate',
        lambda candidate, route, mode, total_equity_quote, settings=None: SimpleNamespace(
            symbol=candidate.symbol,
            route=route,
            mode=mode or 'none',
            status='armed',
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

    candidate = build_preview_sample_candidates(regime='neutral')[0]
    shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates = _build_preview_for_candidates(
        Settings(),
        'neutral',
        [candidate],
        'scan_plus_monitor',
    )

    assert shortlist_symbols == ['TRX/USDT']
    assert route_summaries[0]['symbol'] == 'TRX/USDT'
    assert route_summaries[0]['route'] == 'live'
    assert execution_previews[0]['execution_status'] == 'armed'

    readiness = readiness_summaries[0]
    assert readiness['symbol'] == 'TRX/USDT'
    assert readiness['execution_ready'] is True
    assert readiness['can_progress_to_live_execution'] is False
    assert readiness['primary_blocked_reason'] == 'live_domain_symbol_conflict'
    assert readiness['blocked_reasons'] == ['live_domain_symbol_conflict']
    assert readiness['admission_precheck']['primary_blocked_reason'] == 'live_domain_symbol_conflict'
    assert readiness['admission_precheck']['blocked_reasons_by_source']['single_active_trade'] == ['live_domain_symbol_conflict']
    assert queued_candidates == []


def test_preview_live_mode_only_queues_live_routes(monkeypatch):
    candidates = build_preview_sample_candidates(regime='neutral')[:2]

    monkeypatch.setattr(
        'src.live_queue_admission_policy.build_single_active_trade_state',
        lambda base_dir=None: SimpleNamespace(
            lock=SimpleNamespace(
                blocking=False,
                active_symbol=None,
                lock_reason=None,
                lock_owner=None,
                source_details={},
            )
        ),
    )

    decisions = iter([
        SimpleNamespace(allow=True, route='live', severity='info', score=120, reasons=[]),
        SimpleNamespace(allow=True, route='paper', severity='soft', score=90, reasons=['manual downgrade']),
    ])
    monkeypatch.setattr('src.auto_runner.evaluate_auto_entry', lambda *args, **kwargs: next(decisions))
    monkeypatch.setattr(
        'src.auto_runner.execute_route_candidate',
        lambda candidate, route, mode, total_equity_quote, settings=None: SimpleNamespace(
            symbol=candidate.symbol,
            route=route,
            mode=mode or 'none',
            status='armed',
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

    _, _, _, readiness_summaries, queued_candidates = _build_preview_for_candidates(
        Settings(),
        'neutral',
        candidates,
        'scan_plus_monitor',
        action_mode='live',
    )

    assert [item['route'] for item in readiness_summaries] == ['live', 'paper']
    assert [item['route'] for item in queued_candidates] == ['live']
