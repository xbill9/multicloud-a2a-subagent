"""Score the drafts and name a winner.

This replaces the median consensus of the currency mesh, and the replacement is
weaker in a way worth stating plainly rather than discovering later. A median
over three numbers is arithmetic: it cannot be argued with, and a cloud that
diverges is *wrong*. A judge over three essays is an opinion. Nothing here
establishes that the winning draft is correct, only that one scorer preferred
it, so a single verdict is an anecdote and only the aggregate in
``evaluations/`` is evidence.

Two judges, for the same reason the agents have two brains:

``rubric`` (default)
    Deterministic scoring with no model in the path. Credential-free, so the
    pipeline can be exercised and tested end to end. **It measures form, not
    truth** -- headings, figures, citation markers, length. A draft that is
    confidently wrong in well-structured markdown will beat a hedged correct
    one, and the rubric has no way to notice.

``llm``
    A model reads all three drafts and scores them against the same rubric.
    Better at judging content, and carrying its own failure modes -- chiefly
    that it is one model's taste, and that models favour drafts resembling
    their own output.

Both judge **blind** by default: drafts are relabelled before scoring and the
labels are rotated per topic. Without that, the position of a cloud in the list
is fixed across every run, and a judge with positional bias -- which is the
normal case, not a pathological one -- would hand one vendor a systematic
advantage that the audit would then faithfully record as a model difference.
"""

import json
import os
import re
from hashlib import blake2b

from coordinator.models import (
    MAX_DIMENSION_SCORE,
    RUBRIC_DIMENSIONS,
    DimensionScore,
    Draft,
    DraftVerdict,
    ResearchRequest,
    Verdict,
)

#: Below this total-score gap the winner is not meaningfully ahead. Reported as
#: a warning rather than suppressed, because "gemini won by 0.2" and "gemini won
#: by 6" are the same `winner` field and very different claims.
NARROW_MARGIN = 1.0

JUDGE_MODES = ("rubric", "llm")

_LABELS = "ABCDEFGH"

_URL_RE = re.compile(r"https?://\S+")
_CITATION_RE = re.compile(
    r"\[\d+\]|\(\d{4}\)|\bet al\.|\baccording to\b|\bper\s+the\b|\bsource:", re.IGNORECASE
)
_FIGURE_RE = re.compile(r"\b\d[\d,.]*\s*(?:%|bn|m|k|GW|MW|TWh|USD|EUR|GBP)?\b", re.IGNORECASE)
_PROPER_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+\S", re.MULTILINE)
_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+\.)\s+\S", re.MULTILINE)
_STOPWORDS = frozenset(
    ["the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is", "are", "was", "were", "be", "been", "how", "what", "why", "which", "who", "whom", "when", "where", "does", "do", "did", "can", "could", "should", "would", "will", "shall", "it", "its", "this", "that", "these", "those", "from", "by", "as", "at", "into", "than", "then"]
)


def judge_mode() -> str:
    mode = os.getenv("RESEARCH_JUDGE_MODE", "rubric").strip().lower()
    return mode if mode in JUDGE_MODES else "rubric"


def blind_labels(request: ResearchRequest, drafts: list[Draft]) -> dict[str, str]:
    """Assign each draft a label, rotated by topic so position is not fixed.

    Deterministic on purpose -- the same brief and the same participants give
    the same labels, so a run can be reproduced and a disputed verdict
    re-examined. What it is not is *constant*: a different topic rotates the
    assignment, so no cloud sits at position A across the whole audit.
    """
    ordered = sorted(drafts, key=lambda draft: draft.source)
    digest = blake2b(request.topic.encode(), digest_size=2).digest()
    offset = int.from_bytes(digest, "big") % max(len(ordered), 1)
    rotated = ordered[offset:] + ordered[:offset]
    return {draft.source: _LABELS[index] for index, draft in enumerate(rotated)}


# --------------------------------------------------------------------------
# The deterministic rubric
# --------------------------------------------------------------------------


def _content_words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z][a-z\-']+", text.lower())
        if word not in _STOPWORDS and len(word) > 2
    }


def _clamp(value: float) -> float:
    return round(max(0.0, min(MAX_DIMENSION_SCORE, value)), 2)


def _coverage(request: ResearchRequest, body: str) -> DimensionScore:
    """How much of what was asked for actually appears.

    The only dimension keyed to the requester's own words rather than to
    generic prose features, which makes it the one worth trusting most.
    """
    present = _content_words(body)
    if request.questions:
        hits = []
        for question in request.questions:
            wanted = _content_words(question)
            if not wanted:
                continue
            hits.append(len(wanted & present) / len(wanted))
        if hits:
            score = MAX_DIMENSION_SCORE * (sum(hits) / len(hits))
            return DimensionScore(
                dimension="coverage",
                score=_clamp(score),
                rationale=f"{len(hits)} focus questions, mean term overlap "
                f"{sum(hits) / len(hits):.0%}",
            )

    wanted = _content_words(request.topic)
    overlap = len(wanted & present) / len(wanted) if wanted else 0.0
    return DimensionScore(
        dimension="coverage",
        score=_clamp(MAX_DIMENSION_SCORE * overlap),
        rationale=f"no focus questions; topic term overlap {overlap:.0%}",
    )


def _specificity(body: str) -> DimensionScore:
    """Figures and named things per 100 words.

    A proxy, and a crude one: it rewards a draft for containing numbers
    without any view on whether they are right. Calibrated so that roughly
    eight specifics per hundred words scores full marks, which is dense prose.
    """
    words = max(len(body.split()), 1)
    specifics = len(_FIGURE_RE.findall(body)) + len(_PROPER_RE.findall(body))
    density = specifics / words * 100
    return DimensionScore(
        dimension="specificity",
        score=_clamp(MAX_DIMENSION_SCORE * density / 8),
        rationale=f"{specifics} figures/proper nouns in {words} words ({density:.1f}/100)",
    )


def _evidence(body: str) -> DimensionScore:
    """Citation-shaped markers. Counts the gesture, not the source."""
    markers = len(_URL_RE.findall(body)) + len(_CITATION_RE.findall(body))
    return DimensionScore(
        dimension="evidence",
        score=_clamp(MAX_DIMENSION_SCORE * markers / 5),
        rationale=f"{markers} citation markers (unverified)",
    )


def _structure(body: str) -> DimensionScore:
    headings = _HEADING_RE.findall(body)
    has_h1 = any(level == "#" for level in headings)
    lists = len(_LIST_RE.findall(body))
    score = (
        (2.0 if has_h1 else 0.0)
        + min(2.0, max(len(headings) - 1, 0) * 0.5)
        + min(1.0, lists * 0.25)
    )
    return DimensionScore(
        dimension="structure",
        score=_clamp(score),
        rationale=f"{'h1' if has_h1 else 'no h1'}, {len(headings)} headings, {lists} list items",
    )


def _concision(request: ResearchRequest, body: str) -> DimensionScore:
    """Full marks for landing inside the requested budget, falling off outside it.

    Asymmetric on purpose. Overrunning the limit is ignoring an instruction;
    coming in very short is usually a draft that stopped early. Both are
    penalised, overrun slightly harder.
    """
    words = len(body.split())
    budget = request.max_words
    if words > budget:
        overrun = (words - budget) / budget
        return DimensionScore(
            dimension="concision",
            score=_clamp(MAX_DIMENSION_SCORE * (1 - overrun * 1.5)),
            rationale=f"{words} words against a {budget} budget ({overrun:.0%} over)",
        )
    ratio = words / budget
    if ratio >= 0.5:
        return DimensionScore(
            dimension="concision",
            score=MAX_DIMENSION_SCORE,
            rationale=f"{words} words, inside the {budget} budget",
        )
    return DimensionScore(
        dimension="concision",
        score=_clamp(MAX_DIMENSION_SCORE * ratio / 0.5),
        rationale=f"{words} words, under half the {budget} budget",
    )


def score_draft(request: ResearchRequest, draft: Draft) -> DraftVerdict:
    """Score one draft on every rubric dimension, with no model involved."""
    scores = [
        _coverage(request, draft.body),
        _specificity(draft.body),
        _evidence(draft.body),
        _structure(draft.body),
        _concision(request, draft.body),
    ]
    assert [score.dimension for score in scores] == list(RUBRIC_DIMENSIONS)
    return DraftVerdict(
        source=draft.source,
        scores=scores,
        total=round(sum(score.score for score in scores), 2),
    )


def _rank(verdicts: list[DraftVerdict], drafts: list[Draft]) -> list[DraftVerdict]:
    """Order by total, breaking ties by latency and then by name.

    Ties are common with the rubric and near-certain in `direct` mode, where
    every cloud returns the same canned text. Breaking them on latency at
    least makes the order mean *something*; breaking them on nothing would let
    dict ordering decide a winner and the audit record it as a preference.
    """
    latency = {draft.source: draft.latency_ms for draft in drafts}
    ordered = sorted(
        verdicts,
        key=lambda verdict: (-verdict.total, latency.get(verdict.source, 0.0), verdict.source),
    )
    for position, verdict in enumerate(ordered, start=1):
        verdict.rank = position
    return ordered


class RubricJudge:
    """Deterministic scorer. Measures the shape of a draft, never its truth."""

    name = "rubric"

    async def judge(self, request: ResearchRequest, drafts: list[Draft]) -> Verdict:
        if not drafts:
            return Verdict(
                judge=self.name,
                winner=None,
                warnings=["no cloud returned a draft; nothing to judge"],
            )

        ordered = _rank([score_draft(request, draft) for draft in drafts], drafts)
        verdict = Verdict(
            judge=self.name,
            winner=ordered[0].source,
            verdicts=ordered,
            blind=True,
            rationale=(
                "Deterministic rubric: coverage, specificity, evidence, structure and "
                "concision, 5 points each. Form only -- no claim in any draft was checked."
            ),
        )
        _add_margin_warnings(verdict, len(drafts))
        return verdict


def _add_margin_warnings(verdict: Verdict, draft_count: int) -> None:
    if draft_count < 2:
        verdict.warnings.append(
            "only one cloud returned a draft; a comparison of one is not a comparison"
        )
        return
    margin = verdict.margin
    if margin is not None and margin < NARROW_MARGIN:
        verdict.warnings.append(
            f"winner is ahead by only {margin:.2f} of 25 points; treat this as a tie "
            f"rather than a ranking"
        )


# --------------------------------------------------------------------------
# The model judge
# --------------------------------------------------------------------------

_JUDGE_APP = "research-judge"
_JUDGE_USER = "coordinator"

_JUDGE_PROMPT = """\
You are grading {count} anonymous research briefs written to the same instruction.

The brief they were given:
---
Topic: {topic}
{questions}Word budget: {max_words}
---

Score each draft from 0 to 5 on each of these dimensions:
- coverage: does it address the topic and every focus question?
- specificity: named things, figures and dates rather than generalities
- evidence: does it support claims, and are the supports plausible?
- structure: a clear title, sensible headings, readable order
- concision: does it respect the word budget without padding?

Judge only what is written. You do not know who wrote any draft, and you must
not guess. If two drafts are of equal quality, say so by giving equal scores
rather than inventing a difference.

Reply with one JSON object and nothing else:
{{"drafts": [{{"label": "A", "coverage": 0-5, "specificity": 0-5,
"evidence": 0-5, "structure": 0-5, "concision": 0-5, "notes": "one sentence"}}],
"winner": "<label>", "rationale": "two sentences at most"}}

The drafts:

{drafts}
"""


class LlmJudge:
    """A model scores the drafts, blind, against the same rubric.

    Runs on ADK because the coordinator already depends on it and runs on
    Cloud Run, where a Gemini call needs no stored credential. That does mean
    the judge shares a vendor with one of the three participants, which is a
    real bias risk and is recorded on every verdict rather than argued away --
    see ``docs/`` and the `judge` field in the stored run.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model or os.getenv("RESEARCH_JUDGE_MODEL", "gemini-2.5-pro")

    @property
    def name(self) -> str:
        return self._model

    async def judge(self, request: ResearchRequest, drafts: list[Draft]) -> Verdict:
        if not drafts:
            return Verdict(
                judge=self.name,
                winner=None,
                warnings=["no cloud returned a draft; nothing to judge"],
            )

        labels = blind_labels(request, drafts)
        by_label = {labels[draft.source]: draft for draft in drafts}
        prompt = _build_judge_prompt(request, by_label)

        try:
            reply = await self._ask(prompt)
        except Exception as exc:  # noqa: BLE001 - the judge must never fail the run
            fallback = await RubricJudge().judge(request, drafts)
            fallback.warnings.append(
                f"model judge ({self.name}) failed and the deterministic rubric was used "
                f"instead: {type(exc).__name__}: {exc}"
            )
            return fallback

        verdict = _parse_judge_reply(reply, by_label, judge=self.name)
        if verdict is None:
            fallback = await RubricJudge().judge(request, drafts)
            fallback.warnings.append(
                f"model judge ({self.name}) returned an unreadable verdict and the "
                f"deterministic rubric was used instead: {reply[:200]!r}"
            )
            return fallback

        _add_margin_warnings(verdict, len(drafts))
        return verdict

    async def _ask(self, prompt: str) -> str:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        agent = LlmAgent(
            model=self._model,
            name="draft_judge",
            description="Scores anonymous research drafts against a fixed rubric",
            instruction=(
                "You are a careful, sceptical grader. You reply with one JSON "
                "object and no other text."
            ),
        )
        session_service = InMemorySessionService()
        runner = Runner(agent=agent, app_name=_JUDGE_APP, session_service=session_service)
        session = await session_service.create_session(
            app_name=_JUDGE_APP, user_id=_JUDGE_USER
        )
        texts: list[str] = []
        try:
            async for event in runner.run_async(
                user_id=_JUDGE_USER,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
            ):
                if event.content and event.content.parts:
                    texts.extend(part.text for part in event.content.parts if part.text)
        finally:
            close = getattr(runner, "close", None)
            if close is not None:
                await close()
        return "\n".join(text for text in texts if text)


def _build_judge_prompt(request: ResearchRequest, by_label: dict[str, Draft]) -> str:
    questions = ""
    if request.questions:
        listed = "\n".join(f"- {question}" for question in request.questions)
        questions = f"Focus questions:\n{listed}\n"

    # The body only. Titles are included because they are part of the draft,
    # but nothing that identifies the writer is: no model name, no cloud, no
    # latency. A judge told one draft came from Bedrock is answering a
    # different question from the one the audit is asking.
    blocks = [
        f"### Draft {label}\n\n{by_label[label].body}"
        for label in sorted(by_label)
    ]
    return _JUDGE_PROMPT.format(
        count=len(by_label),
        topic=request.topic,
        questions=questions,
        max_words=request.max_words,
        drafts="\n\n".join(blocks),
    )


def extract_json_object(text: str) -> dict | None:
    """Pull the first top-level JSON object out of free-form model text.

    Models fence JSON, preface it, or append a closing remark, and a judge
    that fails on any of those is a judge that fails. Anything genuinely
    unreadable returns None and the caller falls back to the rubric.
    """
    decoder = json.JSONDecoder()
    index = 0
    while (start := text.find("{", index)) != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict) and "drafts" in obj:
            return obj
        index = start + 1
    return None


def _parse_judge_reply(reply: str, by_label: dict[str, Draft], *, judge: str) -> Verdict | None:
    payload = extract_json_object(reply)
    if payload is None:
        return None

    verdicts: list[DraftVerdict] = []
    for entry in payload.get("drafts") or []:
        label = str(entry.get("label", "")).strip().upper()
        draft = by_label.get(label)
        if draft is None:
            continue
        scores = []
        for dimension in RUBRIC_DIMENSIONS:
            try:
                value = float(entry[dimension])
            except (KeyError, TypeError, ValueError):
                return None
            scores.append(DimensionScore(dimension=dimension, score=_clamp(value)))
        verdicts.append(
            DraftVerdict(
                source=draft.source,
                scores=scores,
                total=round(sum(score.score for score in scores), 2),
                notes=str(entry.get("notes", ""))[:400],
            )
        )

    if len(verdicts) != len(by_label):
        # A judge that scored two of three drafts has not done the job, and
        # ranking the two it did would quietly drop a participant from the
        # audit. Fall back rather than report a partial field as a full one.
        return None

    ordered = _rank(verdicts, list(by_label.values()))
    result = Verdict(
        judge=judge,
        winner=ordered[0].source,
        verdicts=ordered,
        blind=True,
        rationale=str(payload.get("rationale", ""))[:600],
    )

    # The winner comes from the judge's own scores, not from its `winner`
    # field, so the verdict is internally consistent by construction. When the
    # two disagree the model contradicted itself, which is worth recording:
    # it is the cheapest available signal that the judge was not really reading.
    claimed = str(payload.get("winner", "")).strip().upper()
    if claimed in by_label and by_label[claimed].source != result.winner:
        result.warnings.append(
            f"judge named draft {claimed} the winner but scored "
            f"{by_label[claimed].source} below {result.winner}; the scores were used"
        )
    return result


def load_judge(mode: str | None = None):
    """The judge named by ``mode``, or by ``RESEARCH_JUDGE_MODE``."""
    selected = (mode or judge_mode()).strip().lower()
    return LlmJudge() if selected == "llm" else RubricJudge()


# --------------------------------------------------------------------------
# The loop: what the judge sends back
# --------------------------------------------------------------------------

#: A draft at or above this total has passed and is not sent back. 25 is the
#: maximum, so this is "good on four dimensions out of five". Deliberately not
#: the maximum: a bar nothing can clear turns the loop into a fixed number of
#: rounds and burns the budget proving it.
DEFAULT_PASS_MARK = 18.0

#: Including the first. 1 restores the pre-loop behaviour exactly -- fan out,
#: judge, stop -- which is the control this feature has to be compared against.
DEFAULT_MAX_ROUNDS = 3


def pass_mark() -> float:
    try:
        return float(os.getenv("RESEARCH_PASS_MARK", DEFAULT_PASS_MARK))
    except ValueError:
        return DEFAULT_PASS_MARK


def max_rounds() -> int:
    try:
        return max(1, int(os.getenv("RESEARCH_MAX_ROUNDS", DEFAULT_MAX_ROUNDS)))
    except ValueError:
        return DEFAULT_MAX_ROUNDS


def critique_for(verdict: DraftVerdict, *, weakest: int = 3) -> str:
    """What one draft is told about itself, worst dimensions first.

    Built from the scores rather than from free text so the deterministic
    rubric can drive the loop with no model in the path. That matters more than
    it sounds: it means the whole loop -- gate, critique, rewrite, re-judge --
    can be exercised end to end without a credential, which is the only way its
    failure modes get tested before they happen in a deployed run.

    The model judge's own `notes` are appended when it wrote any. They are
    better feedback than a dimension name and a number, and they are also the
    part that can be absent, wrong or flattering, so they supplement the scores
    rather than replacing them.
    """
    lines = []
    ranked = sorted(verdict.scores, key=lambda score: score.score)
    for score in ranked[:weakest]:
        detail = f" ({score.rationale})" if score.rationale else ""
        lines.append(f"- {score.dimension}: {score.score:.1f} of {MAX_DIMENSION_SCORE:.0f}{detail}")

    if verdict.notes:
        lines.append(f"- reviewer's note: {verdict.notes}")
    return "\n".join(lines)


def needs_revision(verdict: Verdict, *, mark: float | None = None) -> list[DraftVerdict]:
    """Which drafts did not clear the bar, worst first.

    Returns the *per-draft* verdicts rather than the winner, because the loop
    is per cloud: a mesh where one model nailed it first time and another
    needed three attempts is exactly the signal worth keeping, and a global
    pass/fail throws it away. Sending back only the failures also means the
    passing draft is not rewritten into something worse, which a
    revise-everything loop does surprisingly often.
    """
    bar = pass_mark() if mark is None else mark
    return sorted(
        (draft for draft in verdict.verdicts if draft.total < bar),
        key=lambda draft: draft.total,
    )
