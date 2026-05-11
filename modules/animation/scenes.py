"""
Manim Scene Templates — Scaler 3.0 Branded
============================================
Standalone script executed by Python 3.11 (where Manim is installed).
Called via subprocess from the renderer.

Usage:
    python3.11 scenes.py <spec_json_path> <output_mp4_path>

Each scene class takes a spec dict and produces a short animation.
"""

import sys
import json
import os
from manim import *

# ── Scaler 3.0 colors ────────────────────────────────────────────────────────
NAVY = "#011845"
BRAND_BLUE = "#0055FF"
ICE_BLUE = "#E9F1FF"
TEXT_PRIMARY = "#0B1529"
ACCENT_TEAL = "#00C2A8"
ACCENT_CORAL = "#FF6B6B"
VISITED_COLOR = "#A0B4D8"
BG = "#011845"

# Timing
STEP_DUR = 0.6
FADE_DUR = 0.3
WAIT = 0.2


# ── Header bar (matches slide design) ────────────────────────────────────────

def _add_header(scene):
    """Add a thin brand-blue accent line at the top like the slide header."""
    line = Line(
        start=LEFT * config.frame_width / 2,
        end=RIGHT * config.frame_width / 2,
        color=BRAND_BLUE,
        stroke_width=6,
    ).to_edge(UP, buff=0)
    scene.add(line)


def _add_title(scene, title_text):
    """Add a title at the top of the scene."""
    title = Text(
        title_text,
        font_size=32,
        color=WHITE,
    ).to_edge(UP, buff=0.5)
    scene.play(FadeIn(title), run_time=FADE_DUR)
    return title


# ── Array Scene ──────────────────────────────────────────────────────────────

class ArrayScene(Scene):
    def __init__(self, spec, title="Array Operation", **kwargs):
        self.spec = spec
        self.title_text = title
        super().__init__(**kwargs)

    def construct(self):
        self.camera.background_color = BG
        _add_header(self)
        _add_title(self, self.title_text)

        values = self.spec.get("values", [1, 2, 3, 4, 5])
        operations = self.spec.get("operations", [])

        # Build array visualization
        cells = VGroup()
        labels = VGroup()
        indices = VGroup()

        for i, val in enumerate(values):
            rect = Square(side_length=0.9, color=BRAND_BLUE, fill_color=ICE_BLUE, fill_opacity=0.15)
            label = Text(str(val), font_size=28, color=WHITE)
            idx = Text(str(i), font_size=18, color=VISITED_COLOR)
            cells.add(rect)
            labels.add(label)
            indices.add(idx)

        cells.arrange(RIGHT, buff=0.15).move_to(ORIGIN)
        for i in range(len(values)):
            labels[i].move_to(cells[i])
            indices[i].next_to(cells[i], DOWN, buff=0.15)

        self.play(FadeIn(cells), FadeIn(labels), FadeIn(indices), run_time=FADE_DUR)
        self.wait(WAIT)

        # Execute operations
        for op in operations:
            if op.startswith("highlight:"):
                idx = int(op.split(":")[1])
                if idx < len(cells):
                    self.play(
                        cells[idx].animate.set_fill(BRAND_BLUE, opacity=0.5),
                        cells[idx].animate.set_stroke(WHITE, width=3),
                        run_time=STEP_DUR,
                    )
                    self.wait(WAIT)

            elif op.startswith("swap:"):
                parts = op.split(":")[1].split(",")
                i, j = int(parts[0]), int(parts[1])
                if i < len(cells) and j < len(cells):
                    # Animate swap
                    self.play(
                        cells[i].animate.set_fill(ACCENT_CORAL, opacity=0.4),
                        cells[j].animate.set_fill(ACCENT_CORAL, opacity=0.4),
                        run_time=STEP_DUR * 0.5,
                    )
                    # Swap positions
                    self.play(
                        labels[i].animate.move_to(cells[j]),
                        labels[j].animate.move_to(cells[i]),
                        run_time=STEP_DUR,
                    )
                    # Swap references
                    labels[i], labels[j] = labels[j], labels[i]
                    # Reset colors
                    self.play(
                        cells[i].animate.set_fill(ICE_BLUE, opacity=0.15),
                        cells[j].animate.set_fill(ICE_BLUE, opacity=0.15),
                        run_time=STEP_DUR * 0.5,
                    )
                    self.wait(WAIT)

            elif op.startswith("insert:"):
                parts = op.split(":")[1].split(",")
                idx_val = int(parts[0])
                new_val = parts[1] if len(parts) > 1 else "?"
                new_rect = Square(side_length=0.9, color=ACCENT_TEAL, fill_color=ACCENT_TEAL, fill_opacity=0.3)
                new_label = Text(str(new_val), font_size=28, color=WHITE)
                new_rect.next_to(cells[-1], RIGHT, buff=0.15) if cells else new_rect.move_to(ORIGIN)
                new_label.move_to(new_rect)
                self.play(FadeIn(new_rect), FadeIn(new_label), run_time=STEP_DUR)
                cells.add(new_rect)
                labels.add(new_label)
                self.wait(WAIT)

            elif op.startswith("remove:"):
                idx = int(op.split(":")[1])
                if idx < len(cells):
                    self.play(
                        cells[idx].animate.set_fill(ACCENT_CORAL, opacity=0.6),
                        run_time=STEP_DUR * 0.5,
                    )
                    self.play(FadeOut(cells[idx]), FadeOut(labels[idx]), run_time=STEP_DUR)
                    self.wait(WAIT)

        self.wait(0.5)


# ── Linked List Scene ────────────────────────────────────────────────────────

class LinkedListScene(Scene):
    def __init__(self, spec, title="Linked List", **kwargs):
        self.spec = spec
        self.title_text = title
        super().__init__(**kwargs)

    def construct(self):
        self.camera.background_color = BG
        _add_header(self)
        _add_title(self, self.title_text)

        nodes_data = self.spec.get("nodes", ["A", "B", "C"])
        operations = self.spec.get("operations", [])

        # Build linked list visualization
        node_groups = VGroup()
        arrows = VGroup()

        for i, val in enumerate(nodes_data):
            # Node = rounded rect + label
            rect = RoundedRectangle(
                corner_radius=0.15, width=1.2, height=0.7,
                color=BRAND_BLUE, fill_color=ICE_BLUE, fill_opacity=0.15,
            )
            label = Text(str(val), font_size=24, color=WHITE)
            label.move_to(rect)
            group = VGroup(rect, label)
            node_groups.add(group)

        node_groups.arrange(RIGHT, buff=1.2).move_to(ORIGIN)

        # Arrows between nodes
        for i in range(len(nodes_data) - 1):
            arrow = Arrow(
                node_groups[i].get_right(), node_groups[i + 1].get_left(),
                color=BRAND_BLUE, buff=0.1, stroke_width=3,
            )
            arrows.add(arrow)

        # NULL terminator
        null_text = Text("NULL", font_size=18, color=VISITED_COLOR)
        null_text.next_to(node_groups[-1], RIGHT, buff=0.8)
        null_arrow = Arrow(
            node_groups[-1].get_right(), null_text.get_left(),
            color=VISITED_COLOR, buff=0.1, stroke_width=2,
        )

        self.play(FadeIn(node_groups), FadeIn(arrows), FadeIn(null_text), FadeIn(null_arrow), run_time=FADE_DUR)
        self.wait(WAIT)

        # Pointer arrow for traversal
        pointer = Arrow(ORIGIN, ORIGIN, color=ACCENT_CORAL, stroke_width=4)

        for op in operations:
            if op.startswith("traverse:"):
                idx = int(op.split(":")[1])
                if idx < len(node_groups):
                    node = node_groups[idx]
                    # Highlight current node
                    self.play(
                        node[0].animate.set_fill(BRAND_BLUE, opacity=0.5),
                        node[0].animate.set_stroke(WHITE, width=3),
                        run_time=STEP_DUR,
                    )
                    self.wait(WAIT)
                    # Dim it to visited
                    self.play(
                        node[0].animate.set_fill(VISITED_COLOR, opacity=0.3),
                        node[0].animate.set_stroke(VISITED_COLOR, width=2),
                        run_time=STEP_DUR * 0.5,
                    )

            elif op.startswith("insert:"):
                parts = op.split(":")[1].split(",")
                idx = int(parts[0])
                val = parts[1] if len(parts) > 1 else "?"
                new_rect = RoundedRectangle(
                    corner_radius=0.15, width=1.2, height=0.7,
                    color=ACCENT_TEAL, fill_color=ACCENT_TEAL, fill_opacity=0.3,
                )
                new_label = Text(str(val), font_size=24, color=WHITE)
                new_label.move_to(new_rect)
                new_group = VGroup(new_rect, new_label)
                # Position above the insertion point
                if idx < len(node_groups):
                    new_group.next_to(node_groups[idx], UP, buff=0.8)
                else:
                    new_group.next_to(node_groups[-1], RIGHT, buff=1.5)
                self.play(FadeIn(new_group, shift=DOWN), run_time=STEP_DUR)
                self.wait(WAIT)
                # Move into place
                target = node_groups[min(idx, len(node_groups) - 1)].get_center()
                self.play(new_group.animate.move_to(target), run_time=STEP_DUR)
                self.wait(WAIT)

            elif op.startswith("delete:"):
                idx = int(op.split(":")[1])
                if idx < len(node_groups):
                    self.play(
                        node_groups[idx][0].animate.set_fill(ACCENT_CORAL, opacity=0.5),
                        run_time=STEP_DUR * 0.5,
                    )
                    self.play(FadeOut(node_groups[idx], shift=DOWN), run_time=STEP_DUR)
                    self.wait(WAIT)

        self.wait(0.5)


# ── Tree Scene ───────────────────────────────────────────────────────────────

class TreeScene(Scene):
    def __init__(self, spec, title="Binary Tree", **kwargs):
        self.spec = spec
        self.title_text = title
        super().__init__(**kwargs)

    def construct(self):
        self.camera.background_color = BG
        _add_header(self)
        _add_title(self, self.title_text)

        nodes_arr = self.spec.get("nodes", [10, 5, 15])
        operations = self.spec.get("operations", [])

        # Build tree using Manim's Graph or manual positioning
        # Level-order array → position tree nodes
        node_mobjects = {}
        edges = []
        positions = {}

        # Calculate positions for binary tree layout
        def _tree_pos(idx, x=0, y=0, spread=2.5):
            if idx >= len(nodes_arr) or nodes_arr[idx] is None:
                return
            positions[idx] = [x, y, 0]
            _tree_pos(2 * idx + 1, x - spread / 2, y - 1.2, spread / 2)
            _tree_pos(2 * idx + 2, x + spread / 2, y - 1.2, spread / 2)

        _tree_pos(0, 0, 1.5)

        # Create node circles
        for idx in positions:
            val = nodes_arr[idx]
            circle = Circle(radius=0.4, color=BRAND_BLUE, fill_color=ICE_BLUE, fill_opacity=0.15)
            circle.move_to(positions[idx])
            label = Text(str(val), font_size=22, color=WHITE)
            label.move_to(circle)
            node_mobjects[idx] = VGroup(circle, label)

        # Create edges
        edge_lines = VGroup()
        for idx in positions:
            left = 2 * idx + 1
            right = 2 * idx + 2
            for child in (left, right):
                if child in positions:
                    line = Line(
                        node_mobjects[idx][0].get_center(),
                        node_mobjects[child][0].get_center(),
                        color=BRAND_BLUE, stroke_width=2, buff=0.4,
                    )
                    edge_lines.add(line)

        all_nodes = VGroup(*node_mobjects.values())
        self.play(FadeIn(edge_lines), FadeIn(all_nodes), run_time=FADE_DUR)
        self.wait(WAIT)

        # Execute operations
        for op in operations:
            if op.startswith("visit:"):
                idx = int(op.split(":")[1])
                if idx in node_mobjects:
                    node = node_mobjects[idx]
                    self.play(
                        node[0].animate.set_fill(BRAND_BLUE, opacity=0.6),
                        node[0].animate.set_stroke(WHITE, width=3),
                        run_time=STEP_DUR,
                    )
                    self.wait(WAIT)
                    self.play(
                        node[0].animate.set_fill(VISITED_COLOR, opacity=0.3),
                        node[0].animate.set_stroke(VISITED_COLOR, width=2),
                        run_time=STEP_DUR * 0.3,
                    )

        self.wait(0.5)


# ── Graph Scene ──────────────────────────────────────────────────────────────

class GraphScene(Scene):
    def __init__(self, spec, title="Graph Traversal", **kwargs):
        self.spec = spec
        self.title_text = title
        super().__init__(**kwargs)

    def construct(self):
        self.camera.background_color = BG
        _add_header(self)
        _add_title(self, self.title_text)

        nodes_data = self.spec.get("nodes", ["A", "B", "C", "D"])
        edges_data = self.spec.get("edges", [])
        operations = self.spec.get("operations", [])

        # Position nodes in a circle layout
        n = len(nodes_data)
        import math
        node_mobjects = {}
        radius = 2.0

        for i, name in enumerate(nodes_data):
            angle = math.pi / 2 + 2 * math.pi * i / n
            x = radius * math.cos(angle)
            y = radius * math.sin(angle) - 0.3
            circle = Circle(radius=0.45, color=BRAND_BLUE, fill_color=ICE_BLUE, fill_opacity=0.15)
            circle.move_to([x, y, 0])
            label = Text(str(name), font_size=24, color=WHITE)
            label.move_to(circle)
            node_mobjects[name] = VGroup(circle, label)

        # Draw edges
        edge_lines = {}
        for (u, v) in edges_data:
            if u in node_mobjects and v in node_mobjects:
                line = Line(
                    node_mobjects[u][0].get_center(),
                    node_mobjects[v][0].get_center(),
                    color=BRAND_BLUE, stroke_width=2, buff=0.45,
                )
                edge_lines[(u, v)] = line

        all_nodes = VGroup(*node_mobjects.values())
        all_edges = VGroup(*edge_lines.values())
        self.play(FadeIn(all_edges), FadeIn(all_nodes), run_time=FADE_DUR)
        self.wait(WAIT)

        # Execute operations
        for op in operations:
            if op.startswith("visit:"):
                name = op.split(":")[1]
                if name in node_mobjects:
                    node = node_mobjects[name]
                    self.play(
                        node[0].animate.set_fill(BRAND_BLUE, opacity=0.6),
                        node[0].animate.set_stroke(WHITE, width=3),
                        run_time=STEP_DUR,
                    )
                    self.wait(WAIT)
                    self.play(
                        node[0].animate.set_fill(VISITED_COLOR, opacity=0.3),
                        node[0].animate.set_stroke(VISITED_COLOR, width=2),
                        run_time=STEP_DUR * 0.3,
                    )

            elif op.startswith("edge:"):
                parts = op.split(":")[1].split(",")
                u, v = parts[0], parts[1]
                key = (u, v) if (u, v) in edge_lines else (v, u)
                if key in edge_lines:
                    self.play(
                        edge_lines[key].animate.set_color(ACCENT_CORAL).set_stroke(width=4),
                        run_time=STEP_DUR,
                    )
                    self.wait(WAIT)

        self.wait(0.5)


# ── Stack / Queue Scene ──────────────────────────────────────────────────────

class StackQueueScene(Scene):
    def __init__(self, spec, title="Stack / Queue", **kwargs):
        self.spec = spec
        self.title_text = title
        super().__init__(**kwargs)

    def construct(self):
        self.camera.background_color = BG
        _add_header(self)
        _add_title(self, self.title_text)

        ds_type = self.spec.get("type", "stack")
        operations = self.spec.get("operations", [])
        is_stack = ds_type == "stack"

        # Label
        type_label = Text(
            "STACK (LIFO)" if is_stack else "QUEUE (FIFO)",
            font_size=22, color=VISITED_COLOR,
        ).to_edge(LEFT, buff=1).shift(UP * 2)
        self.play(FadeIn(type_label), run_time=FADE_DUR)

        container = VGroup()
        items = []  # track (rect, label) groups currently in the structure

        # Draw container outline
        container_rect = Rectangle(
            width=2.0, height=5.0,
            color=BRAND_BLUE, stroke_width=2,
        ).move_to(ORIGIN).shift(DOWN * 0.3)
        self.play(FadeIn(container_rect), run_time=FADE_DUR)

        def _item_y(index):
            """Y position for item at index (bottom = 0)."""
            return container_rect.get_bottom()[1] + 0.5 + index * 0.7

        for op in operations:
            if op.startswith("push:") or op.startswith("enqueue:"):
                val = op.split(":")[1]
                rect = RoundedRectangle(
                    corner_radius=0.1, width=1.6, height=0.55,
                    color=ACCENT_TEAL, fill_color=ACCENT_TEAL, fill_opacity=0.3,
                )
                label = Text(str(val), font_size=22, color=WHITE)

                if is_stack:
                    # Push on top
                    y = _item_y(len(items))
                    group = VGroup(rect, label)
                    group.move_to([0, y + 2, 0])  # start above
                    label.move_to(rect)
                    self.play(FadeIn(group), run_time=FADE_DUR * 0.5)
                    self.play(group.animate.move_to([0, y, 0]), run_time=STEP_DUR)
                    label.move_to(rect)
                    items.append(group)
                else:
                    # Enqueue at back (bottom)
                    y = _item_y(len(items))
                    group = VGroup(rect, label)
                    group.move_to([3, y, 0])  # start right
                    label.move_to(rect)
                    self.play(FadeIn(group), run_time=FADE_DUR * 0.5)
                    self.play(group.animate.move_to([0, y, 0]), run_time=STEP_DUR)
                    label.move_to(rect)
                    items.append(group)
                self.wait(WAIT)

            elif op in ("pop", "dequeue"):
                if items:
                    if is_stack:
                        # Pop from top
                        top = items.pop()
                        self.play(
                            top[0].animate.set_fill(ACCENT_CORAL, opacity=0.5),
                            run_time=STEP_DUR * 0.3,
                        )
                        self.play(FadeOut(top, shift=UP), run_time=STEP_DUR)
                    else:
                        # Dequeue from front (bottom)
                        front = items.pop(0)
                        self.play(
                            front[0].animate.set_fill(ACCENT_CORAL, opacity=0.5),
                            run_time=STEP_DUR * 0.3,
                        )
                        self.play(FadeOut(front, shift=LEFT * 2), run_time=STEP_DUR)
                        # Shift remaining items down
                        anims = []
                        for idx, item in enumerate(items):
                            anims.append(item.animate.move_to([0, _item_y(idx), 0]))
                        if anims:
                            self.play(*anims, run_time=STEP_DUR * 0.5)
                    self.wait(WAIT)

        self.wait(0.5)


# ── Flow Scene ───────────────────────────────────────────────────────────────

class FlowScene(Scene):
    def __init__(self, spec, title="Process Flow", **kwargs):
        self.spec = spec
        self.title_text = title
        super().__init__(**kwargs)

    def construct(self):
        self.camera.background_color = BG
        _add_header(self)
        _add_title(self, self.title_text)

        steps = self.spec.get("steps", ["Step 1", "Step 2", "Step 3"])
        highlights = self.spec.get("highlights", list(range(len(steps))))

        # Build flow boxes
        boxes = VGroup()
        labels = VGroup()

        for step in steps:
            rect = RoundedRectangle(
                corner_radius=0.15, width=2.2, height=0.8,
                color=BRAND_BLUE, fill_color=ICE_BLUE, fill_opacity=0.15,
            )
            label = Text(str(step), font_size=20, color=WHITE)
            label.move_to(rect)
            boxes.add(rect)
            labels.add(label)

        # Arrange: horizontal if ≤4, two rows if more
        if len(steps) <= 4:
            boxes.arrange(RIGHT, buff=1.0).move_to(ORIGIN)
        else:
            top_half = VGroup(*boxes[:len(steps) // 2 + len(steps) % 2])
            bot_half = VGroup(*boxes[len(steps) // 2 + len(steps) % 2:])
            top_half.arrange(RIGHT, buff=0.8).shift(UP * 0.8)
            bot_half.arrange(RIGHT, buff=0.8).shift(DOWN * 0.8)

        for i in range(len(steps)):
            labels[i].move_to(boxes[i])

        # Arrows between consecutive boxes
        flow_arrows = VGroup()
        for i in range(len(steps) - 1):
            arrow = Arrow(
                boxes[i].get_right(), boxes[i + 1].get_left(),
                color=BRAND_BLUE, buff=0.1, stroke_width=3,
            )
            flow_arrows.add(arrow)

        self.play(FadeIn(boxes), FadeIn(labels), FadeIn(flow_arrows), run_time=FADE_DUR)
        self.wait(WAIT)

        # Highlight steps sequentially
        for idx in highlights:
            if idx < len(boxes):
                self.play(
                    boxes[idx].animate.set_fill(BRAND_BLUE, opacity=0.5),
                    boxes[idx].animate.set_stroke(WHITE, width=3),
                    run_time=STEP_DUR,
                )
                self.wait(WAIT)
                # If there's a next arrow, pulse it
                if idx < len(flow_arrows):
                    self.play(
                        flow_arrows[idx].animate.set_color(WHITE),
                        run_time=STEP_DUR * 0.4,
                    )

        self.wait(0.5)


# ── Scene dispatcher ─────────────────────────────────────────────────────────

SCENE_MAP = {
    "array": ArrayScene,
    "linked_list": LinkedListScene,
    "tree": TreeScene,
    "graph": GraphScene,
    "stack_queue": StackQueueScene,
    "flow": FlowScene,
}


def render_scene(spec_data: dict, output_path: str) -> str:
    """
    Render an animation from a spec dict.
    Returns the path to the output MP4.
    """
    anim_type = spec_data.get("animation_type", "flow")
    title = spec_data.get("title", "Animation")
    spec = spec_data.get("spec", {})

    scene_class = SCENE_MAP.get(anim_type, FlowScene)

    # Determine output dir and filename
    out_dir = os.path.dirname(output_path) or "/tmp"
    out_name = os.path.splitext(os.path.basename(output_path))[0]

    with tempconfig({
        "output_file": out_name,
        "media_dir": os.path.join(out_dir, "manim_media"),
        "quality": "medium_quality",
        "preview": False,
        "disable_caching": True,
    }):
        scene = scene_class(spec=spec, title=title)
        scene.render()
        rendered_path = str(scene.renderer.file_writer.movie_file_path)

    # Move rendered file to the requested output path
    if rendered_path != output_path and os.path.exists(rendered_path):
        import shutil
        shutil.move(rendered_path, output_path)

    return output_path


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3.11 scenes.py <spec_json_path> <output_mp4_path>")
        sys.exit(1)

    spec_path = sys.argv[1]
    output_path = sys.argv[2]

    with open(spec_path) as f:
        spec_data = json.load(f)

    result = render_scene(spec_data, output_path)
    print(f"OK:{result}")
