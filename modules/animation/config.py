"""
Animation Config — Scaler 3.0 Design System for Manim
=====================================================
Hex colors, font settings, and scene defaults that match
the slide renderer's visual identity.
"""

# ── Scaler 3.0 palette (hex for Manim) ────────────────────────────────────────
NAVY = "#011845"
BRAND_BLUE = "#0055FF"
CTA_BLUE = "#004CE5"
ICE_BLUE = "#E9F1FF"
BG_COLOR = "#FCFCFC"
TEXT_HEADING = "#101E37"
TEXT_PRIMARY = "#0B1529"
ACCENT_TEAL = "#00C2A8"
ACCENT_CORAL = "#FF6B6B"
ACCENT_GOLD = "#F5A623"

# ── Element colors ────────────────────────────────────────────────────────────
NODE_FILL = ICE_BLUE             # default box/circle fill
NODE_STROKE = NAVY               # border color
NODE_HIGHLIGHT = BRAND_BLUE      # when element is active/selected
NODE_SECONDARY = ACCENT_TEAL     # secondary highlight
ARROW_COLOR = BRAND_BLUE
LABEL_COLOR = TEXT_PRIMARY
POINTER_COLOR = ACCENT_CORAL     # for pointers in linked lists, etc.
VISITED_COLOR = "#A0B4D8"        # muted blue for visited nodes

# ── Scene defaults ────────────────────────────────────────────────────────────
SCENE_BG = NAVY                  # dark navy background (like slide header area)
RESOLUTION = (1920, 1080)
FPS = 24
QUALITY = "medium_quality"       # Manim quality preset (720p, good speed/quality)

# ── Font ──────────────────────────────────────────────────────────────────────
FONT_NAME = "Plus Jakarta Sans"  # Manim will fall back to default if not found

# ── Timing defaults ──────────────────────────────────────────────────────────
DEFAULT_ANIM_DURATION = 5.0      # total animation duration in seconds
STEP_DURATION = 0.8              # duration per step (swap, insert, highlight)
FADE_DURATION = 0.4              # fade in/out
WAIT_AFTER = 0.3                 # pause after each step
