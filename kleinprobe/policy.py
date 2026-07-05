"""
kleinprobe/policy.py
====================
Policy layer for KleinProbe v2. NOT YET IMPLEMENTED.

This module will contain adaptive execution policies that consume
DriftAnalysis and QueueDriftResult outputs and produce
StrategyRecommendation objects.

Design principle:
    KleinProbe measures.
    analyzer.py interprets.
    THIS MODULE recommends actions.
    Users decide whether to act.

Why this is not implemented yet:
    Policy design requires empirical evidence about which state
    changes consistently justify which actions. For example:
    - Does ΔH > 0.3 actually correlate with worse circuit fidelity?
    - Is 'change_seed' ever better than 'proceed'?
    - At what drift_score should a long experiment pause?

    These are empirical questions, not architectural ones.
    They will be answered by the sensitivity experiments and
    by data collected from real use of QueueDriftTracker.

    Implement this module after that evidence exists.

Planned interface (not yet active):

    from kleinprobe.policy import AdaptiveAdvisor

    advisor = AdaptiveAdvisor(analyzer)
    advice  = advisor.recommend(analysis)

    # StrategyRecommendation:
    #   action:     'proceed' | 'pause' | 'change_seed' | 'switch_backend'
    #   confidence: float 0-1
    #   reason:     str
    #   suggested_seed:    Optional[int]
    #   suggested_backend: Optional[str]
"""

raise NotImplementedError(
    "kleinprobe.policy is not yet implemented. "
    "Use kleinprobe.analyzer for drift analysis. "
    "See module docstring for design rationale."
)
