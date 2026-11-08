"""Identidade visual do painel (cores e fontes) compartilhada pelos módulos de interface."""

from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "assets/brand"
BODY_FONT, TITLE_FONT = "Segoe UI", "Segoe UI"
BG, PANEL, BORDER, TEXT, MUTED = "#0A1628", "#10213B", "#23446D", "#FFFFFF", "#E8EDF5"
TEAL, BLUE, ORANGE, PINK = "#12A98E", "#1C64C8", "#ffbc66", "#E8EDF5"
