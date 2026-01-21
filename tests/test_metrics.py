"""Tests for LLM metrics tracking."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoints.llm.metrics import (
    Budget,
    BudgetExceededError,
    LLMCall,
    MetricsCollector,
)


@dataclass
class MockProject:
    """Mock project for testing MetricsCollector."""

    path: Path

    def get_path(self) -> Path:
        return self.path


class TestLLMCall:
    """Tests for LLMCall dataclass."""

    def test_create_llmcall(self) -> None:
        """Test creating an LLMCall with auto-generated ID and timestamp."""
        call = LLMCall.create(
            phase="ideation-qa",
            cost_usd=0.05,
            latency_ms=1500,
            model="claude-3-5-sonnet",
        )

        assert call.call_id is not None
        assert len(call.call_id) == 8  # UUID[:8]
        assert call.phase == "ideation-qa"
        assert call.cost_usd == 0.05
        assert call.latency_ms == 1500
        assert call.model == "claude-3-5-sonnet"
        assert call.success is True
        assert call.error is None
        assert call.waypoint_id is None
        assert call.timestamp is not None

    def test_create_llmcall_with_error(self) -> None:
        """Test creating an LLMCall with error."""
        call = LLMCall.create(
            phase="fly",
            cost_usd=0.0,
            latency_ms=500,
            waypoint_id="WP-1",
            success=False,
            error="Connection timeout",
        )

        assert call.success is False
        assert call.error == "Connection timeout"
        assert call.waypoint_id == "WP-1"

    def test_llmcall_to_dict(self) -> None:
        """Test serializing LLMCall to dict."""
        timestamp = datetime.now(UTC)
        call = LLMCall(
            call_id="test123",
            phase="chart",
            waypoint_id=None,
            cost_usd=0.10,
            latency_ms=2000,
            model="claude-3-5-sonnet",
            timestamp=timestamp,
            success=True,
            error=None,
        )

        data = call.to_dict()

        assert data["call_id"] == "test123"
        assert data["phase"] == "chart"
        assert data["waypoint_id"] is None
        assert data["cost_usd"] == 0.10
        assert data["latency_ms"] == 2000
        assert data["model"] == "claude-3-5-sonnet"
        assert data["timestamp"] == timestamp.isoformat()
        assert data["success"] is True
        assert data["error"] is None

    def test_llmcall_from_dict(self) -> None:
        """Test deserializing LLMCall from dict."""
        timestamp = datetime.now(UTC)
        data = {
            "call_id": "abc123",
            "phase": "product-spec",
            "waypoint_id": "WP-2",
            "cost_usd": 0.25,
            "latency_ms": 3000,
            "model": "claude-3-5-sonnet",
            "timestamp": timestamp.isoformat(),
            "success": True,
            "error": None,
        }

        call = LLMCall.from_dict(data)

        assert call.call_id == "abc123"
        assert call.phase == "product-spec"
        assert call.waypoint_id == "WP-2"
        assert call.cost_usd == 0.25
        assert call.latency_ms == 3000
        assert call.timestamp.isoformat() == timestamp.isoformat()


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_empty_collector(self, tmp_path: Path) -> None:
        """Test collector with no calls."""
        collector = MetricsCollector(MockProject(tmp_path))

        assert collector.total_cost == 0.0
        assert collector.total_calls == 0
        assert collector.cost_by_phase() == {}
        assert collector.cost_by_waypoint() == {}

    def test_record_calls(self, tmp_path: Path) -> None:
        """Test recording multiple calls."""
        collector = MetricsCollector(MockProject(tmp_path))

        call1 = LLMCall.create(phase="ideation-qa", cost_usd=0.05, latency_ms=1000)
        call2 = LLMCall.create(phase="idea-brief", cost_usd=0.10, latency_ms=1500)
        call3 = LLMCall.create(phase="ideation-qa", cost_usd=0.03, latency_ms=800)

        collector.record(call1)
        collector.record(call2)
        collector.record(call3)

        assert collector.total_calls == 3
        assert collector.total_cost == pytest.approx(0.18)

    def test_cost_by_phase(self, tmp_path: Path) -> None:
        """Test aggregating cost by phase."""
        collector = MetricsCollector(MockProject(tmp_path))

        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=0.05, latency_ms=1000)
        )
        collector.record(
            LLMCall.create(phase="idea-brief", cost_usd=0.10, latency_ms=1500)
        )
        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=0.03, latency_ms=800)
        )
        collector.record(LLMCall.create(phase="chart", cost_usd=0.15, latency_ms=2000))

        by_phase = collector.cost_by_phase()

        assert by_phase["ideation-qa"] == pytest.approx(0.08)
        assert by_phase["idea-brief"] == pytest.approx(0.10)
        assert by_phase["chart"] == pytest.approx(0.15)

    def test_cost_by_waypoint(self, tmp_path: Path) -> None:
        """Test aggregating cost by waypoint."""
        collector = MetricsCollector(MockProject(tmp_path))

        collector.record(
            LLMCall.create(
                phase="fly", cost_usd=0.20, latency_ms=5000, waypoint_id="WP-1"
            )
        )
        collector.record(
            LLMCall.create(
                phase="fly", cost_usd=0.15, latency_ms=4000, waypoint_id="WP-1"
            )
        )
        collector.record(
            LLMCall.create(
                phase="fly", cost_usd=0.30, latency_ms=6000, waypoint_id="WP-2"
            )
        )
        # Call without waypoint_id should not appear in cost_by_waypoint
        collector.record(LLMCall.create(phase="chart", cost_usd=0.10, latency_ms=2000))

        by_waypoint = collector.cost_by_waypoint()

        assert by_waypoint["WP-1"] == pytest.approx(0.35)
        assert by_waypoint["WP-2"] == pytest.approx(0.30)
        assert "chart" not in by_waypoint

    def test_persistence(self, tmp_path: Path) -> None:
        """Test that metrics persist across collector instances."""
        # First collector records some calls
        collector1 = MetricsCollector(MockProject(tmp_path))
        collector1.record(
            LLMCall.create(phase="ideation-qa", cost_usd=0.05, latency_ms=1000)
        )
        collector1.record(
            LLMCall.create(phase="idea-brief", cost_usd=0.10, latency_ms=1500)
        )

        # Second collector loads from same path
        collector2 = MetricsCollector(MockProject(tmp_path))

        assert collector2.total_calls == 2
        assert collector2.total_cost == pytest.approx(0.15)

    def test_metrics_file_format(self, tmp_path: Path) -> None:
        """Test that metrics are stored as JSONL with header."""
        collector = MetricsCollector(MockProject(tmp_path))
        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=0.05, latency_ms=1000)
        )

        # Read the file directly
        metrics_file = tmp_path / "metrics.jsonl"
        assert metrics_file.exists()

        with open(metrics_file) as f:
            lines = f.readlines()

        # Should have header + 1 call entry
        assert len(lines) == 2

        # First line is header with schema version
        header = json.loads(lines[0])
        assert header["_schema"] == "metrics"
        assert header["_version"] == "1.0"
        assert "created_at" in header

        # Second line is the call data
        data = json.loads(lines[1])
        assert data["phase"] == "ideation-qa"
        assert data["cost_usd"] == 0.05

    def test_summary(self, tmp_path: Path) -> None:
        """Test generating summary statistics."""
        collector = MetricsCollector(MockProject(tmp_path))

        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=0.05, latency_ms=1000)
        )
        collector.record(
            LLMCall.create(
                phase="fly",
                cost_usd=0.20,
                latency_ms=5000,
                waypoint_id="WP-1",
                success=False,
                error="Test error",
            )
        )
        collector.record(
            LLMCall.create(phase="idea-brief", cost_usd=0.10, latency_ms=1500)
        )

        summary = collector.summary()

        assert summary["total_calls"] == 3
        assert summary["total_cost_usd"] == pytest.approx(0.35)
        assert summary["avg_latency_ms"] == pytest.approx(2500)
        assert summary["success_rate"] == pytest.approx(2 / 3)
        assert "ideation-qa" in summary["cost_by_phase"]
        assert "WP-1" in summary["cost_by_waypoint"]


class TestBudget:
    """Tests for Budget class."""

    def test_no_budget(self, tmp_path: Path) -> None:
        """Test budget with no limit."""
        budget = Budget()
        collector = MetricsCollector(MockProject(tmp_path))

        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=100.0, latency_ms=1000)
        )

        # Should not raise even with high cost
        budget.check(collector)

    def test_budget_not_exceeded(self, tmp_path: Path) -> None:
        """Test budget when under limit."""
        budget = Budget(max_usd=10.0)
        collector = MetricsCollector(MockProject(tmp_path))

        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=5.0, latency_ms=1000)
        )

        # Should not raise
        budget.check(collector)
        assert budget.remaining(collector) == pytest.approx(5.0)

    def test_budget_exceeded(self, tmp_path: Path) -> None:
        """Test budget when over limit."""
        budget = Budget(max_usd=1.0)
        collector = MetricsCollector(MockProject(tmp_path))

        collector.record(
            LLMCall.create(phase="ideation-qa", cost_usd=1.50, latency_ms=1000)
        )

        with pytest.raises(BudgetExceededError) as exc_info:
            budget.check(collector)

        assert exc_info.value.limit_type == "cost"
        assert exc_info.value.current_value == pytest.approx(1.50)
        assert exc_info.value.limit_value == pytest.approx(1.0)

    def test_budget_remaining_none(self, tmp_path: Path) -> None:
        """Test remaining when no budget set."""
        budget = Budget()
        collector = MetricsCollector(MockProject(tmp_path))

        assert budget.remaining(collector) is None

    def test_budget_serialization(self) -> None:
        """Test budget serialization."""
        budget = Budget(max_usd=50.0)

        data = budget.to_dict()
        restored = Budget.from_dict(data)

        assert restored.max_usd == 50.0
