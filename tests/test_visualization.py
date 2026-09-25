"""Tests for visualization components."""

from datetime import datetime
from unittest.mock import MagicMock

from stock_cycle_tracker.models import OHLCV, PivotPoint, PivotType
from stock_cycle_tracker.visualization.plotly_chart import create_candlestick_chart


class TestPlotlyChart:
    """Tests for Plotly chart creation."""

    def test_create_candlestick_chart(self):
        """Test basic candlestick chart creation."""
        now = datetime.now()
        data = [
            OHLCV(timestamp=now, open=100, high=105, low=95, close=102, volume=100),
            OHLCV(timestamp=now, open=102, high=107, low=100, close=105, volume=150),
        ]

        fig = create_candlestick_chart(data)

        # Verify figure has traces
        assert fig is not None
        assert len(fig.data) >= 1

    def test_create_chart_with_pivots(self):
        """Test chart creation with pivot points."""
        now = datetime.now()
        data = [
            OHLCV(timestamp=now, open=100, high=105, low=95, close=102, volume=100),
        ]

        pivots = [
            PivotPoint(
                index=0,
                timestamp=now,
                price=105,
                pivot_type=PivotType.SWING_HIGH,
                source_candle_index=0,
            ),
        ]

        fig = create_candlestick_chart(data, pivots=pivots)

        assert fig is not None

    def test_create_chart_with_legs(self):
        """Test chart creation with swing legs."""
        now = datetime.now()
        data = [
            OHLCV(timestamp=now, open=100, high=105, low=95, close=102, volume=100),
        ]

        legs = [
            MagicMock(
                start_pivot=PivotPoint(index=0, timestamp=now, price=100, pivot_type=PivotType.SWING_HIGH, source_candle_index=0),
                end_pivot=PivotPoint(index=1, timestamp=now, price=95, pivot_type=PivotType.SWING_LOW, source_candle_index=1),
                percent_change=-5.0,
                duration_seconds=3600,
                duration_bars=1,
                direction="down",
            ),
        ]

        fig = create_candlestick_chart(data, legs=legs)

        assert fig is not None

