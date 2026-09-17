"""
Tests for alerting.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from app.alerting import AlertLevel, AlertManager
from app.metrics import MetricsSnapshot


def _ok_snapshot(**overrides) -> MetricsSnapshot:
    """Return a MetricsSnapshot with all-ok defaults, overridden as needed."""
    defaults = dict(
        uptime_seconds=100.0,
        total_requests=100,
        error_rate_pct=0.0,
        latency_p50_ms=50.0,
        latency_p95_ms=200.0,
        latency_p99_ms=500.0,
        cache_hit_rate_pct=80.0,
        cache_operations=100,
    )
    defaults.update(overrides)
    return MetricsSnapshot(**defaults)


class TestAlertManager:
    def _make_manager(self) -> AlertManager:
        """Return a fresh AlertManager (no leftover cooldown state)."""
        return AlertManager()

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_no_alert_when_metrics_ok(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot()
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        alerts = am.check_alerts()
        assert alerts == []

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_critical_alert_on_high_error_rate(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(error_rate_pct=15.0)
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        alerts = am.check_alerts()
        assert len(alerts) == 1
        assert alerts[0].level is AlertLevel.CRITICAL
        assert alerts[0].rule == "error_rate"

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_critical_alert_on_cb_open_over_5min(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot()
        mock_sb.all_statuses.return_value = {
            "ors": {
                "name": "ors",
                "state": "OPEN",
                "consecutive_failures": 10,
                "last_failure_time": 1.0,
                "time_in_state_seconds": 400.0,
            }
        }

        am = self._make_manager()
        alerts = am.check_alerts()
        assert len(alerts) == 1
        assert alerts[0].level is AlertLevel.CRITICAL
        assert alerts[0].rule == "cb_open:ors"

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_warning_alert_on_high_latency(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(latency_p95_ms=4000.0)
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        alerts = am.check_alerts()
        assert len(alerts) == 1
        assert alerts[0].level is AlertLevel.WARNING
        assert alerts[0].rule == "latency_p95"

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_warning_alert_on_low_cache_rate(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(cache_hit_rate_pct=20.0)
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        alerts = am.check_alerts()
        assert len(alerts) == 1
        assert alerts[0].level is AlertLevel.WARNING
        assert alerts[0].rule == "cache_hit_rate"

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_cooldown_prevents_duplicate_alerts(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(error_rate_pct=15.0)
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        first = am.check_alerts()
        assert len(first) == 1
        second = am.check_alerts()
        assert len(second) == 0  # cooldown active

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_cooldown_expires_alert_fires_again(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(error_rate_pct=15.0)
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        first = am.check_alerts()
        assert len(first) == 1

        # Advance time past critical cooldown (300s)
        with patch("app.alerting.time.monotonic", return_value=time.monotonic() + 301):
            again = am.check_alerts()
        assert len(again) == 1

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_multiple_simultaneous_alerts(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(
            error_rate_pct=15.0,
            latency_p95_ms=5000.0,
        )
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        alerts = am.check_alerts()
        rules = {a.rule for a in alerts}
        assert "error_rate" in rules
        assert "latency_p95" in rules

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_maybe_check_respects_throttle(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot()
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        result1 = am.maybe_check()
        assert result1 is not None  # first call always executes

        result2 = am.maybe_check()
        assert result2 is None  # throttled

    @patch("app.alerting.metrics_collector")
    @patch("app.alerting.service_breakers")
    def test_alert_logged_with_correct_level(self, mock_sb, mock_mc):
        mock_mc.snapshot.return_value = _ok_snapshot(error_rate_pct=15.0)
        mock_sb.all_statuses.return_value = {}

        am = self._make_manager()
        with patch("app.alerting.logger") as mock_logger:
            alerts = am.check_alerts()
            assert len(alerts) == 1
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args
            assert call_kwargs.kwargs.get("extra", {}).get("alert") is True
