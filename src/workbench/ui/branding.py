"""Trusted shell markup and bundled assets; never accepts research/source text."""

import logging
from base64 import b64encode
from functools import lru_cache
from importlib.resources import files

import streamlit as st

logger = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def bundled_data_uri(relative_name: str, media_type: str) -> str | None:
    """Load fixed application-owned names, independent of the working directory."""
    try:
        data = files("workbench.ui").joinpath("assets", relative_name).read_bytes()
    except OSError:
        logger.warning("Atlas decorative asset unavailable: %s", relative_name)
        return None
    return f"data:{media_type};base64,{b64encode(data).decode('ascii')}"


def _atlas(*, sidebar: bool = False) -> str:
    source = bundled_data_uri("atlas-green.png", "image/png")
    if source is None:
        return '<div class="atlas-text-brand">ATLAS LOCALIS</div>'
    variant = "sidebar" if sidebar else "hero"
    alt = "" if sidebar else "Pixel-art Atlas holding the Earth"
    return (
        f'<img class="atlas-sprite atlas-sprite--{variant}" src="{source}" '
        f'width="1199" height="1312" alt="{alt}">'
    )


def render_header(slot, *, compact: bool):
    variant = " atlas-wordmark--compact" if compact else ""
    slot.html(f"""<header class="atlas-header">
      <div class="atlas-kicker">LOCAL RESEARCH TERMINAL / WIKIPEDIA</div>
      <h1 class="atlas-wordmark{variant}">ATLAS LOCALIS</h1>
      <p class="atlas-tagline">Historical research. Local evidence. Traceable answers.</p>
    </header>""")


def render_sidebar_brand(slot, *, show_atlas: bool):
    brand = (
        _atlas(sidebar=True)
        if show_atlas
        else '<div class="atlas-text-brand">ATLAS LOCALIS</div>'
    )
    slot.html(f"""<div class="atlas-sidebar-brand">
      <div class="atlas-kicker">ATLAS / SESSION</div>{brand}
    </div>""")


def render_welcome():
    st.html(f"""<section class="atlas-welcome">
      <div class="atlas-invitation">
        <h2>READY FOR A QUESTION</h2>
        <p>Explore an event, compare historical developments, or trace a chronology.</p>
        <div class="atlas-example">
          <div class="atlas-kicker">EXAMPLE QUESTION</div>
          <p>When did the Roman Republic end?</p>
        </div>
      </div>
      <div class="atlas-art">{_atlas()}</div>
    </section>""")
