"""Explicit, reproducible mechanism ablations; production defaults enable all."""
import os


ABLATIONS = {
    "none": frozenset(),
    "no_name_expansion": frozenset({"name_expansion"}),
    "no_feedback_retention": frozenset({"feedback_retention"}),
    "no_evidence_updates": frozenset({"name_expansion", "feedback_retention"}),
    "no_intent_recall": frozenset({"intent_recall"}),
    "no_column_alignment": frozenset({"column_alignment"}),
}


def mechanism_enabled(name):
    variant = os.getenv("NL2SQL_MECHANISM_ABLATION", "none")
    if variant not in ABLATIONS:
        raise ValueError(f"Unknown mechanism ablation: {variant}")
    if name not in set().union(*ABLATIONS.values()):
        raise ValueError(f"Unknown mechanism: {name}")
    return name not in ABLATIONS[variant]
