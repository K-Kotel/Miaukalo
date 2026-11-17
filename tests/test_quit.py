"""Выход из программы «как в собранном .exe» (stdout/stderr = None): не должно быть ошибок. Запуск: python tests/test_quit.py"""
import sys, os, io
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
# имитируем собранный .exe без консоли: PyInstaller (windowed) ставит stdout/stderr = None
sys.stdout = None
sys.stderr = None
import faulthandler; faulthandler.dump_traceback_later(25, exit=True, file=sys.__stdout__)
import main as M
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
def later():
    app = QApplication.instance()
    win = [w for w in app.topLevelWidgets() if w.__class__.__name__ == 'MainWindow'][0]
    m = app.activeModalWidget()
    sys.__stdout__.write(f'modal: {m}\n')
    if m: m.accept()
    win.tabs.setCurrentWidget(win.routing)   # коммутация: запускает сканер источников
    win.toggle_quick()
    sys.__stdout__.write('calling quit\n'); QTimer.singleShot(800, win.quit)   # «Выход» из трея
import app.ui.main_window as MW
_orig = MW.MainWindow.__init__
def _init(self, *a, **k):
    _orig(self, *a, **k)
    QTimer.singleShot(4000, later)
MW.MainWindow.__init__ = _init
try:
    code = M.main()
    sys.__stdout__.write(f"main returned {code}\n")
    exec(compile("sys.stdout.flush()", "main-tail", "exec"))
    if code:
        raise SystemExit(code)
except BaseException as e:
    sys.__stdout__.write(f"EXCEPTION AFTER MAIN: {e!r}\n")
    raise
