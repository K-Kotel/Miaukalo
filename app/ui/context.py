"""Общий контекст интерфейса: проект, движок, роутер и сигналы между потоками."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from app.model import save_project


class Bridge(QObject):
    """Сигналы из фоновых потоков (горячие клавиши, аудио) в поток интерфейса."""
    captured = Signal(object)
    changed = Signal()
    error = Signal(str)
    activity = Signal(str)
    midi_ports = Signal(list)
    sample_loaded = Signal(str)
    ui_action = Signal(str)
    recorded = Signal(object)
    editor_recorded = Signal(object)
    info = Signal(str)


class Ctx:
    def __init__(self, project, engine, router):
        self.project = project
        self.engine = engine
        self.router = router
        self.bridge = Bridge()
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_now)

        engine.on_change = self.bridge.changed.emit
        engine.on_error = self.bridge.error.emit
        engine.cache.on_loaded = self.bridge.sample_loaded.emit
        engine.on_ui_action = self.bridge.ui_action.emit
        engine.on_record = self.bridge.recorded.emit
        engine.on_info = self.bridge.info.emit
        router.on_activity = self.bridge.activity.emit
        router.on_midi_ports = self.bridge.midi_ports.emit
        # громкость/эффекты, изменённые горячими клавишами, тоже надо сохранить
        self.bridge.changed.connect(self.save_later)

    def save_later(self):
        self._save_timer.start()

    def _save_now(self):
        try:
            save_project(self.project)
        except Exception as e:  # noqa: BLE001
            self.bridge.error.emit(f"Не удалось сохранить настройки: {e}")

    def apply(self, devices=True):
        """Применить изменения проекта к движку и горячим клавишам и сохранить."""
        self.project.repair()
        self.engine.apply_project(self.project)  # устройства перезапускаются только если изменились
        self.router.set_project(self.project)
        for p in self.project.pads:
            if p.kind == "file":
                self.engine.cache.request(p.file)
        self.save_later()

    def flush(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_now()
