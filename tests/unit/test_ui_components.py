from kestrel.metrics.service import MetricValue
from kestrel.ui.components import format_metric_value


def _percentage(value: float) -> MetricValue:
    return MetricValue("test_rate", value, None, None, "percent", 0)


def test_percent_format_does_not_round_a_small_nonzero_rate_to_zero() -> None:
    assert format_metric_value(_percentage(0.0003330327)) == "0.033%"
    assert format_metric_value(_percentage(0.0000001)) == "<0.001%"
    assert format_metric_value(_percentage(0.0)) == "0.0%"
    assert format_metric_value(_percentage(0.857)) == "85.7%"
