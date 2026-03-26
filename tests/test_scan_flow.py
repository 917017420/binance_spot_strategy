from __future__ import annotations

from src.auto_runner_preview_samples import build_preview_sample_candidates
from src.scan_flow import apply_ranked_candidate_handoff


def test_apply_ranked_candidate_handoff_assigns_shared_scan_semantics():
    priority = build_preview_sample_candidates(regime='risk_on')[:2]
    secondary = build_preview_sample_candidates(regime='risk_on')[2:]

    for candidate in priority + secondary:
        candidate.execution_stage = 'MANUAL_CONFIRMATION'
        candidate.attention_level = 'LOW'

    apply_ranked_candidate_handoff(priority, secondary)

    assert priority[0].execution_stage == 'IMMEDIATE_ATTENTION'
    assert priority[0].attention_level == 'HIGH'
    assert priority[1].execution_stage == 'MANUAL_CONFIRMATION'
    assert priority[1].attention_level == 'HIGH'
    assert secondary[0].execution_stage == 'MONITOR_ONLY'
    assert secondary[0].attention_level == 'MEDIUM'
