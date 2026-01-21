"""Metrics display widget for TUI.

Shows running cost total in the header/status bar.
"""
from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


class MetricsSummary(Static):
    """Shows running cost in the status bar.

    Displays the total cost accumulated across all LLM calls
    in the current session.
    """

    DEFAULT_CSS = """
    MetricsSummary {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: transparent;
    }

    MetricsSummary.has-cost {
        color: $success;
    }

    MetricsSummary.over-budget {
        color: $error;
    }
    """

    cost: reactive[float] = reactive(0.0)
    budget: reactive[float | None] = reactive(None)

    def render(self) -> str:
        """Render the cost display."""
        if self.cost == 0.0:
            return ""

        cost_str = f"${self.cost:.2f}"

        if self.budget is not None:
            remaining = self.budget - self.cost
            if remaining < 0:
                return f"{cost_str} (over budget!)"
            return f"{cost_str} / ${self.budget:.2f}"

        return cost_str

    def watch_cost(self, cost: float) -> None:
        """Update styling when cost changes."""
        self.remove_class("has-cost", "over-budget")
        if cost > 0:
            self.add_class("has-cost")
        if self.budget is not None and cost > self.budget:
            self.add_class("over-budget")

    def update_cost(self, cost: float) -> None:
        """Update the displayed cost.

        Args:
            cost: Total cost in USD.
        """
        self.cost = cost

    def set_budget(self, budget: float | None) -> None:
        """Set an optional budget limit for display.

        Args:
            budget: Budget limit in USD, or None for no limit.
        """
        self.budget = budget
