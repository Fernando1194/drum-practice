"""The page around the player in the player's own look: dark slate, paper-white text, one yellow
accent (the same yellow as the playhead highlights and the Steps counter). Always dark, also when
the browser is in light mode, so the page never switches look between the form and the player."""
from __future__ import annotations

import gradio as gr

INK = "#f3f5f8"          # text on dark (player --mp-ink-on-stage)
MUTED = "#9aabc2"        # secondary text (player --mp-muted-on-stage)
PAGE = "#121923"         # page background, a step darker than the player
STAGE = "#1d2736"        # cards (player --mp-stage)
STAGE_2 = "#263244"      # inputs, inner panels (player --mp-stage-2)
LINE = "#3a4860"         # borders (player buttons)
ACCENT = "#ffd640"       # the one accent color
ACCENT_HOVER = "#ffe27a"


def _both(**kw):
    """Same value for light and dark mode."""
    out = {}
    for k, v in kw.items():
        out[k] = v
        out[k + "_dark"] = v
    return out


THEME = gr.themes.Base(
    font=["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
    font_mono=["JetBrains Mono", "ui-monospace", "Consolas", "monospace"],
    radius_size=gr.themes.sizes.radius_md,
).set(**_both(
    body_background_fill=PAGE,
    body_text_color=INK,
    body_text_color_subdued=MUTED,
    background_fill_primary=STAGE,
    background_fill_secondary=STAGE_2,
    block_background_fill=STAGE,
    block_border_color=LINE,
    block_label_background_fill=STAGE,
    block_label_text_color=MUTED,
    block_label_border_color=LINE,
    block_title_text_color=MUTED,
    border_color_primary=LINE,
    border_color_accent=ACCENT,
    color_accent_soft=STAGE_2,
    panel_background_fill=STAGE,
    panel_border_color=LINE,
    input_background_fill=STAGE_2,
    input_background_fill_focus=STAGE_2,
    input_background_fill_hover=STAGE_2,
    input_border_color=LINE,
    input_border_color_focus=ACCENT,
    input_border_color_hover=MUTED,
    input_placeholder_color="#7a8aa3",
    button_primary_background_fill=ACCENT,
    button_primary_background_fill_hover=ACCENT_HOVER,
    button_primary_border_color=ACCENT,
    button_primary_text_color=STAGE,
    button_primary_text_color_hover=STAGE,
    button_secondary_background_fill=STAGE_2,
    button_secondary_background_fill_hover="#33425a",
    button_secondary_border_color=LINE,
    button_secondary_text_color=INK,
    button_secondary_text_color_hover=INK,
    checkbox_background_color=STAGE_2,
    checkbox_background_color_selected=ACCENT,
    checkbox_border_color=LINE,
    checkbox_border_color_selected=ACCENT,
    checkbox_label_background_fill=STAGE,
    checkbox_label_background_fill_selected=STAGE_2,
    checkbox_label_border_color=LINE,
    checkbox_label_border_color_selected=ACCENT,
    slider_color=ACCENT,
    link_text_color=ACCENT,
    link_text_color_hover=ACCENT_HOVER,
    link_text_color_visited=ACCENT,
    link_text_color_active=ACCENT,
    code_background_fill=STAGE_2,
    table_border_color=LINE,
    table_even_background_fill=STAGE,
    error_background_fill="#3a1d24",
    error_border_color="#e34948",
))

# Page layout around the player. Gradio's own footer ("Use via API / Built with Gradio") and
# the form's labels are hidden: the placeholder and the button say what to do.
APP_CSS = f"""
.gradio-container {{ max-width: 1680px !important; margin: 0 auto !important; padding: 0 20px !important; }}
footer {{ display: none !important; }}
#mp-brand {{ padding: 22px 4px 6px; }}
#mp-brand .mp-brand-name {{ font-family: "Barlow Condensed", "Arial Narrow", system-ui, sans-serif; font-weight: 600;
  font-size: 34px; letter-spacing: .01em; color: {INK}; line-height: 1; }}
#mp-brand .mp-brand-name i {{ font-style: normal; color: {ACCENT}; }}
#mp-brand .mp-brand-sub {{ margin-top: 6px; color: {MUTED}; font-size: 14px; }}
#mp-input {{ background: {STAGE}; border: 0; border-radius: 14px; padding: 14px 16px 10px; gap: 8px; }}
#mp-input .mp-input-row {{ gap: 10px; align-items: stretch; }}
#mp-url textarea, #mp-url input {{ font-size: 15px !important; min-height: 44px; }}
#mp-go {{ min-height: 44px; font-weight: 700; font-size: 15px; border-radius: 8px; }}
#mp-input .mp-upload, #mp-input .mp-upload > * {{ background: transparent !important; border: 0 !important;
  box-shadow: none !important; }}
#mp-input .mp-upload .label-wrap {{ padding: 4px 2px !important; }}
#mp-input .mp-upload .label-wrap, #mp-input .mp-upload .label-wrap * {{ color: {MUTED} !important; font-size: 13px; }}
#mp-input .mp-upload .label-wrap:hover * {{ color: {INK} !important; }}
#mp-input .mp-upload table, #mp-input .mp-upload tr, #mp-input .mp-upload td,
#mp-input .mp-upload .file-preview-holder, #mp-input .mp-upload .wrap {{ background: {STAGE_2} !important;
  color: {INK} !important; border-color: {LINE} !important; }}
#mp-wrap .html-container {{ padding: 0 !important; }}
#mp-input .styler {{ background: transparent !important; gap: 8px !important; }}
#mp-notes, #mp-notes * {{ color: {MUTED} !important; font-size: 13px; }}
#mp-notes {{ padding: 0 6px; }}
#mp-notes ul {{ margin: 6px 0 2px; }}
#mp-notes:empty {{ display: none; }}
.mp-card {{ background: {STAGE} !important; border: 0 !important; border-radius: 14px !important; }}
.mp-card > .label-wrap {{ padding: 12px 16px !important; }}
.mp-card .label-wrap span {{ color: {INK}; font-weight: 600; }}
"""
