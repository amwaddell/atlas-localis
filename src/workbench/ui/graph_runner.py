"""Presentation adapter for allowlisted application events only."""

from contextlib import closing


def run_application_streaming(
    application, value, *, on_progress=None, cancel_event=None
):
    result = None
    with closing(application.stream(value, cancel_event=cancel_event)) as events:
        for event in events:
            if event.kind == "run_finished":
                result = event.result
            elif on_progress:
                try:
                    on_progress(event)
                except Exception:
                    pass  # A broken progress display cannot change research.
    return result
