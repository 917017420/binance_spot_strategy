from __future__ import annotations

from types import SimpleNamespace

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


def test_live_queue_admission_contract_keeps_primary_and_all_blocked_reasons(monkeypatch):
    monkeypatch.setattr(
        'src.live_queue_admission_policy.build_single_active_trade_state',
        lambda base_dir=None: _blocking_state(
            'live_domain_symbol_conflict',
            active_symbol='ETH/USDT',
            source_details={'observed_symbols': ['BTC/USDT', 'ETH/USDT']},
        ),
    )

    decision = derive_live_queue_admission_policy(
        symbol='BTC/USDT',
        route='live',
        stale_count=1,
        cooldown_count=0,
        inflight_pending_count=1,
    )

    assert decision.allow_enqueue is False
    assert decision.allow_process is False
    assert decision.blocked_reason == 'stale_live_inflight_detected'
    assert decision.blocked_reasons == [
        'stale_live_inflight_detected',
        'live_submit_inflight_pending',
        'live_domain_symbol_conflict',
    ]

    contract = decision.to_contract()

    assert contract['primary_blocked_reason'] == 'stale_live_inflight_detected'
    assert contract['blocked_reasons'] == decision.blocked_reasons
    assert contract['blocked_reasons_by_source'] == {
        'live_gate': ['stale_live_inflight_detected', 'live_submit_inflight_pending'],
        'single_active_trade': ['live_domain_symbol_conflict'],
        'queue_record': [],
    }
    assert contract['live_gate']['blocked_reason'] == 'stale_live_inflight_detected'
    assert contract['single_active_trade']['lock_reason'] == 'live_domain_symbol_conflict'
    assert contract['single_active_trade']['active_symbol'] == 'ETH/USDT'
