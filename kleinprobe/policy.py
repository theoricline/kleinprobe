"""
kleinprobe/policy.py
====================
Policy layer for KleinProbe. Importable but not yet implemented.

Design principle:
    KleinProbe measures.
    analyzer.py interprets.
    THIS MODULE recommends actions.
    Users decide whether to act.

Why policies are not yet implemented:
    Policy design requires empirical evidence about which state
    changes consistently justify which actions. For example:
    - Does ΔH > 0.3 actually correlate with worse circuit fidelity?
    - Is 'change_seed' ever better than 'proceed'?
    - At what drift_score should a long experiment pause?

    These are empirical questions. They will be answered by the
    seed variation sensitivity experiments and by data collected
    from real use of QueueDriftTracker.

    Implement concrete policies after that evidence exists.

Current contents:
    PolicyBase  — abstract base class for all future policies
    NullPolicy  — no-op implementation (safe default)
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass


# ── StrategyRecommendation ────────────────────────────────────────────────

@dataclass
class StrategyRecommendation:
    """
    Output of a Policy.recommend() call.

    action:            what to do
    confidence:        0-1, how confident the policy is
    reason:            human-readable explanation
    suggested_seed:    optional transpiler seed to try instead
    suggested_backend: optional backend to switch to
    """
    action:            str              # 'proceed' | 'pause' | 'change_seed' | 'switch_backend'
    confidence:        float            # 0-1
    reason:            str
    suggested_seed:    Optional[int]    = None
    suggested_backend: Optional[str]   = None

    def __repr__(self):
        return (f"StrategyRecommendation(action='{self.action}', "
                f"confidence={self.confidence:.2f}, "
                f"reason='{self.reason}')")


# ── PolicyBase ────────────────────────────────────────────────────────────

class PolicyBase(ABC):
    """
    Abstract base class for KleinProbe execution policies.

    A policy consumes a DriftAnalysis or QueueDriftResult
    and returns a StrategyRecommendation.

    KleinProbe never calls policies internally.
    Policies are strictly optional components that users
    may build on top of the observer layer.

    To implement a policy:
        class MyPolicy(PolicyBase):
            def recommend(self, analysis) -> StrategyRecommendation:
                if analysis.trend == 'degraded':
                    return StrategyRecommendation(
                        action='pause',
                        confidence=0.8,
                        reason=f"H drifted {analysis.max_drift.dH:+.3f} bits"
                    )
                return StrategyRecommendation(
                    action='proceed', confidence=1.0, reason="stable")
    """

    @abstractmethod
    def recommend(self, analysis) -> StrategyRecommendation:
        """
        Produce a StrategyRecommendation from a DriftAnalysis
        or QueueDriftResult.

        Args:
            analysis: DriftAnalysis or QueueDriftResult

        Returns:
            StrategyRecommendation
        """


# ── NullPolicy ────────────────────────────────────────────────────────────

class NullPolicy(PolicyBase):
    """
    No-op policy: always recommends 'proceed' with full confidence.

    Safe default. Use when you want the policy interface
    without any adaptive behaviour.

    Example:
        policy = NullPolicy()
        rec = policy.recommend(analysis)
        # rec.action == 'proceed', rec.confidence == 1.0
    """

    def recommend(self, analysis) -> StrategyRecommendation:
        return StrategyRecommendation(
            action='proceed',
            confidence=1.0,
            reason="NullPolicy: no adaptive behaviour defined"
        )
