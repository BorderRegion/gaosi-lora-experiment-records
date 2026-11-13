"""Compare every noise field exactly except the explicitly recorded wall-clock timer."""
import math


def noise_pair(left, right):
    assert isinstance(left, dict) and isinstance(right, dict)
    assert set(left) == set(right) and 'boundary_seconds' in left
    for value in [left['boundary_seconds'], right['boundary_seconds']]:
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        assert math.isfinite(value) and value >= 0
    assert {k: v for k, v in left.items() if k != 'boundary_seconds'} == {
        k: v for k, v in right.items() if k != 'boundary_seconds'}
    return {'all_non_timing_fields_exactly_equal': True,
            'excluded_fields': ['boundary_seconds'],
            'left_boundary_seconds': left['boundary_seconds'],
            'right_boundary_seconds': right['boundary_seconds']}
