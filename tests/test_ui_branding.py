"""Bundled presentation resources must not gate local research or error guidance."""

from importlib.resources import files
from pathlib import Path

from streamlit.testing.v1 import AppTest

from workbench.ui import branding, components


def test_bundled_assets_are_independent_of_working_directory(monkeypatch, tmp_path):
    branding.bundled_data_uri.cache_clear()
    monkeypatch.chdir(tmp_path)
    assets = files("workbench.ui").joinpath("assets")
    assert assets.joinpath("atlas-green.png").read_bytes().startswith(b"\x89PNG")
    for font in ("VT323-Regular", "IBMPlexMono-Regular", "IBMPlexMono-Medium"):
        uri = branding.bundled_data_uri(f"fonts/{font}.ttf", "font/ttf")
        assert uri.startswith("data:font/ttf;base64,AAEAAA")
    for name in ("VT323", "IBMPlexMono"):
        assert (
            "SIL OPEN FONT LICENSE"
            in assets.joinpath(f"fonts/OFL-{name}.txt").read_text()
        )
    branding.bundled_data_uri.cache_clear()


def test_missing_decoration_keeps_actionable_resource_error(monkeypatch, caplog):
    def missing_assets(_):
        raise FileNotFoundError("decorative asset missing")

    def missing_application():
        raise FileNotFoundError("Provisioned local index missing: fixture")

    branding.bundled_data_uri.cache_clear()
    monkeypatch.setattr(branding, "files", missing_assets)
    monkeypatch.setattr(components, "get_application", missing_application)
    try:
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / "src/workbench/ui/streamlit_app.py")
        ).run()
        assert not ui.exception
        assert "Provisioned local index missing" in ui.error[0].value
        assert "bash scripts/start_model_stack.sh" in ui.code[0].value
        assert "Atlas decorative asset unavailable" in caplog.text
        assert "ATLAS LOCALIS" in branding._atlas()
        assert not ui.chat_input
    finally:
        branding.bundled_data_uri.cache_clear()
