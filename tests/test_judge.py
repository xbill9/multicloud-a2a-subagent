"""The judge, and the properties an audit depends on it having."""

from datetime import UTC, datetime

from coordinator.judge import (
    NARROW_MARGIN,
    LlmJudge,
    RubricJudge,
    blind_labels,
    extract_json_object,
    load_judge,
    score_draft,
)
from coordinator.models import RUBRIC_DIMENSIONS, Draft, ResearchRequest

GOOD = """\
# Solid-state batteries in 2026

## Where production actually stands

QuantumScape shipped 12,000 B-sample cells in 2025, according to its Q4
filing. Toyota has said (2024) it targets 2027 for a limited launch. Samsung
SDI's pilot line runs at roughly 200 MWh a year.

## What the cost floor looks like

- Sulfide electrolyte remains near $40/kg against a $10/kg target
- Stack pressure rigs add 15% to pack mass

See https://example.org/ssb-2026 for the underlying figures.
"""

THIN = "Solid-state batteries are an interesting area with many considerations to weigh up."


def request(**kwargs) -> ResearchRequest:
    return ResearchRequest(topic="solid-state batteries in 2026", **kwargs)


def draft(source: str, body: str, *, latency_ms: float = 10.0, brain: str = "llm") -> Draft:
    return Draft(
        source=source,
        cloud=source,
        model=f"{source}-model",
        brain=brain,
        title="t",
        body=body,
        observed_at=datetime.now(UTC),
        latency_ms=latency_ms,
    )


# --------------------------------------------------------------------------
# The deterministic rubric
# --------------------------------------------------------------------------


def test_every_dimension_is_scored_in_order():
    verdict = score_draft(request(), draft("gcp", GOOD))

    assert [score.dimension for score in verdict.scores] == list(RUBRIC_DIMENSIONS)
    assert all(0 <= score.score <= 5 for score in verdict.scores)


def test_a_structured_specific_draft_outscores_a_thin_one():
    good = score_draft(request(), draft("gcp", GOOD))
    thin = score_draft(request(), draft("aws", THIN))

    assert good.total > thin.total


def test_coverage_rewards_answering_the_questions_asked():
    asked = request(questions=["what is the cost floor?"])
    covered = score_draft(asked, draft("gcp", GOOD))
    uncovered = score_draft(asked, draft("aws", "# Title\n\n" + "filler " * 80))

    assert covered.score_for("coverage") > uncovered.score_for("coverage")


def test_concision_penalises_overrunning_the_budget():
    long_body = "# Title\n\n" + "word " * 500
    tight = score_draft(request(max_words=600), draft("gcp", long_body))
    over = score_draft(request(max_words=100), draft("gcp", long_body))

    assert tight.score_for("concision") == 5.0
    assert over.score_for("concision") < 1.0


async def test_the_winner_is_the_highest_total():
    verdict = await RubricJudge().judge(
        request(), [draft("aws", THIN), draft("gcp", GOOD)]
    )

    assert verdict.winner == "gcp"
    assert verdict.ranking == ["gcp", "aws"]
    assert verdict.judge == "rubric"


async def test_a_narrow_win_is_reported_as_a_tie():
    """Identical drafts must not produce a confident winner.

    This is the `direct` mode case and it is the one most likely to be
    misread: three clouds returning the same canned text will always have a
    winner in the sense that something sorts first, and reporting that as a
    result is how scaffolding becomes a finding.
    """
    verdict = await RubricJudge().judge(
        request(), [draft("gcp", GOOD), draft("aws", GOOD)]
    )

    assert verdict.margin < NARROW_MARGIN
    assert any("tie" in warning for warning in verdict.warnings)


async def test_ties_are_broken_by_latency_not_by_dict_order():
    verdict = await RubricJudge().judge(
        request(),
        [draft("aws", GOOD, latency_ms=90.0), draft("gcp", GOOD, latency_ms=10.0)],
    )

    assert verdict.ranking == ["gcp", "aws"]


async def test_one_draft_is_flagged_as_not_a_comparison():
    verdict = await RubricJudge().judge(request(), [draft("gcp", GOOD)])

    assert verdict.winner == "gcp"
    assert any("not a comparison" in warning for warning in verdict.warnings)


async def test_no_drafts_yields_no_winner():
    verdict = await RubricJudge().judge(request(), [])

    assert verdict.winner is None
    assert verdict.warnings


# --------------------------------------------------------------------------
# Blinding
# --------------------------------------------------------------------------


def test_labels_are_stable_for_one_topic():
    drafts = [draft("gcp", GOOD), draft("aws", GOOD), draft("azure", GOOD)]

    assert blind_labels(request(), drafts) == blind_labels(request(), drafts)


def test_labels_rotate_across_topics():
    """No cloud may sit at position A for the whole audit.

    Judges have positional bias. With a fixed alphabetical assignment, "aws" is
    Draft A in every run forever, and any positional preference the judge has
    would be recorded by the audit as a property of the model.
    """
    drafts = [draft("gcp", GOOD), draft("aws", GOOD), draft("azure", GOOD)]
    seen = {
        blind_labels(ResearchRequest(topic=topic), drafts)["aws"]
        for topic in ("alpha topic", "beta topic", "gamma topic", "delta topic")
    }

    assert len(seen) > 1


# --------------------------------------------------------------------------
# The model judge's failure paths, which are the ones that matter
# --------------------------------------------------------------------------


def test_the_json_extractor_survives_a_fenced_reply():
    payload = extract_json_object('Sure!\n```json\n{"drafts": [], "winner": "A"}\n```\nDone.')

    assert payload == {"drafts": [], "winner": "A"}


def test_the_json_extractor_returns_none_on_junk():
    assert extract_json_object("no json here at all") is None


class _BrokenJudge(LlmJudge):
    def __init__(self, reply: str | None = None, raises: Exception | None = None) -> None:
        super().__init__(model="stub-judge")
        self._reply = reply
        self._raises = raises

    async def _ask(self, prompt: str) -> str:
        if self._raises:
            raise self._raises
        return self._reply


async def test_an_unreadable_verdict_falls_back_to_the_rubric():
    verdict = await _BrokenJudge(reply="I'd rather not.").judge(
        request(), [draft("gcp", GOOD), draft("aws", THIN)]
    )

    assert verdict.judge == "rubric"
    assert verdict.winner == "gcp"
    assert any("unreadable" in warning for warning in verdict.warnings)


async def test_a_judge_that_raises_falls_back_rather_than_failing_the_run():
    verdict = await _BrokenJudge(raises=RuntimeError("quota")).judge(
        request(), [draft("gcp", GOOD), draft("aws", THIN)]
    )

    assert verdict.judge == "rubric"
    assert any("quota" in warning for warning in verdict.warnings)


async def test_a_partial_verdict_falls_back_rather_than_dropping_a_participant():
    """Scoring two of three drafts silently drops one from the audit."""
    scored_one = (
        '{"drafts": [{"label": "A", "coverage": 5, "specificity": 5, "evidence": 5, '
        '"structure": 5, "concision": 5, "notes": ""}], "winner": "A"}'
    )
    verdict = await _BrokenJudge(reply=scored_one).judge(
        request(), [draft("gcp", GOOD), draft("aws", THIN), draft("azure", GOOD)]
    )

    assert verdict.judge == "rubric"
    assert len(verdict.verdicts) == 3


async def test_a_model_verdict_is_used_when_it_is_complete():
    labels = blind_labels(request(), [draft("gcp", GOOD), draft("aws", THIN)])
    entries = []
    for source, label in labels.items():
        top = source == "aws"
        scores = {dimension: (5 if top else 1) for dimension in RUBRIC_DIMENSIONS}
        entries.append({"label": label, **scores, "notes": "n"})
    reply = (
        '{"drafts": '
        + str(entries).replace("'", '"')
        + ', "winner": "' + labels["aws"] + '", "rationale": "because"}'
    )

    verdict = await _BrokenJudge(reply=reply).judge(
        request(), [draft("gcp", GOOD), draft("aws", THIN)]
    )

    assert verdict.judge == "stub-judge"
    assert verdict.winner == "aws"
    assert verdict.rationale == "because"


async def test_a_judge_contradicting_its_own_scores_is_recorded():
    """The scores decide, and the disagreement is reported rather than hidden."""
    labels = blind_labels(request(), [draft("gcp", GOOD), draft("aws", THIN)])
    entries = [
        {"label": labels["gcp"], **{d: 5 for d in RUBRIC_DIMENSIONS}, "notes": ""},
        {"label": labels["aws"], **{d: 1 for d in RUBRIC_DIMENSIONS}, "notes": ""},
    ]
    reply = (
        '{"drafts": ' + str(entries).replace("'", '"')
        + ', "winner": "' + labels["aws"] + '"}'
    )

    verdict = await _BrokenJudge(reply=reply).judge(
        request(), [draft("gcp", GOOD), draft("aws", THIN)]
    )

    assert verdict.winner == "gcp"
    assert any("named draft" in warning for warning in verdict.warnings)


def test_the_default_judge_needs_no_credentials():
    assert isinstance(load_judge(), RubricJudge)
    assert isinstance(load_judge("llm"), LlmJudge)
