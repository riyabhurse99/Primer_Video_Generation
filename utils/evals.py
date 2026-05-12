"""
Evals for the Scaler Primer pipeline.

Evaluates the quality of AI-generated narrations using an LLM judge.
Two eval calls per video:
  1. Slide-level  — checks each narration independently (relevance + quality)
  2. Lecture-level — checks all narrations together (coverage, flow, appropriateness)

Level-aware: prompts change based on "basic", "intermediate", "advanced", or None (generic).
The goal is always the same: the learner must be able to understand the topic clearly.

Usage:
    from utils.evals import run_evals
    evals = run_evals(topic, narrations, level="basic", call_llm=my_llm_fn)
    # call_llm is any function: (prompt: str) -> str
    # Returns None if call_llm is not provided (evals skipped).
"""

import json
import re
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Level-specific quality criteria
# ─────────────────────────────────────────────────────────────────────────────

_SLIDE_QUALITY_CRITERIA = {

    "basic": """\
TARGET AUDIENCE: BASIC — the student has ZERO prior knowledge. They have never
encountered this topic before. They may not even know the meaning of common
technical words like "algorithm", "data structure", "function", or "variable".

WHAT "GOOD" LOOKS LIKE AT BASIC LEVEL:
The narration reads like a patient, friendly teacher sitting next to the student,
explaining from absolute scratch. Every new word is immediately defined using
everyday language. Abstract concepts are grounded with a real-world analogy the
student can picture. Sentences are short. No concept is introduced without context
for why the student should care.

WHAT "BAD" LOOKS LIKE AT BASIC LEVEL:
- Using any technical term without immediately defining it in plain English
  (e.g. saying "Dijkstra's algorithm uses a priority queue" without first explaining
  what a priority queue is AND what an algorithm is)
- Circular definitions ("a graph is a graph-based data structure")
- Assuming the student understands related topics ("just like in BFS..." — a basic
  student has never heard of BFS)
- Dense, textbook-style sentences with multiple clauses
- Jumping straight into mechanics without explaining WHY this topic matters

SCORING CALIBRATION FOR BASIC:
- If any single technical term is used without a plain-English definition → quality cannot be above 3
- If the narration reads like a Wikipedia paragraph → quality is 2 at most
- If the narration uses an analogy that genuinely helps a beginner visualise the concept → quality is at least 4""",

    "intermediate": """\
TARGET AUDIENCE: INTERMEDIATE — the student knows the basics of programming and
general concepts. They have written code before. They know what variables, loops,
functions, arrays, and basic data types are. But they have NOT studied this
specific topic in depth.

WHAT "GOOD" LOOKS LIKE AT INTERMEDIATE LEVEL:
The narration goes beyond "what it is" into "how it works" and "why it matters".
It explains the mechanism or logic, not just the definition. Domain-specific terms
for THIS topic are still explained, but common programming terms (variable, loop,
function, array) can be used freely. At least one concrete example or use-case is
given. The student should understand not just the concept but its practical value.

WHAT "BAD" LOOKS LIKE AT INTERMEDIATE LEVEL:
- Staying at beginner-intro level (just defining the term and moving on)
- Only covering "what" without explaining "how" or "why"
- Using domain-specific jargon for this topic without brief explanation
  (e.g. for graph algorithms: "adjacency list" should be briefly explained;
  "variable" or "for loop" does not need explanation)
- No concrete examples — everything stays abstract

SCORING CALIBRATION FOR INTERMEDIATE:
- If the narration is just a definition with no mechanism → quality is 2 at most
- If it explains how something works but not why → quality is 3
- If it has mechanism + motivation + example → quality is 4-5""",

    "advanced": """\
TARGET AUDIENCE: ADVANCED — the student has solid foundations. They understand the
basics of the broader field and want depth, nuance, and practical insight on this
specific topic.

WHAT "GOOD" LOOKS LIKE AT ADVANCED LEVEL:
The narration adds real value beyond what a textbook introduction provides. It covers
trade-offs, edge cases, limitations, performance characteristics, or real-world
application details. Technical terminology is used fluently. The student should learn
something they could bring to a technical interview or apply in production code.

WHAT "BAD" LOOKS LIKE AT ADVANCED LEVEL:
- Surface-level content that a basic student could have received
- Restating commonly known facts without adding insight
- Missing trade-offs, limitations, or comparisons with alternatives
- No practical or applied perspective

SCORING CALIBRATION FOR ADVANCED:
- If the narration reads like a beginner introduction → quality is 1-2
- If it covers the concept correctly but with no advanced insight → quality is 3
- If it includes trade-offs, edge cases, or production considerations → quality is 4-5""",

    "generic": """\
TARGET AUDIENCE: GENERAL — no specific level assumed. The narration should work
for a motivated learner with no prior background in this topic.

WHAT "GOOD" LOOKS LIKE FOR GENERIC:
Self-contained and clear. A new learner can follow along without external help.
Technical terms are briefly explained on first use. The explanation is direct,
free of unnecessary jargon, and gives the learner a real understanding they can
build on — not just a surface definition.

WHAT "BAD" LOOKS LIKE FOR GENERIC:
- Jargon or assumed knowledge that would block a newcomer
- So vague it could apply to any topic ("This is a very important concept in computer science")
- Generic filler with no actual explanation of anything specific
- Overly complex sentences that require re-reading

SCORING CALIBRATION FOR GENERIC:
- If a newcomer would need to Google terms to understand → quality is 2 at most
- If it explains clearly but stays surface-level → quality is 3
- If it gives genuine understanding a learner can build on → quality is 4-5""",
}

_LECTURE_APPROPRIATENESS_CRITERIA = {

    "basic": """\
LEVEL-SPECIFIC LECTURE REQUIREMENTS (BASIC):
At basic level, the lecture must build the student's mental model from zero.
- Slide 1 MUST establish what this topic is and why it matters — in everyday language.
- Each slide should be independently understandable — the student should not be confused
  at any point, even if they lost focus for a moment.
- Analogies and concrete examples are essential — abstract-only explanations fail basic learners.
- The lecture should leave the student feeling "I understand this" not "this is complicated".
- There should be NO moment where a basic student encounters an unexplained term or concept.""",

    "intermediate": """\
LEVEL-SPECIFIC LECTURE REQUIREMENTS (INTERMEDIATE):
At intermediate level, the lecture should deepen understanding beyond basics.
- Slide 1 may briefly introduce or recap, but should quickly move to substantive content.
- Subsequent slides should explain mechanisms, reasoning, and practical examples.
- The student should walk away understanding HOW this works and WHY it's used, not just WHAT it is.
- Connections to related concepts the student already knows are valuable.
- At least one concrete example or use-case should appear across the 4 slides.""",

    "advanced": """\
LEVEL-SPECIFIC LECTURE REQUIREMENTS (ADVANCED):
At advanced level, the lecture should provide depth, nuance, and applied insight.
- Content should go beyond textbook definitions into trade-offs, performance, edge cases,
  or real-world usage patterns.
- The lecture should challenge the student — they already know the basics, so repeating
  fundamentals wastes their time.
- Comparisons with alternative approaches or techniques add significant value.
- A student who already understands the basics should learn something genuinely new.""",

    "generic": """\
LEVEL-SPECIFIC LECTURE REQUIREMENTS (GENERIC):
For a generic primer, the lecture should give a complete, self-contained introduction.
- A motivated learner with no prior background should be able to follow the full lecture.
- The lecture should cover: what is this, why it matters, how it works (at a high level),
  and where it's used.
- Jargon should be explained on first use.
- The student should finish with a solid foundational understanding.""",
}


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_slide_eval_prompt(topic: str, narrations: list, level: str) -> str:
    level_key = level if level in _SLIDE_QUALITY_CRITERIA else "generic"
    criteria = _SLIDE_QUALITY_CRITERIA[level_key]

    slides_block = "\n\n".join(
        f'[Slide {i+1}] """\n{n.strip() or "(empty — no narration was generated for this slide)"}\n"""'
        for i, n in enumerate(narrations)
    )

    return f"""You are a strict quality evaluator for AI-generated educational narrations.

CONTEXT: An AI system generates primer video lectures for students. For each slide,
it generates narration text that will be converted to audio. Your job is to evaluate
whether the narration is good enough to ship to a real student. Be honest and strict —
a generous evaluation means a student gets a bad learning experience.

IMPORTANT SCORING RULES:
- A score of 3 means "acceptable, minimum bar". This should be your starting assumption.
- A score of 5 means "genuinely excellent". This is rare. Do not give 5 unless the
  narration truly excels.
- A score of 1-2 means "this would confuse or fail the student". Use these when warranted.
- Do NOT inflate scores to be polite. This is an internal quality check, not feedback to
  the content creator.
- If the narration is empty, less than 15 words, or clearly a placeholder → score both
  dimensions as 1.
- If the narration is NOT in English or switches languages mid-text → relevance is 1.

TOPIC BEING TAUGHT: "{topic}"

{criteria}

═══════════════════════════════════════════════════
SLIDE NARRATIONS TO EVALUATE:
═══════════════════════════════════════════════════
{slides_block}
═══════════════════════════════════════════════════

SCORE EACH SLIDE ON TWO DIMENSIONS:

RELEVANCE (1–5): Is this narration actually about "{topic}"?
  1 = off-topic, empty, generic filler, or not in English
  2 = loosely related but does not explain any specific concept from "{topic}"
  3 = on-topic, addresses "{topic}" but explanation is vague or shallow
  4 = clearly about "{topic}", explains a real concept with some substance
  5 = directly and specifically explains an important concept from "{topic}" — a student
      reading only this slide would learn something concrete

QUALITY (1–5): Given the target audience described above, how well is this explained?
  1 = fails the level requirements entirely — student would be confused or learn nothing
  2 = partially meets requirements but has clear gaps (unexplained terms, missing context)
  3 = acceptable — meets the minimum bar but could be better
  4 = good — clearly meets level requirements, student would understand this well
  5 = excellent — exceeds expectations, uses strong examples/analogies, nothing to improve

FOR THE "reason" FIELD: You MUST cite a specific phrase or word from the narration that
supports your score. Example: "Uses 'adjacency matrix' without defining it — a basic
student would not know this term" or "The analogy 'like a queue at a coffee shop' makes
the concept immediately clear for beginners".

Set "needs_regeneration" to true if EITHER score is below 3.

Respond ONLY with valid JSON. No text before or after the JSON block.
{{
  "slides": [
    {{
      "slide": 1,
      "relevance_score": <1-5>,
      "quality_score": <1-5>,
      "reason": "<cite specific evidence from the narration text>",
      "needs_regeneration": <true or false>
    }}
  ]
}}

The "slides" array MUST contain exactly {len(narrations)} entries, one per slide in order."""


def _build_lecture_eval_prompt(topic: str, narrations: list, level: str) -> str:
    level_key = level if level in _LECTURE_APPROPRIATENESS_CRITERIA else "generic"
    appropriateness = _LECTURE_APPROPRIATENESS_CRITERIA[level_key]
    level_label = level.upper() if level else "GENERIC"

    numbered = "\n\n".join(
        f'[Slide {i+1}] """\n{n.strip() or "(empty)"}\n"""'
        for i, n in enumerate(narrations)
    )

    return f"""You are a strict quality evaluator for AI-generated educational video lectures.

CONTEXT: An AI system automatically generates {len(narrations)}-slide primer video lectures
for students. Each slide has narration text that is read aloud as audio. You are evaluating
whether this lecture, as a whole, would give a student a clear understanding of the topic.
Be honest and strict — this is an internal quality gate, not public feedback.

IMPORTANT SCORING RULES:
- 3 = "acceptable minimum". Start here and adjust based on evidence.
- 5 = "genuinely excellent". Rare. The lecture would need to be well-structured, complete,
  and perfectly matched to the target level.
- 1-2 = "this would fail the student". Use when the lecture is clearly inadequate.
- Do NOT inflate scores. A student receiving a bad lecture has a real negative experience.
- If multiple slides repeat the same content in different words, FLOW score cannot exceed 2.
- If the lecture covers fewer than 2 distinct concepts from "{topic}", COVERAGE cannot exceed 2.

TOPIC: "{topic}"
LEVEL: {level_label}

{appropriateness}

═══════════════════════════════════════════════════
LECTURE NARRATIONS IN SLIDE ORDER:
═══════════════════════════════════════════════════
{numbered}
═══════════════════════════════════════════════════

SCORE THE LECTURE AS A WHOLE ON THREE DIMENSIONS:

COVERAGE (1–5): Does this lecture cover the important concepts of "{topic}"?
  Think about what a student NEEDS to know about "{topic}" at the {level_label} level.
  Then check: did the lecture actually cover those things?
  1 = misses all key concepts — content is irrelevant or empty
  2 = mentions the topic but skips fundamental concepts a student would need
  3 = covers some concepts but leaves important gaps
  4 = covers most essential concepts — a student would have a reasonable understanding
  5 = comprehensive — covers what matters, nothing critical is missing

FLOW (1–5): Do the slides connect and build on each other in a logical teaching sequence?
  1 = slides feel random; no connection between them or content repeats across slides
  2 = weakly connected; the order feels arbitrary or there is significant repetition
  3 = a structure exists (intro → content → wrapup) but transitions are weak
  4 = clear progression; each slide builds on the previous one logically
  5 = excellent story arc — natural flow from introduction to conclusion, easy to follow

APPROPRIATENESS (1–5): Is the depth and language right for a {level_label} learner?
  Refer to the level-specific requirements above.
  1 = completely wrong level — a {level_label} student would be either totally lost or bored
  2 = mostly mismatched — significant portions are too simple or too complex
  3 = roughly right level with some mismatches in vocabulary or depth
  4 = well-matched — a {level_label} student would find this useful and accessible
  5 = perfectly calibrated — every slide feels like it was written specifically for this level

FOR "overall_score": Compute the EXACT arithmetic mean of the three scores, rounded to 1 decimal.
  Example: coverage=4, flow=3, appropriateness=4 → overall = (4+3+4)/3 = 3.7

FOR "pass": Set to true ONLY if overall_score >= 3.5.

FOR "missing_concepts": List specific, named concepts from "{topic}" that a student would
need but the lecture did not cover. Use real concept names, not vague descriptions.
  Good: ["time complexity of Dijkstra", "negative edge weights limitation"]
  Bad: ["more detail needed", "some concepts are missing"]
If nothing important is missing, use an empty array [].

FOR "verdict": Write exactly 2 sentences. First sentence: what this lecture does well.
Second sentence: the single most important thing it fails at or is missing.
If the lecture is excellent, the second sentence should identify a minor improvement.

Respond ONLY with valid JSON. No text before or after the JSON block.
{{
  "coverage_score": <1-5>,
  "flow_score": <1-5>,
  "appropriateness_score": <1-5>,
  "overall_score": <mean of the three, 1 decimal>,
  "missing_concepts": ["<specific concept name>"],
  "verdict": "<sentence 1: strength>. <sentence 2: weakness or missing piece>.",
  "pass": <true or false>
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# JSON parsing helper
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json(response: str, context: str):
    """Extract and parse JSON from an LLM response. Returns None on failure."""
    try:
        # Try direct parse first
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    try:
        # Strip markdown code fences if present
        clean = re.sub(r"```(?:json)?\s*", "", response)
        clean = re.sub(r"\s*```\s*$", "", clean).strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from surrounding text
    try:
        match = re.search(r"\{[\s\S]*\}", response)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        pass

    logger.warning(f"Failed to parse LLM JSON ({context}). Raw response:\n{response[:500]}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_slide_evals(
    topic: str,
    narrations: list,
    level: str,
    call_llm,
):
    """Run only slide-level evals. Returns the slides list or None on failure."""
    level_key = level if level in _SLIDE_QUALITY_CRITERIA else "generic"
    try:
        prompt = _build_slide_eval_prompt(topic, narrations, level_key)
        response = call_llm(prompt)
        parsed = _parse_json(response, "slide_eval")
        if parsed and "slides" in parsed:
            flagged = sum(1 for s in parsed["slides"] if s.get("needs_regeneration"))
            logger.info(f"Slide evals complete — {flagged}/{len(narrations)} flagged for regeneration")
            return parsed["slides"]
        logger.warning("Slide eval returned unexpected structure")
    except Exception as e:
        logger.error(f"Slide eval failed: {e}")
    return None


def run_lecture_eval(
    topic: str,
    narrations: list,
    level: str,
    call_llm,
):
    """Run only lecture-level eval. Returns the parsed dict or None on failure."""
    level_key = level if level in _LECTURE_APPROPRIATENESS_CRITERIA else "generic"
    try:
        prompt = _build_lecture_eval_prompt(topic, narrations, level_key)
        response = call_llm(prompt)
        parsed = _parse_json(response, "lecture_eval")
        if parsed and "overall_score" in parsed:
            logger.info(
                f"Lecture eval complete — overall={parsed['overall_score']} "
                f"pass={parsed.get('pass')} missing={parsed.get('missing_concepts')}"
            )
            return parsed
        logger.warning("Lecture eval returned unexpected structure")
    except Exception as e:
        logger.error(f"Lecture eval failed: {e}")
    return None


def run_evals(
    topic: str,
    narrations: list,
    level: str = None,
    call_llm=None,
):
    """
    Run slide-level and lecture-level evals on the generated narrations.

    Args:
        topic:      The video topic (e.g. "Dijkstra Algorithm")
        narrations: List of narration strings, one per slide
        level:      "basic", "intermediate", "advanced", or None for generic
        call_llm:   A callable (prompt: str) -> str. If None, evals are skipped.

    Returns:
        dict with "slide_evals" and "lecture_eval", or None if skipped/failed.
    """
    if call_llm is None:
        logger.info("Evals skipped — no LLM configured")
        return None

    if not narrations:
        logger.warning("Evals skipped — no narrations to evaluate")
        return None

    level_key = level if level in _SLIDE_QUALITY_CRITERIA else "generic"
    logger.info(f"Running evals — topic='{topic}' level={level_key} slides={len(narrations)}")

    return {
        "level": level_key,
        "slide_evals": run_slide_evals(topic, narrations, level_key, call_llm),
        "lecture_eval": run_lecture_eval(topic, narrations, level_key, call_llm),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Narration improvement
# ─────────────────────────────────────────────────────────────────────────────

_IMPROVEMENT_LEVEL_INSTRUCTIONS = {
    "basic": """\
LEVEL REQUIREMENTS — BASIC (student has ZERO prior knowledge):
- Define EVERY technical term immediately in plain English when you first use it
- Use a real-world analogy or everyday example for abstract concepts
- Use short, simple sentences — one idea per sentence
- Do NOT reference other topics the student hasn't learned
- Write as if explaining to a curious 15-year-old with no background""",

    "intermediate": """\
LEVEL REQUIREMENTS — INTERMEDIATE (student knows programming basics):
- Common terms (variable, loop, function, array) don't need definitions
- But domain-specific terms for THIS topic must be briefly explained
- Explain HOW the concept works, not just WHAT it is
- Include WHY this matters or WHEN you'd use it
- Give at least one concrete example""",

    "advanced": """\
LEVEL REQUIREMENTS — ADVANCED (student has solid foundations):
- Use technical terminology freely
- Go beyond definitions — cover trade-offs, edge cases, performance, or limitations
- Compare with alternative approaches when relevant
- Add insight a practitioner or interview candidate would value
- Do NOT waste words on basics the student already knows""",

    "generic": """\
LEVEL REQUIREMENTS — GENERIC (general audience, no level assumed):
- Briefly define technical terms on first use
- Be clear and direct — any motivated learner should follow
- Balance accessibility with substance — don't oversimplify but don't assume knowledge
- Give the learner real understanding, not just surface definitions""",
}


def improve_narration(
    topic: str,
    narration: str,
    level: str,
    eval_reason: str,
    slide_number: int,
    total_slides: int,
    all_narrations: list,
    call_llm,
) -> str:
    """
    Use the LLM to rewrite a narration that failed evaluation.

    Returns the improved narration text, or the original if improvement fails.
    """
    level_key = level if level in _IMPROVEMENT_LEVEL_INSTRUCTIONS else "generic"
    level_instructions = _IMPROVEMENT_LEVEL_INSTRUCTIONS[level_key]

    # Build context about what other slides cover (so this slide doesn't repeat them)
    other_slides_context = ""
    for i, n in enumerate(all_narrations):
        if i != slide_number - 1 and n.strip():
            other_slides_context += f"  Slide {i+1}: {n.strip()[:150]}...\n"

    prompt = f"""You are rewriting a narration for a student primer video lecture.
This narration will be converted to spoken audio — write it as natural speech, not text.

TOPIC: "{topic}"
THIS IS SLIDE {slide_number} OF {total_slides}.

THE ORIGINAL NARRATION THAT FAILED QUALITY EVALUATION:
\"\"\"{narration}\"\"\"

THE EVALUATOR'S REASON FOR FLAGGING THIS NARRATION:
"{eval_reason}"

{level_instructions}

{"OTHER SLIDES IN THIS LECTURE (do NOT repeat their content):" + chr(10) + other_slides_context if other_slides_context else ""}

YOUR TASK: Rewrite this narration to fix the problem identified by the evaluator.

STRICT RULES:
1. FIX the specific issue described in the evaluator's reason — this is your primary goal
2. Stay on-topic — this MUST be about "{topic}"
3. Keep the length between 50–150 words — this is spoken narration, not an essay
4. Write as SPEECH — use natural spoken language, not formal written text
   (Good: "Let's think about this..." Bad: "In this section, we shall examine...")
5. Do NOT start with "Welcome" or "In this slide" — jump straight into the content
6. Do NOT repeat content covered in other slides
7. Slide {slide_number} of {total_slides} means:
   {"- This is the OPENING slide — introduce the topic and WHY it matters" if slide_number == 1 else ""}
   {"- This is the CLOSING slide — summarize key takeaways" if slide_number == total_slides else ""}
   {"- This is a MIDDLE slide — go deeper into a specific concept" if 1 < slide_number < total_slides else ""}

Respond with ONLY the rewritten narration. No quotes, no labels, no explanation.
Just the narration text exactly as it should be spoken aloud."""

    try:
        improved = call_llm(prompt).strip()
        # Remove any accidental quotes the LLM might wrap it in
        if improved.startswith('"') and improved.endswith('"'):
            improved = improved[1:-1]
        if improved.startswith("'") and improved.endswith("'"):
            improved = improved[1:-1]

        word_count = len(improved.split())
        if word_count < 10:
            logger.warning(f"Improved narration too short ({word_count} words) — keeping original")
            return narration
        if word_count > 300:
            logger.warning(f"Improved narration too long ({word_count} words) — keeping original")
            return narration

        logger.info(f"Narration improved for slide {slide_number} ({word_count} words)")
        return improved

    except Exception as e:
        logger.error(f"Narration improvement failed for slide {slide_number}: {e}")
        return narration


# ─────────────────────────────────────────────────────────────────────────────
# Eval + Improve loop
# ─────────────────────────────────────────────────────────────────────────────

MAX_EVAL_RETRIES = 3


def eval_and_improve(
    topic: str,
    narrations: list,
    level: str = None,
    call_llm=None,
):
    """
    Run evals on narrations. If any slide scores below 3, rewrite it and re-eval.
    Repeats until all slides pass or MAX_EVAL_RETRIES is reached.

    Args:
        topic:      The video topic
        narrations: List of narration strings (will be modified in-place)
        level:      "basic", "intermediate", "advanced", or None
        call_llm:   (prompt: str) -> str callable, or None to skip evals

    Returns:
        (narrations, final_evals_result)
        - narrations: the improved list (same object, modified in-place)
        - final_evals_result: the final eval dict, or None if evals were skipped
    """
    if call_llm is None:
        logger.info("Eval loop skipped — no LLM configured")
        return narrations, None

    level_key = level if level in _SLIDE_QUALITY_CRITERIA else "generic"
    slide_evals = None

    for attempt in range(1, MAX_EVAL_RETRIES + 1):
        logger.info(f"Eval loop — attempt {attempt}/{MAX_EVAL_RETRIES}")

        slide_evals = run_slide_evals(topic, narrations, level_key, call_llm)
        if slide_evals is None:
            return narrations, None

        flagged = [s for s in slide_evals if s.get("needs_regeneration")]

        if not flagged:
            logger.info(f"All slides passed eval on attempt {attempt}")
            break

        logger.info(
            f"Attempt {attempt}: {len(flagged)}/{len(narrations)} slides flagged — "
            f"improving narrations..."
        )

        if attempt == MAX_EVAL_RETRIES:
            logger.warning(
                f"Max retries ({MAX_EVAL_RETRIES}) reached. "
                f"{len(flagged)} slides still below threshold — proceeding anyway."
            )
            break

        for s in flagged:
            idx = s["slide"] - 1
            if 0 <= idx < len(narrations):
                narrations[idx] = improve_narration(
                    topic=topic,
                    narration=narrations[idx],
                    level=level,
                    eval_reason=s.get("reason", "Quality below threshold"),
                    slide_number=s["slide"],
                    total_slides=len(narrations),
                    all_narrations=narrations,
                    call_llm=call_llm,
                )

    # Lecture eval runs once — on the final narrations after all retries
    lecture_eval = run_lecture_eval(topic, narrations, level_key, call_llm)

    final_result = {
        "level": level_key,
        "slide_evals": slide_evals,
        "lecture_eval": lecture_eval,
        "eval_attempts": attempt,
    }

    return narrations, final_result
