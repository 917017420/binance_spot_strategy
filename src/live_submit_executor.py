from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .live_exchange_adapter import submit_live_order
from .live_order_payload import build_live_order_payload
from .live_submit_log import append_live_submit_plan
from .models import PairAnalysis


@dataclass
class LiveSubmitPlanResult:
    status: str
    message: str
    details: dict = field(default_factory=dict)



def _read_live_submit_debug_contract(candidate: PairAnalysis, debug_contract: dict | None = None) -> dict:
    if isinstance(debug_contract, dict) and debug_contract:
        return debug_contract

    metadata = getattr(candidate, 'metadata', None)
    if isinstance(metadata, dict):
        debug = metadata.get('debug') or {}
        live_submit = debug.get('live_submit') or {}
        if isinstance(live_submit, dict):
            return live_submit

    if bool(getattr(candidate, 'debug_force_live_submit_failure', False)):
        return {
            'force_failure': True,
            'failure_stage': getattr(candidate, 'debug_force_live_submit_failure_stage', 'adapter_call'),
            'failure_message': getattr(candidate, 'debug_force_live_submit_failure_message', 'forced live submit failure for queue retry path'),
        }
    return {}



def _apply_debug_force_failure(adapter_result, candidate: PairAnalysis, debug_contract: dict | None = None):
    debug_contract = _read_live_submit_debug_contract(candidate, debug_contract=debug_contract)
    if not bool(debug_contract.get('force_failure', False)):
        return adapter_result

    debug_force_stage = debug_contract.get('failure_stage', 'adapter_call')
    debug_force_message = debug_contract.get('failure_message', 'forced live submit failure for queue retry path')
    forced_error = {
        'type': 'RuntimeError',
        'message': debug_force_message,
        'stage': debug_force_stage,
        'recoverable': False,
        'raw': {
            'symbol': candidate.symbol,
            'debug_contract': debug_contract,
        },
    }
    return adapter_result.__class__(
        status='failed',
        message=debug_force_message,
        details={
            **adapter_result.details,
            'response': {
                **(adapter_result.details.get('response') or {}),
                'status': 'submit_failed',
            },
            'error': forced_error,
            'submit_contract': {
                **(adapter_result.details.get('submit_contract') or {}),
                'adapter_call_stage': debug_force_stage,
                'response_stage': 'submit_failed',
                'terminal_submit_status': 'submit_failed',
                'error_stage': debug_force_stage,
            },
            'debug_contract': debug_contract,
        },
    )



def build_live_submit_plan(candidate: PairAnalysis, total_equity_quote: float, settings: Settings | None = None, debug_contract: dict | None = None) -> LiveSubmitPlanResult:
    quote_amount_override = None
    if settings is not None:
        quote_amount_override = float(settings.auto_entry.live_order_quote_amount or 0.0)
    payload = build_live_order_payload(
        candidate,
        total_equity_quote=total_equity_quote,
        quote_amount=quote_amount_override,
    )
    adapter_result = submit_live_order(settings, payload)
    adapter_result = _apply_debug_force_failure(adapter_result, candidate, debug_contract=debug_contract)
    submit_enabled = bool(settings.api.enable_order_submit) if settings is not None else False

    record = {
        'symbol': payload.symbol,
        'side': payload.side,
        'order_type': payload.order_type,
        'quote_amount': payload.quote_amount,
        'reference_price': payload.reference_price,
        'requested_position_size_pct': payload.requested_position_size_pct,
        'resolved_quote_amount_source': payload.metadata.get('quote_amount_source'),
        'configured_live_order_quote_amount': quote_amount_override,
        'client_order_id': payload.client_order_id,
        'metadata': payload.metadata,
        'submit_enabled': submit_enabled,
        'idempotency_scope': f'live_submit_plan:{payload.client_order_id}',
        'exchange_adapter_status': adapter_result.status,
        'exchange_adapter_message': adapter_result.message,
        'exchange_submit_request': adapter_result.details.get('request'),
        'exchange_submit_exchange_params': adapter_result.details.get('exchange_params'),
        'exchange_submit_response': adapter_result.details.get('response'),
        'exchange_submit_error': adapter_result.details.get('error'),
        'exchange_submit_contract': adapter_result.details.get('submit_contract'),
        'exchange_submit_debug_contract': adapter_result.details.get('debug_contract'),
    }
    plan_path = append_live_submit_plan(record)
    return LiveSubmitPlanResult(
        status='planned',
        message='live submit plan logged and executed through configured adapter path' if submit_enabled else 'live submit skeleton logged; exchange order submit still disabled',
        details={
            **record,
            'plan_path': str(plan_path),
            'adapter_details': adapter_result.details,
            'next_step': 'replace stubbed submit_live_order with real exchange create_order + idempotent live order persistence',
        },
    )
