"""
Learning stage package: production outcome measurement, cost attribution, and feedback loops.
"""

from product_value_system.learning.analytics_adapter import (
    AnalyticsAdapter,
    MetricResult,
    MockAnalyticsAdapter,
    PrometheusAnalyticsAdapter,
    SQLAnalyticsAdapter,
    TimeseriesResult,
)
from product_value_system.learning.attribution import (
    CostAttributionReport,
    CostAttributor,
)
from product_value_system.learning.feedback_loop import (
    DiscoveryFeedbackItem,
    FeedbackAlert,
    FeedbackLoop,
)
from product_value_system.learning.measurement_agent import MeasurementAgent
from product_value_system.learning.outcome_tracker import (
    OutcomeMeasurement,
    OutcomeTracker,
)

__all__ = [
    "AnalyticsAdapter",
    "CostAttributionReport",
    "CostAttributor",
    "DiscoveryFeedbackItem",
    "FeedbackAlert",
    "FeedbackLoop",
    "MeasurementAgent",
    "MetricResult",
    "MockAnalyticsAdapter",
    "OutcomeMeasurement",
    "OutcomeTracker",
    "PrometheusAnalyticsAdapter",
    "SQLAnalyticsAdapter",
    "TimeseriesResult",
]
