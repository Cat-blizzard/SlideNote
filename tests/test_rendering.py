from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from slidenote import rendering


class FakeSlide:
    def Export(self, path: str, fmt: str) -> None:
        Path(path).write_bytes(b"png")


class FakeSlides:
    def __init__(self, count: int) -> None:
        self.Count = count

    def __call__(self, index: int) -> FakeSlide:
        return FakeSlide()


class FakePresentation:
    def __init__(self, app: "FakeApp", count: int) -> None:
        self.app = app
        self.Slides = FakeSlides(count)
        self.closed = False

    def Close(self) -> None:
        self.closed = True
        self.app.open_presentations -= 1


class FakePresentations:
    def __init__(self, app: "FakeApp") -> None:
        self.app = app

    @property
    def Count(self) -> int:
        return self.app.open_presentations

    def Open(self, path: str, WithWindow: bool = True) -> FakePresentation:
        self.app.open_presentations += 1
        self.app.opened.append(FakePresentation(self.app, 2))
        return self.app.opened[-1]


class FakeApp:
    def __init__(self, open_presentations: int = 0) -> None:
        self.open_presentations = open_presentations
        self.opened: list[FakePresentation] = []
        self.quit_called = False
        self.Presentations = FakePresentations(self)

    def Quit(self) -> None:
        self.quit_called = True


def _install_fake_win32com(monkeypatch, app: FakeApp, running: bool) -> None:
    client = types.ModuleType("win32com.client")
    client.Dispatch = lambda name: app

    def get_active_object(name):
        if not running:
            raise OSError("not running")
        return app

    client.GetActiveObject = get_active_object
    package = types.ModuleType("win32com")
    package.client = client
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", client)


def test_powerpoint_export_quits_only_instance_it_started(tmp_path, monkeypatch):
    app = FakeApp()
    _install_fake_win32com(monkeypatch, app, running=False)
    shots = tmp_path / "screenshots"
    shots.mkdir()

    result = rendering._render_with_powerpoint(tmp_path / "deck.pptx", shots, tmp_path)

    assert result == {1: "screenshots/slide1.png", 2: "screenshots/slide2.png"}
    assert app.opened[0].closed
    assert app.quit_called


def test_powerpoint_export_leaves_users_running_powerpoint_open(tmp_path, monkeypatch):
    app = FakeApp(open_presentations=1)  # the user's own deck
    _install_fake_win32com(monkeypatch, app, running=True)
    shots = tmp_path / "screenshots"
    shots.mkdir()

    result = rendering._render_with_powerpoint(tmp_path / "deck.pptx", shots, tmp_path)

    assert len(result) == 2
    assert app.opened[0].closed
    assert not app.quit_called
    assert app.open_presentations == 1


def test_pptx_screenshots_warn_when_no_renderer_available(tmp_path, monkeypatch):
    monkeypatch.setattr(rendering, "_render_with_powerpoint", lambda *args: {})
    monkeypatch.setattr(rendering, "find_executable", lambda names: None)

    result, warnings = rendering.render_pptx_screenshots(tmp_path / "deck.pptx", tmp_path / "shots", tmp_path)

    assert result == {}
    assert any("LibreOffice/PowerPoint not found" in warning for warning in warnings)


def test_pptx_screenshots_report_libreoffice_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(rendering, "_render_with_powerpoint", lambda *args: {})
    monkeypatch.setattr(rendering, "find_executable", lambda names: "soffice")

    def failing_run(args, cwd=None):
        raise RuntimeError("convert crashed")

    monkeypatch.setattr(rendering, "run_command", failing_run)

    result, warnings = rendering.render_pptx_screenshots(tmp_path / "deck.pptx", tmp_path / "shots", tmp_path)

    assert result == {}
    assert any("convert crashed" in warning for warning in warnings)


def test_pptx_screenshots_render_libreoffice_pdf_pages(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    monkeypatch.setattr(rendering, "_render_with_powerpoint", lambda *args: {})
    monkeypatch.setattr(rendering, "find_executable", lambda names: "soffice")
    input_path = tmp_path / "deck.pptx"

    def fake_convert(args, cwd=None):
        outdir = Path(args[args.index("--outdir") + 1])
        doc = fitz.open()
        for _ in range(3):
            doc.new_page(width=200, height=120)
        doc.save(outdir / f"{input_path.stem}.pdf")
        doc.close()

    monkeypatch.setattr(rendering, "run_command", fake_convert)

    result, warnings = rendering.render_pptx_screenshots(input_path, tmp_path / "shots", tmp_path)

    assert warnings == []
    assert result == {index: f"shots/slide{index}.png" for index in (1, 2, 3)}
    assert all((tmp_path / "shots" / f"slide{index}.png").exists() for index in (1, 2, 3))


def test_pptx_screenshots_warn_when_libreoffice_produces_no_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(rendering, "_render_with_powerpoint", lambda *args: {})
    monkeypatch.setattr(rendering, "find_executable", lambda names: "soffice")
    monkeypatch.setattr(rendering, "run_command", lambda args, cwd=None: None)

    result, warnings = rendering.render_pptx_screenshots(tmp_path / "deck.pptx", tmp_path / "shots", tmp_path)

    assert result == {}
    assert any("did not produce a PDF" in warning for warning in warnings)
