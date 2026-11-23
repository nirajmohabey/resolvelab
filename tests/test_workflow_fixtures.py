"""Frozen requirements assert database effects, not merely classifier labels."""
import json
from copy import deepcopy

import pytest

from resolvelab.evaluate import FIXTURES, run_fixture_case

CASES = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("spec", CASES, ids=[c["id"] for c in CASES])
def test_frozen_workflow_case(spec):
    result = run_fixture_case(spec, reset=False)
    assert result["passed"], result


def test_model_abstention_is_a_workflow_failure_not_runner_crash():
    spec=deepcopy(next(case for case in CASES if case['id']=='AI-E14'))
    spec['faults']={'routing_adapter':{'predicted_intent':'cancel_request','routing_score':.3,'validation_threshold':.8}}
    result=run_fixture_case(spec,reset=False)
    assert result['passed'] is False
    assert result['runner_error'] is None
    assert result['failure_category']=='invariant_failed'
    assert 'all_tenant_order_effects_inspected' in result['checks']
