"""
Animation Detector
==================
Analyzes slide narration via Claude and decides if an animated
visualization would help. If yes, returns a structured animation
spec that the Manim renderer can execute.

Animation types:
  - array:       highlight, swap, sort steps
  - linked_list: traverse, insert, delete
  - tree:        traverse (BFS/DFS), insert
  - graph:       BFS, DFS, Dijkstra path highlighting
  - stack_queue: push/pop, enqueue/dequeue
  - flow:        process flow with labeled boxes and arrows
"""

import json
import re
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Claude prompt ─────────────────────────────────────────────────────────────

DETECT_PROMPT = """You are analyzing a slide's narration to decide if a Manim animation is truly necessary.

NARRATION:
\"\"\"{narration}\"\"\"

SLIDE TOPIC: "{topic}"
SLIDE TITLE: "{slide_title}"

DEFAULT: Return null. Only produce an animation spec when the bar below is clearly met.

ANIMATE ONLY IF ALL of these are true:
1. The narration explicitly walks through an algorithm or data structure operation STEP BY STEP
2. Each step changes visible state (elements moving, pointers updating, nodes highlighted)
3. Seeing it animated would be SIGNIFICANTLY clearer than a static diagram
4. The steps map directly and clearly onto one of the supported animation types below

ALWAYS return null for:
- Conceptual explanations, definitions, motivation, or history
- Topics where a diagram or text is sufficient
- Anything vague or high-level (e.g. "how sorting works in general")
- Process flows, architecture diagrams, or comparisons (use whiteboard sketch instead)
- Any doubt — if you are not confident animation helps, return null

STRONG PREFERENCE FOR NULL. Most slides do NOT need animation.

AVAILABLE ANIMATION TYPES:

1. "array" — visualize array operations
   spec: {{ "values": [4, 2, 7, 1, 3], "operations": ["highlight:0", "highlight:1", "swap:0,1", "highlight:2"] }}
   operations: "highlight:<index>", "swap:<i>,<j>", "insert:<index>,<value>", "remove:<index>"

2. "linked_list" — visualize linked list operations
   spec: {{ "nodes": ["A", "B", "C", "D"], "operations": ["traverse:0", "traverse:1", "insert:2,X", "traverse:2"] }}
   operations: "traverse:<index>", "insert:<index>,<value>", "delete:<index>"

3. "tree" — visualize binary tree operations
   spec: {{ "nodes": [10, 5, 15, 3, 7, null, 20], "operations": ["visit:0", "visit:1", "visit:3", "visit:4", "visit:2", "visit:5"] }}
   nodes: array representation of binary tree (null for empty). operations: "visit:<index>", "insert:<value>"

4. "graph" — visualize graph algorithms
   spec: {{ "nodes": ["A", "B", "C", "D"], "edges": [["A","B"], ["B","C"], ["A","D"], ["D","C"]], "operations": ["visit:A", "visit:B", "visit:D", "visit:C"] }}
   operations: "visit:<node>", "edge:<from>,<to>"

5. "stack_queue" — visualize stack or queue operations
   spec: {{ "type": "stack", "operations": ["push:A", "push:B", "push:C", "pop", "pop", "push:D"] }}
   operations: "push:<value>", "pop", "enqueue:<value>", "dequeue"

6. "flow" — visualize a process flow
   spec: {{ "steps": ["Client", "DNS", "Server", "Database"], "highlights": [0, 1, 2, 3] }}

RULES:
1. Maximum 8 operations — keep it short and clear
2. Use real values from the narration when possible
3. Labels should be SHORT (1-3 words)
4. Think about what would ACTUALLY help a student understand THIS specific narration

Respond with ONLY valid JSON. Either null (no animation needed) or:
{{
  "animation_type": "array|linked_list|tree|graph|stack_queue|flow",
  "title": "short title for the animation",
  "spec": {{ ... type-specific spec ... }}
}}"""


def detect_animation(
    topic: str,
    slide_title: str,
    narration: str,
    call_llm,
):
    """
    Ask Claude whether a slide benefits from animation.

    Returns an animation spec dict or None.
    """
    if not call_llm or not narration.strip():
        return None

    prompt = DETECT_PROMPT.format(
        narration=narration.strip(),
        topic=topic,
        slide_title=slide_title,
    )

    try:
        response = call_llm(prompt)
        clean = response.strip()

        if clean.lower() in ("null", "none", ""):
            logger.info(f"  Animation: Claude says no animation needed for '{slide_title}'")
            return None

        # Strip markdown fences
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```\s*$", "", clean).strip()

        data = json.loads(clean)
        if data is None:
            return None

        anim_type = data.get("animation_type", "")
        if anim_type not in ("array", "linked_list", "tree", "graph", "stack_queue", "flow"):
            logger.warning(f"  Animation: unknown type '{anim_type}'")
            return None

        spec = data.get("spec")
        if not spec:
            return None

        logger.info(
            f"  Animation: {data.get('title', '?')} — type={anim_type}, "
            f"{len(spec.get('operations', spec.get('highlights', [])))} steps"
        )
        return data

    except Exception as e:
        logger.warning(f"  Animation detection failed: {e}")
        return None
