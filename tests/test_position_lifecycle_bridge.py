from src.models import PositionActionResult
from src.position_lifecycle_bridge import build_lifecycle_view_from_action


def test_build_lifecycle_view_from_live_reduce_submit_stays_pending_fill():
    action = PositionActionResult(
        action_id='action-reduce-live',
        position_id='pos-live-ada',
        symbol='ADA/USDT',
        mode='live',
        action='SELL_REDUCE',
        status='submitted',
        executed_at='2026-03-26T00:00:00Z',
        resulting_position_status='open',
        message='submitted',
        details={},
    )

    view = build_lifecycle_view_from_action(action)

    assert view.lifecycle_stage == 'reduce_pending_fill'
    assert 'awaiting fill reconcile' in view.notes[0]



def test_build_lifecycle_view_from_live_exit_submit_stays_pending_fill():
    action = PositionActionResult(
        action_id='action-exit-live',
        position_id='pos-live-ada',
        symbol='ADA/USDT',
        mode='live',
        action='SELL_EXIT',
        status='submitted',
        executed_at='2026-03-26T00:00:00Z',
        resulting_position_status='open',
        message='submitted',
        details={},
    )

    view = build_lifecycle_view_from_action(action)

    assert view.lifecycle_stage == 'exit_pending_fill'
    assert 'awaiting fill reconcile' in view.notes[0]


def test_build_lifecycle_view_from_live_reduce_skip_stays_pending_fill():
    action = PositionActionResult(
        action_id='action-reduce-live-skip',
        position_id='pos-live-ada',
        symbol='ADA/USDT',
        mode='live',
        action='SELL_REDUCE',
        status='skipped',
        executed_at='2026-03-26T00:00:00Z',
        resulting_position_status='partially_reduced',
        message='skipped',
        details={'skip_reason': 'pending_live_management_order_exists'},
    )

    view = build_lifecycle_view_from_action(action)

    assert view.lifecycle_stage == 'reduce_pending_fill'
    assert 'resubmit suppressed' in view.notes[0]


def test_build_lifecycle_view_from_live_exit_skip_stays_pending_fill():
    action = PositionActionResult(
        action_id='action-exit-live-skip',
        position_id='pos-live-ada',
        symbol='ADA/USDT',
        mode='live',
        action='SELL_EXIT',
        status='skipped',
        executed_at='2026-03-26T00:00:00Z',
        resulting_position_status='open',
        message='skipped',
        details={'skip_reason': 'pending_live_management_order_exists'},
    )

    view = build_lifecycle_view_from_action(action)

    assert view.lifecycle_stage == 'exit_pending_fill'
    assert 'resubmit suppressed' in view.notes[0]
