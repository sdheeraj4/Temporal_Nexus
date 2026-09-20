"""Small custom ML-assisted correlation baseline.

The model is intentionally lightweight and dependency-free so the hackathon build can
run offline. It is trained deterministically on synthetic feature vectors that model
same-person and different-person record pairs. Its output is an association score,
NOT a calibrated identity probability and never overrides explicit conflicts.
"""
from __future__ import annotations

from functools import lru_cache
import math
import random

from backend.models import ConnectedProfile, MLAssessment, SearchContext, TemporalAssessment

FEATURES = (
    "name_match",
    "username_match",
    "organization_match",
    "role_match",
    "department_match",
    "explicit_cross_link",
    "direct_page",
    "support_density",
    "missing_density",
    "temporal_consistency",
    "conflict_present",
)


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class _LogisticRegression:
    def __init__(self) -> None:
        self.weights = [0.0] * len(FEATURES)
        self.bias = 0.0

    def fit(self, rows: list[list[float]], labels: list[int], *, epochs: int = 1600, lr: float = 0.08, l2: float = 0.002) -> None:
        n = max(1, len(rows))
        for _ in range(epochs):
            dw = [0.0] * len(self.weights)
            db = 0.0
            for x, y in zip(rows, labels):
                pred = _sigmoid(self.bias + sum(w * v for w, v in zip(self.weights, x)))
                error = pred - y
                db += error
                for i, value in enumerate(x):
                    dw[i] += error * value
            self.bias -= lr * db / n
            for i in range(len(self.weights)):
                grad = dw[i] / n + l2 * self.weights[i]
                self.weights[i] -= lr * grad

    def predict_score(self, row: list[float]) -> float:
        return _sigmoid(self.bias + sum(w * v for w, v in zip(self.weights, row)))


def _synthetic_dataset() -> tuple[list[list[float]], list[int]]:
    """Generate deterministic controlled identity-pair examples.

    Positives include incomplete profiles and cross-linked aliases. Negatives include
    same-name collisions, copied usernames, organization conflicts, and sparse pages.
    """
    rng = random.Random(20260920)
    rows: list[list[float]] = []
    labels: list[int] = []

    positive_templates = [
        [1,1,1,1,1,1,1,.95,.05,1,0],
        [1,1,1,.7,.4,1,1,.82,.18,1,0],
        [1,0,1,1,1,1,1,.78,.22,.9,0],
        [1,1,.6,.5,.5,1,.55,.70,.30,.8,0],
        [1,1,0,.6,0,1,1,.64,.36,.8,0],
        [1,0,1,.5,.5,0,1,.62,.38,1,0],
    ]
    negative_templates = [
        [1,0,0,0,0,0,1,.18,.82,.6,0],       # same name only
        [1,1,0,0,0,0,.55,.30,.70,.5,0],     # copied/same handle but no context
        [1,0,0,0,0,0,.55,.15,.85,.5,1],     # explicit conflict
        [0,1,0,0,0,0,1,.15,.85,.5,1],
        [1,.5,.2,0,0,0,.2,.25,.75,.4,0],
        [1,1,1,0,0,0,.3,.42,.58,.1,1],       # tempting but temporal/conflict issue
    ]

    def perturb(template: list[float], label: int) -> list[float]:
        row = []
        for index, value in enumerate(template):
            if index == len(FEATURES) - 1:  # conflict indicator stays binary-ish
                noisy = value
            else:
                noisy = value + rng.uniform(-0.10, 0.10)
            row.append(max(0.0, min(1.0, noisy)))
        # Occasionally remove a non-critical field to simulate real missing data.
        if rng.random() < 0.25:
            idx = rng.choice([2, 3, 4, 5, 6])
            row[idx] *= rng.uniform(0.0, 0.5)
        return row

    for label, templates in ((1, positive_templates), (0, negative_templates)):
        for template in templates:
            rows.append(template[:]); labels.append(label)
            for _ in range(17):
                rows.append(perturb(template, label)); labels.append(label)
    return rows, labels


@lru_cache(maxsize=1)
def _trained_model() -> tuple[_LogisticRegression, float]:
    rows, labels = _synthetic_dataset()
    indices = list(range(len(rows)))
    random.Random(11).shuffle(indices)
    split = int(len(indices) * 0.8)
    train_ids, test_ids = indices[:split], indices[split:]
    model = _LogisticRegression()
    model.fit([rows[i] for i in train_ids], [labels[i] for i in train_ids])
    correct = 0
    for i in test_ids:
        pred = 1 if model.predict_score(rows[i]) >= 0.5 else 0
        correct += pred == labels[i]
    accuracy = correct / max(1, len(test_ids))
    return model, accuracy


def _support_value(profile: ConnectedProfile, field: str) -> float:
    return 1.0 if field in profile.supporting else 0.0


def _profile_features(profile: ConnectedProfile, context: SearchContext, temporal: TemporalAssessment | None = None) -> dict[str, float]:
    supplied = [field for field in ("name","username","organization","role","department") if getattr(context, field, None)]
    supporting = [field for field in supplied if field in profile.supporting]
    missing = [field for field in supplied if field not in profile.supporting]
    temporal_value = 0.65
    if temporal:
        temporal_value = {"consistent": 1.0, "mixed": 0.55, "conflicting": 0.0, "insufficient": 0.65}[temporal.status]
    return {
        "name_match": _support_value(profile, "name"),
        "username_match": _support_value(profile, "username"),
        "organization_match": _support_value(profile, "organization"),
        "role_match": _support_value(profile, "role"),
        "department_match": _support_value(profile, "department"),
        "explicit_cross_link": min(1.0, len(profile.profile.external_profile_links) / 2.0 + (0.5 if any(str(x).startswith("explicit profile cross-link") for x in profile.supporting) else 0.0)),
        "direct_page": 1.0 if profile.profile.access_status == "PUBLIC_PAGE_ANALYZED" else 0.55 if profile.profile.access_status == "SEARCH_SNIPPET_ONLY" else 0.25,
        "support_density": len(supporting) / max(1, len(supplied)),
        "missing_density": len(missing) / max(1, len(supplied)),
        "temporal_consistency": temporal_value,
        "conflict_present": 1.0 if profile.conflicts or profile.status == "conflicting" else 0.0,
    }


def _score_features(features: dict[str, float]) -> tuple[float, float]:
    model, accuracy = _trained_model()
    row = [features[name] for name in FEATURES]
    return model.predict_score(row), accuracy


def band_for(score: float) -> str:
    if score >= 0.72:
        return "strong"
    if score >= 0.48:
        return "moderate"
    return "weak"


def score_profiles(profiles: list[ConnectedProfile], context: SearchContext, temporal: TemporalAssessment | None = None) -> None:
    """Attach ML scores in-place without changing deterministic verdicts."""
    for profile in profiles:
        features = _profile_features(profile, context, temporal)
        score, _ = _score_features(features)
        # Explicit contradictions remain a hard ceiling; ML cannot wash them away.
        if features["conflict_present"]:
            score = min(score, 0.25)
        profile.ml_score = round(score, 3)
        profile.ml_band = band_for(score)


def overall_assessment(profiles: list[ConnectedProfile], context: SearchContext, temporal: TemporalAssessment) -> MLAssessment:
    supplied = [field for field in ("name","username","organization","role","department") if getattr(context, field, None)]
    supported_union = {field for profile in profiles for field in profile.supporting if field in supplied}
    conflict = any(profile.conflicts or profile.status == "conflicting" for profile in profiles)
    explicit_links = sum(len(profile.profile.external_profile_links) for profile in profiles)
    direct = sum(profile.profile.access_status == "PUBLIC_PAGE_ANALYZED" for profile in profiles)

    def field_value(field: str) -> float:
        return 1.0 if field in supported_union else 0.0

    features = {
        "name_match": field_value("name"),
        "username_match": field_value("username"),
        "organization_match": field_value("organization"),
        "role_match": field_value("role"),
        "department_match": field_value("department"),
        "explicit_cross_link": min(1.0, explicit_links / 2.0),
        "direct_page": min(1.0, direct / 2.0),
        "support_density": len(supported_union) / max(1, len(supplied)),
        "missing_density": (len(supplied) - len(supported_union)) / max(1, len(supplied)),
        "temporal_consistency": {"consistent":1.0,"mixed":0.55,"conflicting":0.0,"insufficient":0.65}[temporal.status],
        "conflict_present": 1.0 if conflict or temporal.status == "conflicting" else 0.0,
    }
    score, accuracy = _score_features(features)
    if features["conflict_present"]:
        score = min(score, 0.25)
    band = band_for(score)
    strongest = sorted(((name, value) for name, value in features.items() if name not in {"missing_density","conflict_present"}), key=lambda item: item[1], reverse=True)[:3]
    positive = ", ".join(name.replace("_", " ") for name, value in strongest if value >= 0.5) or "limited corroborating signals"
    explanation = f"The synthetic-trained baseline weighs {positive}. Deterministic conflicts remain authoritative and are never overridden by the model."
    return MLAssessment(
        score=round(score, 3),
        band=band,
        features={key: round(value, 3) for key, value in features.items()},
        model_version="tn-logreg-synth-v1",
        training_basis="Deterministic logistic regression trained locally on controlled synthetic same-person/different-person feature pairs.",
        holdout_accuracy=round(accuracy, 3),
        explanation=explanation,
    )
