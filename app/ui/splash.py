"""Заставка при запуске."""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from app.model import resource_path
from . import theme
from .widgets import ui_font


class Splash(QWidget):
    W, H = 360, 600
    MIN_MS = 1700

    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(self.W, self.H)
        self.logo = QPixmap(str(resource_path("assets/logo.png")))
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.W // 2, scr.center().y() - self.H // 2)
        self.timer = QElapsedTimer()
        self._anim = None

    def start(self):
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.timer.start()
        self._fade(0.0, 1.0, 260)

    def _fade(self, a, b, ms, done=None):
        an = QPropertyAnimation(self, b"windowOpacity", self)
        an.setDuration(ms)
        an.setStartValue(a)
        an.setEndValue(b)
        if done:
            an.finished.connect(done)
        an.start()
        self._anim = an

    def finish(self, show_next):
        """Дождаться минимального времени показа, плавно погаснуть и показать главное окно."""
        left = max(0, self.MIN_MS - self.timer.elapsed())

        def out():
            def done():
                show_next()
                self.close()
            self._fade(self.windowOpacity(), 0.0, 280, done)

        QTimer.singleShot(left, out)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(theme.BG))
        # логотип
        if not self.logo.isNull():
            lh = self.H - 120
            lw = int(self.logo.width() * lh / self.logo.height())
            if lw > self.W - 40:
                lw = self.W - 40
                lh = int(self.logo.height() * lw / self.logo.width())
            p.drawPixmap(QRect((self.W - lw) // 2, 34, lw, lh), self.logo)
        # подпись
        f = ui_font(8.5, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRect(0, self.H - 46, self.W, 20), Qt.AlignCenter, "МЯУКАЛО")
        # рамка
        p.setPen(QColor(theme.DIM))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
