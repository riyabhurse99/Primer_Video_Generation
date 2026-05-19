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

Summary: the full call sequence for a 5-slide video
                                                                                                                                                                                                                
  eval_and_improve() called
    └── run_slide_evals()          → [eval:slide]  "5 slides · 0 LLM-flagged"                                                                                                                                   
    └── _length_violations()       → [Length check] "5 slides flagged (too long)"                                                                                                                               
    └── improve_narration() × 5    → [CLAUDE eval:improve] + [REWRITE WAS/NOW] × 5                                                                                                                              
    └── run_slide_evals() again    → [eval:slide]  "5 slides · 0 flagged" ✓                                                                                                                                     
    └── (if lecture eval on)                                                                                                                                                                                    
        └── run_lecture_eval()     → [eval:lecture] pass/fail + scores                                                                                                                                          
        └── (if failed) improve × N again                                                                                                                                                                       

"""

import json
import re
from utils.logger import get_logger
import utils.run_logger as run_logger

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
- WHAT DISTINGUISHES 4 FROM 3: A score of 3 means the narration explains the concept
  correctly in simple language but stays abstract. A score of 4 means it includes a
  concrete real-world analogy that makes the concept visually click for a complete beginner.
  Example of 3: "A queue is a data structure where elements are added at the back and
  removed from the front." Example of 4: "Think of a queue like a line at a coffee shop
  — the first person who joined is the first person served. That's exactly how a queue
  data structure works."
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

DECIDING WHAT COUNTS AS DOMAIN-SPECIFIC (for intermediate jargon calls):
The test: would a developer with 1-2 years of general experience, but no background
in this specific topic, know this term? If yes → no explanation needed. If no → a
brief definition is required.

ASSUMED-KNOWN (use freely — no definition needed):
  variable, loop, function, array, object, class, method, argument, return value,
  recursion, boolean, string, integer, null/None, index, pointer, stack (call stack),
  heap (memory region), thread, process, file I/O, HTTP, JSON, API (REST API concept)

ALWAYS EXPLAIN (even for intermediate — these are topic-specific):
  Any term coined by or central to the topic being taught. For example:
  - Graph algorithms: "adjacency list", "adjacency matrix", "edge weight", "relaxation"
  - Dynamic programming: "memoization table", "overlapping subproblems", "optimal substructure"
  - Concurrency: "mutex", "semaphore", "deadlock condition", "race condition"
  When unsure, assume the term needs a brief definition.

WATCH FOR OVERLOADED TERMS: Some everyday words have a specific domain meaning.
Always clarify when a word is used in its domain-specific sense:
  - "graph" in graph theory = nodes + edges (NOT a bar chart or line plot)
  - "tree" in data structures = a hierarchical node structure (NOT a plant)
  - "stack" in data structures = LIFO container (NOT the call stack or a tech stack)
  - "hash" as a noun = the output of a hash function (NOT the # symbol)
One parenthetical clarification on first use removes all ambiguity.

SCORING CALIBRATION FOR INTERMEDIATE:
- If the narration is just a definition with no mechanism → quality is 2 at most
- If it explains how something works but not why → quality is 3
- WHAT DISTINGUISHES 4 FROM 3: A score of 3 means the mechanism is explained but the
  example feels contrived or the motivation is vague ("it's faster" without saying when
  or why). A score of 4 means the narration uses a realistic developer scenario AND
  explains WHY this approach beats the naive alternative.
  Example of 3: "A hash map stores key-value pairs and retrieves them in O(1) time."
  Example of 4: "Imagine checking if a username is already taken — with a list you'd
  scan every entry, but a hash map jumps directly to the answer. That's why lookups
  stay fast even with millions of users."
- If it has mechanism + motivation + realistic example → quality is 4-5""",

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
- WHAT DISTINGUISHES 4 FROM 3: A score of 3 means textbook-depth coverage — correct
  but no practitioner value added. A score of 4 means the narration surfaces something
  a developer only learns from production experience: a non-obvious gotcha, a trade-off
  that changes the decision in real scenarios, or a limitation textbooks gloss over.
  Example of 3: "Quicksort has average O(n log n) time complexity but O(n²) worst case."
  Example of 4: "Quicksort's worst case triggers on already-sorted data — exactly what
  you get when sorting database query results. This is why production sort implementations
  use introsort, which switches to heapsort when recursion depth gets too high."
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
- WHAT DISTINGUISHES 4 FROM 3: A score of 3 means the reader knows WHAT the concept
  is after reading. A score of 4 means the reader has a mental model they can extend
  — they understand WHY it works, not just WHAT it is.
  Example of 3: "Caching stores frequently accessed data in a faster location so it
  can be retrieved quickly."
  Example of 4: "Caching works because most programs ask for the same data repeatedly
  — the first request pays the full cost, and every subsequent request is nearly free.
  That's why a 90% cache hit rate can make a system ten times faster even though only
  10% of data is cached."
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
- At least one concrete example or use-case should appear across the {num_slides} slides.""",

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
whether the narration is good enough to ship to a real student.

YOU ARE EVALUATING AI-GENERATED CONTENT — READ THIS CAREFULLY:
AI-generated educational narrations have well-documented failure modes. You must
actively look for these, not assume they are absent:
- Sounds authoritative but explains nothing concrete (uses the right words, teaches nothing)
- Correct at a surface level but too shallow to actually help the student
- Uses domain-specific terms without defining them, or defines them circularly
- Pitched at the wrong level — too basic or too advanced for the stated audience
- Plausible-sounding but factually wrong claims stated with full confidence

Do NOT give the benefit of the doubt. If you are unsure whether an explanation is
clear enough for the target audience, assume it is not. A student listening to this
narration as audio has no fallback — if it is unclear, they are stuck. Your job is
to find problems, not to confirm that content is adequate.

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

Respond ONLY with valid JSON. No text before or after the JSON block.
{{
  "slides": [
    {{
      "slide": 1,
      "relevance_score": <1-5>,
      "quality_score": <1-5>,
      "reason": "<cite specific evidence from the narration text>"
    }}
  ]
}}

The "slides" array MUST contain exactly {len(narrations)} entries, one per slide in order."""


def _build_lecture_eval_prompt(topic: str, narrations: list, level: str) -> str:
    level_key = level if level in _LECTURE_APPROPRIATENESS_CRITERIA else "generic"
    appropriateness = _LECTURE_APPROPRIATENESS_CRITERIA[level_key].replace(
        "{num_slides}", str(len(narrations))
    )
    level_label = level.upper() if level else "GENERIC"

    numbered = "\n\n".join(
        f'[Slide {i+1}] """\n{n.strip() or "(empty)"}\n"""'
        for i, n in enumerate(narrations)
    )

    return f"""You are a strict quality evaluator for AI-generated educational video lectures.

CONTEXT: An AI system automatically generates {len(narrations)}-slide primer video lectures
for students. Each slide has narration text that is read aloud as audio. You are evaluating
whether this lecture, as a whole, would give a student a clear understanding of the topic.

YOU ARE EVALUATING AI-GENERATED CONTENT — READ THIS CAREFULLY:
AI-generated lectures have well-documented failure modes at the whole-lecture level:
- Slides that are individually acceptable but repeat the same point in different words
- A lecture that covers the vocabulary of a topic without building any real understanding
- Flow that feels logical on paper but jumps between unrelated ideas in practice
- Appropriateness mismatches: uses jargon the audience doesn't know, or over-explains
  things the audience already knows

Do NOT give the benefit of the doubt. If you are unsure whether a lecture hangs
together or whether it's truly appropriate for the target level, assume it does not.
Your job is to find problems, not to confirm that the lecture is adequate.

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

FOR "missing_concepts": List concepts from "{topic}" that the lecture did NOT cover but
that you are CONFIDENT a student at this level genuinely needs. Only include a concept
if you are certain it is standard and important for this topic — do not guess for obscure
or compound topics. Use specific names, not vague descriptions.
  Good: ["time complexity of Dijkstra", "negative edge weights limitation"]
  Bad: ["more detail needed", "some concepts are missing"]
If nothing important is clearly missing, use an empty array [].
This field is advisory — it guides a rewrite pass, it is not a definitive gap analysis.

FOR "verdict": Write exactly 2 sentences. First sentence: what this lecture does well.
Second sentence: the single most important thing it fails at or is missing.
If the lecture is excellent, the second sentence should identify a minor improvement.

Respond ONLY with valid JSON. No text before or after the JSON block.
{{
  "coverage_score": <1-5>,
  "flow_score": <1-5>,
  "appropriateness_score": <1-5>,
  "missing_concepts": ["<specific concept name>"],
  "verdict": "<sentence 1: strength>. <sentence 2: weakness or missing piece>."
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
    expected = len(narrations)

    for attempt in range(1, 3):
        try:
            prompt = _build_slide_eval_prompt(topic, narrations, level_key)
            response = call_llm(prompt)
            parsed = _parse_json(response, "slide_eval")
            if parsed and "slides" in parsed:
                slides = parsed["slides"]
                if len(slides) != expected:
                    logger.warning(
                        f"Slide eval returned {len(slides)} entries for {expected} slides "
                        f"(attempt {attempt}/2) — "
                        f"{'retrying' if attempt == 1 else 'giving up'}"
                    )
                    continue
                # Compute needs_regeneration from the raw scores — never trust the LLM to
                # derive it correctly. An LLM can assign relevance=2, quality=2 but still
                # return needs_regeneration=false when it drifts from instructions.
                for s in slides:
                    r = s.get("relevance_score", 5)
                    q = s.get("quality_score", 5)
                    s["needs_regeneration"] = r < 3 or q < 3
                flagged = sum(1 for s in slides if s["needs_regeneration"])
                logger.info(f"Slide evals complete — {flagged}/{expected} flagged for regeneration")
                run_logger.log_eval(
                    "slide",
                    attempt=attempt,
                    total=expected,
                    flagged=flagged,
                    slide_details=[
                        {
                            "slide": s["slide"],
                            "relevance": s.get("relevance_score"),
                            "quality": s.get("quality_score"),
                            "needs_regen": s.get("needs_regeneration"),
                            "reason": s.get("reason") or "",
                        }
                        for s in slides
                    ],
                )
                return slides
            logger.warning(f"Slide eval returned unexpected structure (attempt {attempt}/2)")
        except Exception as e:
            logger.error(f"Slide eval failed (attempt {attempt}/2): {e}")

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
        if parsed and "coverage_score" in parsed:
            c = parsed.get("coverage_score", 0)
            f = parsed.get("flow_score", 0)
            a = parsed.get("appropriateness_score", 0)
            parsed["overall_score"] = round((c + f + a) / 3, 1)
            parsed["pass"] = parsed["overall_score"] >= 3.5 and c >= 3 and f >= 3 and a >= 3
            logger.info(
                f"Lecture eval complete — overall={parsed['overall_score']} "
                f"pass={parsed['pass']} missing={parsed.get('missing_concepts')}"
            )
            run_logger.log_eval(
                "lecture",
                overall_score=parsed["overall_score"],
                coverage=parsed.get("coverage_score"),
                flow=parsed.get("flow_score"),
                appropriateness=parsed.get("appropriateness_score"),
                passed=parsed.get("pass", False),
                verdict=parsed.get("verdict") or "",
                missing_concepts=parsed.get("missing_concepts", []),
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
    lecture_context: dict = None,
) -> str:
    """
    Use the LLM to rewrite a narration that failed evaluation.

    Returns the improved narration text, or the original if improvement fails.
    lecture_context: optional dict from run_lecture_eval — if provided, missing
    concepts and verdict are injected so the rewrite can address lecture-level gaps.
    """
    level_key = level if level in _IMPROVEMENT_LEVEL_INSTRUCTIONS else "generic"
    level_instructions = _IMPROVEMENT_LEVEL_INSTRUCTIONS[level_key]

    # Build context about what other slides cover (so this slide doesn't repeat them)
    other_slides_context = ""
    for i, n in enumerate(all_narrations):
        if i != slide_number - 1 and n.strip():
            other_slides_context += f"  Slide {i+1}: {n.strip()[:150]}...\n"

    # Inject lecture-level feedback when rewriting after a failed lecture eval
    lecture_feedback = ""
    if lecture_context:
        missing = lecture_context.get("missing_concepts", [])
        verdict = lecture_context.get("verdict", "")
        missing_str = ", ".join(missing) if missing else "none identified"
        lecture_feedback = f"""
LECTURE-LEVEL FEEDBACK (the full lecture was evaluated and needs improvement):
Concepts the lecture may have missed (advisory — only weave in if genuinely relevant): {missing_str}
Overall verdict: {verdict}
If this slide can naturally address any of the above without forcing it, weave it in.
Do NOT invent content to satisfy this list — only add what actually fits this slide's topic.
"""

    prompt = f"""You are rewriting a narration for a student primer video lecture.
This narration will be converted to spoken audio — write it as natural speech, not text.

TOPIC: "{topic}"
THIS IS SLIDE {slide_number} OF {total_slides}.

THE ORIGINAL NARRATION THAT FAILED QUALITY EVALUATION:
\"\"\"{narration}\"\"\"

THE EVALUATOR'S REASON FOR FLAGGING THIS NARRATION:
"{eval_reason}"

{level_instructions}
{lecture_feedback}
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
        run_logger.log_narration_improve(
            slide_num=slide_number,
            original=narration,
            improved=improved,
            reason=eval_reason,
        )
        return improved

    except Exception as e:
        logger.error(f"Narration improvement failed for slide {slide_number}: {e}")
        return narration


# ─────────────────────────────────────────────────────────────────────────────
# Lecture eval pass gate
# ─────────────────────────────────────────────────────────────────────────────

def _lecture_passed(result: dict) -> bool:
    """
    Returns the pass verdict from the lecture eval result.

    overall_score and pass are both computed in Python inside run_lecture_eval
    (not by the LLM), so this value is authoritative:
      pass = overall_score >= 3.5 AND every dimension (coverage, flow, appropriateness) >= 3
    """
    return result.get("pass", False)


# ─────────────────────────────────────────────────────────────────────────────
# Eval + Improve loop
# ─────────────────────────────────────────────────────────────────────────────

MAX_EVAL_RETRIES = 3

_MIN_NARRATION_WORDS = 40
_MAX_NARRATION_WORDS = 200


def _length_violations(narrations: list) -> list:
    """
    Programmatic length gate — returns flagged-slide dicts for narrations outside
    the target word range. Format matches slide eval entries so they merge directly
    into the flagged list without special-casing.
    """
    violations = []
    for i, n in enumerate(narrations):
        wc = len(n.split()) if n.strip() else 0
        if wc < _MIN_NARRATION_WORDS:
            violations.append({
                "slide": i + 1,
                "needs_regeneration": True,
                "reason": (
                    f"Narration is too short ({wc} words). "
                    f"Target is {_MIN_NARRATION_WORDS}–{_MAX_NARRATION_WORDS} words for spoken audio. "
                    "Expand with more explanation or a concrete example."
                ),
            })
        elif wc > _MAX_NARRATION_WORDS:
            violations.append({
                "slide": i + 1,
                "needs_regeneration": True,
                "reason": (
                    f"Narration is too long ({wc} words). "
                    f"Target is {_MIN_NARRATION_WORDS}–{_MAX_NARRATION_WORDS} words for spoken audio. "
                    "Trim — remove repetition and cut anything that doesn't add new information."
                ),
            })
    return violations


def eval_and_improve(
    topic: str,
    narrations: list,
    level: str = None,
    call_llm=None,
    do_lecture_eval: bool = False,
    eval_call_llm=None,
):
    """
    Run evals on narrations. If any slide scores below 3 or is outside the target
    word range, rewrite it and re-eval. Repeats until all slides pass or
    MAX_EVAL_RETRIES is reached.

    Args:
        topic:            The video topic
        narrations:       List of narration strings (will be modified in-place)
        level:            "basic", "intermediate", "advanced", or None
        call_llm:         (prompt: str) -> str callable for content generation.
                          Also used for evals if eval_call_llm is not provided.
        do_lecture_eval:  If True, run a lecture-level eval after the slide loop.
                          If that eval fails, one extra rewrite pass is triggered
                          using the lecture eval findings as additional context.
        eval_call_llm:    Optional separate callable for eval calls only.
                          Pass a different model here to avoid same-model judge bias.
                          Defaults to call_llm if not provided.

    Returns:
        (narrations, final_evals_result)
        - narrations: the improved list (same object, modified in-place)
        - final_evals_result: dict with slide_evals, lecture_eval, eval_attempts,
          and escalated_slides (slides that shipped despite failing all retries).
          None if evals were skipped entirely.
    """
    if call_llm is None:
        logger.info("Eval loop skipped — no LLM configured")
        return narrations, None

    # Use a separate model for evals if provided — avoids same-model judge bias.
    _eval_llm = eval_call_llm or call_llm

    level_key = level if level in _SLIDE_QUALITY_CRITERIA else "generic"
    slide_evals = None
    escalated_slide_nums = set()  # permanently escalated, never retried again
    escalated_slides = []         # ordered list for the final result
    attempt = 0

    for attempt in range(1, MAX_EVAL_RETRIES + 1):
        logger.info(f"Eval loop — attempt {attempt}/{MAX_EVAL_RETRIES}")

        slide_evals = run_slide_evals(topic, narrations, level_key, _eval_llm)
        if slide_evals is None:
            return narrations, None

        # Build the flagged list, excluding slides already permanently escalated.
        raw_flagged = [
            s for s in slide_evals
            if s.get("needs_regeneration") and s.get("slide") not in escalated_slide_nums
        ]
        # Merge in length violations (also excluding already-escalated slides).
        already_flagged_nums = {s["slide"] for s in raw_flagged}
        for v in _length_violations(narrations):
            if v["slide"] not in escalated_slide_nums and v["slide"] not in already_flagged_nums:
                raw_flagged.append(v)
                already_flagged_nums.add(v["slide"])

        # Three-state split:
        #   ESCALATE immediately — both dimensions are 1, the slide is fundamentally broken.
        #   r=1 with q=4 (off-topic but well-explained) is worth retrying — the rewriter
        #   can redirect it. q=1 with r=4 (on-topic but totally unclear) is also worth
        #   retrying. Only r=1 AND q=1 means the slide has no salvageable starting point.
        #   Length violations (no relevance/quality fields) default to 5 → never escalated.
        #   REGENERATE — borderline (score=2) or length issues. Worth retrying.
        newly_escalated = [
            s for s in raw_flagged
            if s.get("relevance_score", 5) == 1 and s.get("quality_score", 5) == 1
        ]
        for s in newly_escalated:
            escalated_slide_nums.add(s["slide"])
            escalated_slides.append(s["slide"])
            logger.warning(
                f"Slide {s['slide']} scored 1 on both dimensions — "
                f"escalating immediately, not retrying."
            )

        to_regenerate = [s for s in raw_flagged if s["slide"] not in escalated_slide_nums]

        # Log length violations separately — they are not visible in the slide eval log
        # (run_slide_evals only counts LLM-score failures, not word-count violations)
        llm_flagged_nums = {s["slide"] for s in slide_evals if s.get("needs_regeneration", False)}
        length_only = [s for s in to_regenerate if s["slide"] not in llm_flagged_nums]
        if length_only:
            details = "; ".join(
                f"slide {s['slide']}: {s['reason'][:80]}" for s in length_only
            )
            run_logger.log_step(
                "Length check",
                f"{len(length_only)} slide(s) flagged for word-count violations — {details}",
            )

        if not to_regenerate:
            logger.info(f"All retryable slides passed eval on attempt {attempt}")
            break

        logger.info(
            f"Attempt {attempt}: {len(to_regenerate)} to regenerate, "
            f"{len(newly_escalated)} newly escalated — improving narrations..."
        )

        if attempt == MAX_EVAL_RETRIES:
            for s in to_regenerate:
                escalated_slide_nums.add(s["slide"])
                escalated_slides.append(s["slide"])
            logger.warning(
                f"Max retries ({MAX_EVAL_RETRIES}) reached. "
                f"Escalating slides {sorted(escalated_slides)} — shipping despite failing eval."
            )
            break

        for s in to_regenerate:
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

    # Lecture eval — optional, only runs when do_lecture_eval=True
    lecture_eval_result = None
    if do_lecture_eval:
        logger.info("Running lecture eval on final narrations...")
        lecture_eval_result = run_lecture_eval(topic, narrations, level_key, _eval_llm)

        if lecture_eval_result and not _lecture_passed(lecture_eval_result):
            logger.info(
                f"Lecture eval failed — overall={lecture_eval_result.get('overall_score')} "
                f"coverage={lecture_eval_result.get('coverage_score')} "
                f"flow={lecture_eval_result.get('flow_score')} "
                f"appropriateness={lecture_eval_result.get('appropriateness_score')} — "
                f"running one final improvement pass with lecture context..."
            )
            # One extra slide eval pass, rewrites inject the lecture findings
            final_slide_evals = run_slide_evals(topic, narrations, level_key, _eval_llm)
            if final_slide_evals:
                flagged_final = [s for s in final_slide_evals if s.get("needs_regeneration")]
                if flagged_final:
                    logger.info(f"  {len(flagged_final)} slides flagged — rewriting with lecture context...")
                    for s in flagged_final:
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
                                lecture_context=lecture_eval_result,
                            )
                else:
                    logger.warning(
                        "Lecture eval failed but no individual slides were flagged — "
                        "no further rewrites possible."
                    )
        elif lecture_eval_result and _lecture_passed(lecture_eval_result):
            logger.info(
                f"Lecture eval passed — overall={lecture_eval_result.get('overall_score')} "
                f"coverage={lecture_eval_result.get('coverage_score')} "
                f"flow={lecture_eval_result.get('flow_score')} "
                f"appropriateness={lecture_eval_result.get('appropriateness_score')} — "
                f"no extra rewrite needed."
            )

    final_result = {
        "level": level_key,
        "slide_evals": slide_evals,
        "lecture_eval": lecture_eval_result,
        "eval_attempts": attempt,
        "escalated_slides": escalated_slides,
    }

    return narrations, final_result
