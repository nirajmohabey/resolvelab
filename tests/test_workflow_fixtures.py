"""Frozen requirements assert database effects, not merely classifier labels."""
import json

import pytest

from resolvelab.evaluate import FIXTURES, run_fixture_case

CASES = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("spec", CASES, ids=[c["id"] for c in CASES])
def test_frozen_workflow_case(spec):
    result = run_fixture_case(spec, reset=False)
    assert result["passed"], result
