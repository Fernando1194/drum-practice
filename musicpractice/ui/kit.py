"""Top-down drum kit diagram (right-handed setup, drummer at the bottom) as inline SVG.

Each piece is a <g class="mp-pc" data-p="piece"> with:
  .shape  the drum head / cymbal / kick shell
  .flash  same shape, highlighter yellow, faded in briefly on every hit
  .soon   a ring that brightens as the next hit on that piece approaches
"""
from __future__ import annotations

from ..plugins.drums import COLORS, LABELS

# (piece, kind, cx, cy, r)   kick is drawn separately as a shell seen from above
LAYOUT = [
    ("crash", "cymbal", 88, 58, 48),
    ("ride", "cymbal", 322, 70, 54),
    ("hihat", "cymbal", 45, 150, 36),
    ("tom_high", "drum", 160, 108, 32),
    ("tom_mid", "drum", 238, 108, 35),
    ("snare", "drum", 115, 205, 38),
    ("tom_floor", "drum", 305, 205, 44),
]
KICK = (158, 148, 92, 64)  # x, y, w, h


def _circle_piece(piece, kind, cx, cy, r, used):
    cls = f"mp-pc mp-pc--{kind}" + ("" if used else " is-unused")
    rings = ""
    if kind == "cymbal":
        rings = (f'<circle class="ring" cx="{cx}" cy="{cy}" r="{r * 0.68:.1f}"/>'
                 f'<circle class="ring" cx="{cx}" cy="{cy}" r="{r * 0.38:.1f}"/>'
                 f'<circle class="bell" cx="{cx}" cy="{cy}" r="{r * 0.16:.1f}"/>')
    # cymbal names sit on the lower part of the cymbal, clear of the bell and of neighbors
    label_y = cy + r * 0.66 + 4 if kind == "cymbal" else cy + 4
    label_cls = "lbl lbl--cym" if kind == "cymbal" else "lbl"
    return (f'<g class="{cls}" data-p="{piece}" style="--c:{COLORS[piece]}">'
            f'<circle class="soon" cx="{cx}" cy="{cy}" r="{r + 5}"/>'
            f'<circle class="shape" cx="{cx}" cy="{cy}" r="{r}"/>{rings}'
            f'<circle class="flash" cx="{cx}" cy="{cy}" r="{r}"/>'
            f'<text class="{label_cls}" x="{cx}" y="{label_y:.0f}">{LABELS[piece]}</text></g>')


def kit_svg(used: set[str]) -> str:
    x, y, w, h = KICK
    kick_cls = "mp-pc mp-pc--kick" + ("" if "kick" in used else " is-unused")
    kick = (f'<g class="{kick_cls}" data-p="kick" style="--c:{COLORS["kick"]}">'
            f'<rect class="soon" x="{x - 5}" y="{y - 5}" width="{w + 10}" height="{h + 10}" rx="14"/>'
            f'<rect class="shape" x="{x}" y="{y}" width="{w}" height="{h}" rx="9"/>'
            f'<line class="head" x1="{x + 4}" y1="{y + h - 3}" x2="{x + w - 4}" y2="{y + h - 3}"/>'
            f'<rect class="pedal" x="{x + w / 2 - 9}" y="{y + h + 2}" width="18" height="22" rx="3"/>'
            f'<rect class="flash" x="{x}" y="{y}" width="{w}" height="{h}" rx="9"/>'
            f'<text class="lbl" x="{x + w / 2}" y="{y + h / 2 + 4}">Kick</text></g>')
    # kick first so the toms sit on top of it, like on a real kit
    pieces = "".join(_circle_piece(p, k, cx, cy, r, p in used) for p, k, cx, cy, r in LAYOUT)
    throne = '<circle class="throne" cx="204" cy="276" r="14"/>'
    return (f'<svg class="mp-kit-svg" viewBox="0 0 380 296" role="img" '
            f'aria-label="Drum kit seen from above; pieces light up when they are played">'
            f'{kick}{pieces}{throne}</svg>')
