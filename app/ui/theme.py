"""Тема «Мяукало»: цифровой брутализм, янтарный люминофор, лабораторный прибор."""
from __future__ import annotations

from app.model import resource_path

# ---------------------------------------------------------------- палитра
BG = "#0a0705"          # фон / «стекло» экрана
SURFACE = "#110c07"     # панели
SURFACE2 = "#1a1209"    # поля ввода, неактивные ярлыки
SURFACE3 = "#2a1c0c"    # подсветка, наведение
BORDER = "#4a3212"      # тусклые рамки
DIM = "#6e4c1c"         # неактивные элементы
MUTED = "#b5803a"       # вторичный текст
TEXT = "#ffc87a"        # основной текст (светлый янтарь — читаемо)
ACCENT = "#ff9a1f"      # главный оранжевый
ACCENT2 = "#ffd36b"     # клавиши / акцент второго уровня
INK = "#0a0705"         # текст на оранжевом
DANGER = "#ff4b2b"
OK = "#ffd36b"
WARN = "#ffb020"

FONT = "IBM Plex Mono"
FONT_STACK = f'"{FONT}", "Consolas", "Cascadia Mono", "Lucida Console", monospace'

PAD_COLORS = ["#ff9a1f", "#ffd36b", "#ff6a2a", "#ff3b30", "#ffe3b0", "#d0843a",
              "#ffb38a", "#e0573a", "#b9e35a", "#5fd4d0", "#c99bff", "#9a8a78"]


def load_fonts():
    from PySide6.QtGui import QFontDatabase
    d = resource_path("assets/fonts")
    for f in sorted(d.glob("*.ttf")) if d.exists() else []:
        QFontDatabase.addApplicationFont(str(f))


def _img(name):
    return resource_path(f"assets/ui/{name}.png").as_posix()


def qss() -> str:
    return f"""
* {{ font-family: {FONT_STACK}; font-size: 9.5pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {ACCENT}; padding: 5px; }}

QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; padding: 5px 12px; min-height: 18px; }}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT2}; }}
QPushButton:pressed {{ background: {ACCENT}; color: {INK}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {SURFACE3}; }}
QPushButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {INK}; font-weight: 600; }}
QPushButton::menu-indicator {{ image: url({_img('down')}); subcontrol-position: right center; right: 6px; width: 9px; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: {INK}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {ACCENT2}; border-color: {ACCENT2}; color: {INK}; }}
QPushButton#danger {{ background: {DANGER}; border: 1px solid {DANGER}; color: {INK}; font-weight: 700; }}
QPushButton#danger:hover {{ background: #ff7a5c; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; padding: 3px 8px; }}
QPushButton#ghost:hover {{ color: {ACCENT2}; border-color: {BORDER}; }}
QPushButton#ghost::menu-indicator {{ image: none; width: 0; }}
QPushButton#chip {{ background: {BG}; border: 1px solid {ACCENT2}; color: {ACCENT2}; padding: 2px 8px; font-weight: 600; }}
QPushButton#chip:hover {{ background: {ACCENT2}; color: {INK}; }}
QPushButton#capture {{ background: {BG}; border: 1px dashed {ACCENT}; color: {ACCENT}; }}
QPushButton#capture:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: {INK}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {SURFACE2}; border: 1px solid {BORDER}; padding: 4px 6px; min-height: 18px;
    selection-background-color: {ACCENT}; selection-color: {INK}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {DIM}; }}
QComboBox::drop-down {{ border: none; border-left: 1px solid {BORDER}; width: 20px; }}
QComboBox::down-arrow {{ image: url({_img('down')}); width: 9px; height: 9px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {ACCENT}; outline: none;
    selection-background-color: {ACCENT}; selection-color: {INK}; padding: 0; }}
QAbstractSpinBox {{ padding-left: 6px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 16px; border: none; border-left: 1px solid {BORDER}; background: transparent; }}
QAbstractSpinBox::up-arrow {{ image: url({_img('up_dim')}); width: 8px; height: 8px; }}
QAbstractSpinBox::down-arrow {{ image: url({_img('down_dim')}); width: 8px; height: 8px; }}
QAbstractSpinBox::up-arrow:hover {{ image: url({_img('up')}); }}
QAbstractSpinBox::down-arrow:hover {{ image: url({_img('down')}); }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {MUTED}; background: {BG}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE3}; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; }}
QSlider::handle:horizontal {{ background: {ACCENT2}; width: 8px; margin: -7px 0; border: 1px solid {INK}; }}
QSlider::groove:vertical {{ width: 4px; background: {SURFACE3}; }}
QSlider::add-page:vertical {{ background: {ACCENT}; }}
QSlider::handle:vertical {{ background: {ACCENT2}; height: 8px; margin: 0 -8px; border: 1px solid {INK}; }}
QSlider::handle:hover {{ background: white; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; border-left: 1px solid {BORDER}; }}
QScrollBar::handle:vertical {{ background: {DIM}; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; border-top: 1px solid {BORDER}; }}
QScrollBar::handle:horizontal {{ background: {DIM}; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; }}
QFrame#banner {{ background: {SURFACE}; border: 1px solid {DIM}; border-left: 4px solid {ACCENT}; }}
QFrame#bannerWarn {{ background: #1c0d06; border: 1px solid {DANGER}; border-left: 4px solid {DANGER}; }}
QLabel#h1 {{ font-size: 15pt; font-weight: 700; color: {ACCENT}; }}
QLabel#h2 {{ background: {ACCENT}; color: {INK}; font-weight: 700; padding: 2px 8px; }}
QLabel#cap {{ color: {DIM}; font-size: 8pt; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#small {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#ok {{ color: {OK}; font-weight: 600; }}
QLabel#err {{ color: {DANGER}; font-weight: 600; }}
QLabel#big {{ font-size: 16pt; font-weight: 700; color: {ACCENT2}; }}

QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; gridline-color: {BORDER};
    alternate-background-color: #150f08; selection-background-color: {ACCENT}; selection-color: {INK}; }}
QHeaderView::section {{ background: {SURFACE2}; color: {MUTED}; border: none; border-bottom: 1px solid {ACCENT};
    border-right: 1px solid {BORDER}; padding: 5px 8px; font-weight: 700; }}
QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; outline: none; }}
QListWidget::item {{ padding: 6px; border-bottom: 1px solid {SURFACE3}; }}
QListWidget::item:hover {{ background: {SURFACE3}; }}
QListWidget::item:selected {{ background: {ACCENT}; color: {INK}; }}
QListWidget::indicator {{ width: 12px; height: 12px; border: 1px solid {MUTED}; background: {BG}; }}
QListWidget::indicator:checked {{ background: {ACCENT2}; border-color: {INK}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {ACCENT}; padding: 2px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {INK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 0; }}
QMenu::indicator {{ width: 10px; height: 10px; margin-left: 4px; border: 1px solid {MUTED}; }}
QMenu::indicator:checked {{ background: {ACCENT2}; }}
QMessageBox QLabel {{ color: {TEXT}; }}

/* нижняя панель «софт-клавиш» */
QWidget#softbar {{ background: {BG}; border-top: 2px solid {ACCENT}; }}
QPushButton#softkey {{ background: {BG}; border: none; border-right: 1px solid {DIM}; padding: 0 14px;
    color: {ACCENT}; font-weight: 700; min-height: 26px; }}
QPushButton#softkey:hover {{ background: {SURFACE3}; color: {ACCENT2}; }}
QPushButton#softkey:pressed, QPushButton#softkey:checked {{ background: {ACCENT}; color: {INK}; }}
QPushButton#softkeyDanger {{ background: {BG}; border: none; border-right: 1px solid {DIM}; padding: 0 14px;
    color: {DANGER}; font-weight: 700; min-height: 26px; }}
QPushButton#softkeyDanger:hover {{ background: {DANGER}; color: {INK}; }}
QLabel#softcell {{ border-left: 1px solid {DIM}; padding: 0 12px; color: {MUTED}; font-size: 8.5pt; }}
QLabel#softmsg {{ padding: 0 12px; color: {MUTED}; font-size: 8.5pt; }}
"""


QSS = ""  # совместимость: тема собирается через qss() после загрузки шрифтов
