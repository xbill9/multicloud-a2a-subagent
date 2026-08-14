"""Human feedback, and what it is for.

The README has carried this as an open weakness since the rubric was written:
*nobody has checked that rubric rank correlates with human rank on even one set
of drafts.* The weightings were chosen by argument, which is how you get a
plausible scorer rather than a calibrated one. These tests are about the one
number that can say the instrument is wrong.
"""


from evaluations import feedback


def ranked(order: list[str], complete: bool = True) -> feedback.JudgeRanking:
    return feedback.JudgeRanking(ranking=order, complete=complete)


def review(run_id: str, winner: str | None = None, **kwargs) -> feedback.HumanReview:
    return feedback.HumanReview(run_id=run_id, winner=winner, **kwargs)


def test_a_review_round_trips_through_the_store(tmp_path):
    path = tmp_path / "feedback.jsonl"
    written = review(
        "run-1",
        winner="aws",
        reviewer="xbill",
        drafts=[
            feedback.DraftFeedback(
                source="aws",
                rank=1,
                score=19.0,
                note="the only one that named a manufacturer",
                citations=[
                    feedback.CitationFeedback(url="https://example.org/a", verdict="verified")
                ],
            ),
            feedback.DraftFeedback(source="gcp", rank=2),
        ],
    )
    feedback.record(written, path=path)

    (restored,) = list(feedback.load(path))

    assert restored.run_id == "run-1"
    assert restored.winner == "aws"
    assert restored.ranking == ["aws", "gcp"]
    assert restored.drafts[0].citations[0].verdict == "verified"


def test_feedback_is_append_only(tmp_path):
    """Several people may review one run, and a later opinion must not erase an
    earlier one."""
    path = tmp_path / "feedback.jsonl"
    feedback.record(review("run-1", winner="aws", reviewer="a"), path=path)
    feedback.record(review("run-1", winner="gcp", reviewer="b"), path=path)

    reviews = list(feedback.load(path))

    assert [r.reviewer for r in reviews] == ["a", "b"]
    assert {r.winner for r in reviews} == {"aws", "gcp"}


def test_a_torn_final_line_does_not_hide_the_rest(tmp_path):
    """A store appended to by a process that can be killed mid-write must not
    lose its whole history to one bad line."""
    path = tmp_path / "feedback.jsonl"
    feedback.record(review("run-1", winner="aws"), path=path)
    with path.open("a") as handle:
        handle.write('{"run_id": "run-2", "winner":')

    assert [r.run_id for r in feedback.load(path)] == ["run-1"]


def test_agreement_is_none_rather_than_zero_when_nobody_has_looked(tmp_path):
    """"The judge and a human never agree" and "nobody has looked" are opposite
    claims and must not render as the same number."""
    result = feedback.agreement({"run-1": ranked(["aws"])}, path=tmp_path / "none.jsonl")

    assert result["reviewed"] == 0
    assert result["agreement_rate"] is None


def test_agreement_counts_only_runs_the_judge_also_ranked(tmp_path):
    path = tmp_path / "feedback.jsonl"
    feedback.record(review("run-1", winner="aws"), path=path)
    feedback.record(review("run-2", winner="gcp"), path=path)
    feedback.record(review("ghost", winner="aws"), path=path)

    result = feedback.agreement({"run-1": ranked(["aws"]), "run-2": ranked(["azure"])}, path=path)

    assert result["agreed"] == 1
    assert result["disagreed"] == 1
    assert result["agreement_rate"] == 0.5
    assert result["reviews_for_unknown_runs"] == 1


def test_citation_verdicts_are_tallied(tmp_path):
    path = tmp_path / "feedback.jsonl"
    feedback.record(
        review(
            "run-1",
            winner="aws",
            drafts=[
                feedback.DraftFeedback(
                    source="aws",
                    citations=[
                        feedback.CitationFeedback(url="https://a", verdict="verified"),
                        feedback.CitationFeedback(url="https://b", verdict="fabricated"),
                    ],
                )
            ],
        ),
        path=path,
    )

    result = feedback.agreement({"run-1": ranked(["aws"])}, path=path)

    assert result["citations"]["verified"] == 1
    assert result["citations"]["fabricated"] == 1


def test_unreachable_is_not_the_same_verdict_as_fabricated():
    """A link can rot, a site can block a datacentre, a paper can move behind a
    paywall. Collapsing those into "fabricated" would let honest link rot count
    as a model inventing sources -- which is the specific accusation this exists
    to support or refute."""
    assert "unreachable" in feedback.CITATION_VERDICTS
    assert "fabricated" in feedback.CITATION_VERDICTS


def test_concordance_counts_every_pair_not_just_the_winner(tmp_path):
    """The measure that makes a review worth doing.

    Winner agreement is one bit per run, and with three clouds a coin lands on
    it a third of the time. Every run yields one comparison per *pair* -- three
    for a three-cloud run -- so five reviewed runs give fifteen comparisons
    rather than five. That is the difference between a number and an anecdote.
    """
    path = tmp_path / "feedback.jsonl"
    feedback.record(
        review(
            "run-1",
            winner="aws",
            drafts=[
                feedback.DraftFeedback(source="aws", rank=1),
                feedback.DraftFeedback(source="gcp", rank=2),
                feedback.DraftFeedback(source="azure", rank=3),
            ],
        ),
        path=path,
    )

    # The judge agrees on the winner and on aws>gcp and aws>azure, but flips
    # the other two. One run, three pairs, two concordant.
    result = feedback.agreement({"run-1": ranked(["aws", "azure", "gcp"])}, path=path)

    assert result["agreed"] == 1
    assert result["pairs_compared"] == 3
    assert result["concordant"] == 2
    assert result["discordant"] == 1
    assert result["concordance"] == 2 / 3


def test_a_judge_that_is_exactly_backwards_scores_zero(tmp_path):
    """Which would be a more useful finding than anything in between."""
    path = tmp_path / "feedback.jsonl"
    feedback.record(
        review(
            "run-1",
            drafts=[
                feedback.DraftFeedback(source="aws", rank=1),
                feedback.DraftFeedback(source="gcp", rank=2),
                feedback.DraftFeedback(source="azure", rank=3),
            ],
        ),
        path=path,
    )

    result = feedback.agreement({"run-1": ranked(["azure", "gcp", "aws"])}, path=path)

    assert result["concordance"] == 0.0


def test_concordance_is_none_when_nobody_ranked_anything(tmp_path):
    """A reviewer who named a winner but ranked nothing contributes no pairs,
    and that must not read as total disagreement."""
    path = tmp_path / "feedback.jsonl"
    feedback.record(review("run-1", winner="aws"), path=path)

    result = feedback.agreement({"run-1": ranked(["aws", "gcp"])}, path=path)

    assert result["agreed"] == 1
    assert result["pairs_compared"] == 0
    assert result["concordance"] is None


def test_a_run_that_lost_a_leg_cannot_calibrate(tmp_path):
    """And is excluded loudly rather than quietly shrinking the sample.

    Measured 2026-08-14: three consecutive runs lost the GCP leg to one defect
    in the logging setup. Every pair they could contribute was `aws` against
    `azure` -- the same comparison repeated -- so counting them would have
    measured one matchup several times and reported it as coverage, while
    saying nothing about how the judge ranks the missing cloud against
    anything.

    A leg is never missing at random. That is what makes this different from a
    small sample.
    """
    path = tmp_path / "feedback.jsonl"
    for run_id in ("whole", "degraded"):
        feedback.record(
            review(
                run_id,
                winner="aws",
                drafts=[
                    feedback.DraftFeedback(source="aws", rank=1),
                    feedback.DraftFeedback(source="azure", rank=2),
                ],
            ),
            path=path,
        )

    result = feedback.agreement(
        {
            "whole": ranked(["aws", "azure"], complete=True),
            "degraded": ranked(["aws", "azure"], complete=False),
        },
        path=path,
    )

    assert result["reviewed"] == 1, "the degraded run was counted"
    assert result["pairs_compared"] == 1
    assert result["excluded_incomplete_runs"] == 1


def test_the_exclusion_is_visible_rather_than_silent(tmp_path):
    """A sample that shrank without saying so is worse than a small one: the
    reader believes they have the coverage they asked for."""
    path = tmp_path / "feedback.jsonl"
    feedback.record(review("degraded", winner="aws"), path=path)

    result = feedback.agreement({"degraded": ranked(["aws"], complete=False)}, path=path)

    assert result["reviewed"] == 0
    assert result["concordance"] is None
    assert result["excluded_incomplete_runs"] == 1
