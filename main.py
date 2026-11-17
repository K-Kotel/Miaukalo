"""Мяукало (Miaukalo) — точка входа."""
from __future__ import annotations

import sys
import traceback


STATE = {"quitting": False}


def _fix_std_streams():
    """В собранном .exe без консоли sys.stdout/sys.stderr = None — любой print или flush падает.
    Подменяем их «пустышками», чтобы ни одна библиотека не уронила программу (в т.ч. при выходе)."""
    import os
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main():
    _fix_std_streams()
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    app.setApplicationName("Miaukalo")
    app.setApplicationDisplayName("Мяукало")
    app.setQuitOnLastWindowClosed(False)

    from app.model import data_dir, load_project, resource_path, save_project
    from app.ui import theme

    theme.load_fonts()
    app.setStyleSheet(theme.qss())
    icon = resource_path("assets/icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    log_path = data_dir() / "error.log"

    def log_error(text):
        try:
            import datetime
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
        except Exception:
            pass

    def excepthook(et, ev, tb):
        log_error("".join(traceback.format_exception(et, ev, tb)))
        if STATE["quitting"] or QApplication.instance() is None or QApplication.closingDown():
            return                      # при выходе только пишем в журнал — без всплывающих окон
        QMessageBox.critical(None, "Ошибка", f"Что-то пошло не так:\n\n{ev}\n\nПодробности в {log_path}")

    def thread_hook(args):
        log_error(f"Поток {getattr(args.thread, 'name', '?')}: " +
                  "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    import threading
    threading.excepthook = thread_hook
    try:                                 # падения на уровне C (драйверы, Qt) тоже попадут в журнал
        import faulthandler
        main._fault_file = open(data_dir() / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(main._fault_file)
    except Exception:
        pass

    # защита от второго экземпляра (две копии = двойные срабатывания клавиш)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer("miaukalo-single")
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer("miaukalo-single")
    server = QLocalServer()
    server.listen("miaukalo-single")

    # заставка (не показываем при автозапуске в трей)
    start_hidden = "--hidden" in sys.argv
    splash = None
    if not start_hidden:
        from app.ui.splash import Splash
        splash = Splash()
        splash.start()
        app.processEvents()

    from app.audio.engine import Engine
    from app.defaults import default_project
    from app.input.router import InputRouter
    from app.ui.context import Ctx
    from app.ui.main_window import MainWindow

    project = load_project()
    first_run = project is None
    if first_run:
        project = default_project()
        save_project(project)

    engine = Engine()
    router = InputRouter(engine)
    ctx = Ctx(project, engine, router)
    for p in project.pads:
        if p.kind == "file":
            engine.cache.request(p.file)
    engine.start(project)
    from app.audio.tabaudio import HUB
    HUB.start()                          # приём звука вкладок от расширения браузера (только 127.0.0.1)
    try:
        from app.extension import install_extension_copy
        install_extension_copy()
    except Exception:
        log_error("Копия расширения: " + traceback.format_exc())
    router.set_project(project)
    router.start_keyboard()
    router.start_midi()

    win = MainWindow(ctx)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win.show_normal()))

    def show_main():
        win.show()
        win.raise_()
        win.activateWindow()

    if splash is not None:
        splash.finish(show_main)
    elif not start_hidden:
        show_main()

    app.aboutToQuit.connect(lambda: STATE.__setitem__("quitting", True))
    code = app.exec()
    STATE["quitting"] = True
    for step in (ctx.flush, router.stop, win.shutdown, HUB.stop, engine.shutdown):
        try:
            step()
        except Exception:
            log_error("При выходе: " + traceback.format_exc())
    return code


if __name__ == "__main__":
    import os
    try:
        code = main()
    except Exception:
        code = 1
    try:
        if sys.stdout:
            sys.stdout.flush()
    except Exception:
        pass
    os._exit(code or 0)  # не ждём фоновые потоки (хук клавиатуры, MIDI, захват звука)
