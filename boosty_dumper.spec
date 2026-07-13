# -*- mode: python ; coding: utf-8 -*-
"""Spec-файл PyInstaller для сборки одиночного BoostyDumper.exe.

Сборка::

    pyinstaller boosty_dumper.spec --noconfirm

Размер .exe сильно зависит от того, сколько Qt-модулей попадёт в bundle.
PySide6 по умолчанию тянет всю экосистему (WebEngine = Chromium ~200 МБ,
QML, Quick3D, Multimedia, PDF, Charts...), хотя приложение использует только
Core/Gui/Widgets. Поэтому здесь:

* явно вырезаются PySide6-пакеты ненужных модулей через ``excludes``;
* ``collect_data_files`` ограничивается только тем, что нужно GUI (стиль),
  а не всеми ресурсами/переводами Qt;
* тяжёлые DLL отсекаются через фильтр ``binaries`` (WebEngine, Quick3D и т. п.).

Дополнительно: если рядом лежит ``tools/upx.exe``, PyInstaller сожмёт бинарники
(даёт ещё ~×1.5). UPX не обязателен и скачивается отдельно.
"""

from PyInstaller.utils.hooks import collect_submodules

datas = [
    # Тёмная тема — единственный ресурс GUI.
    ("boosty_dumper/gui/style.qss", "boosty_dumper/gui"),
]

# --- Модули, которые тащит PySide6, но которые мы НЕ используем. ---
# Каждый такой exclude убирает и соответствующие DLL (Qt6Quick.dll и т. п.),
# и Python-биндинги (PySide6.QtQml и т. п.).
_excluded_qt_modules = [
    # WebEngine = встроенный Chromium (~200 МБ). Главный виновник размера.
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    # QML / Quick / 3D — declarative-движок, не нужен для виджетов.
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickTest",
    "PySide6.QtQmlWorkerScript",
    "PySide6.QtShaderTools",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    # Медиа/3D/датавиз — не нужны.
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDataVisualizationQml",
    "PySide6.QtChartsQml",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects",
    "PySide6.QtNetworkAuth",
    "PySide6.QtOpcUa",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtConcurrent",
    "PySide6.QtAxContainer",
]

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

# Поддиректории PySide6, которые не нужно нести в bundle (qml/, translations/,
# resources/, plugins помимо базовых). Большая часть веса — именно они.
_excluded_pyside_subpkgs = [
    "PySide6.qml",
    "PySide6.translations",
    "PySide6.resources",
    "PySide6.metatypes",
    "PySide6.examples",
    "PySide6.plugins",
]

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Стандартная «обрезка”.
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "pytest",
        "IPython",
        "notebook",
        "jupyter",
        "unittest",
        "pydoc",
        "pdb",
        # Все неиспользуемые Qt-модули.
        *_excluded_qt_modules,
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Дополнительно выкидываем тяжёлые Qt-DLL и плагины, которые могли проскочить
# через hook PySide6, даже если Python-биндинг исключён. Ориентируемся по именам.
_strip_dll_prefixes = (
    "Qt6WebEngine", "Qt6Pdf", "Qt6Quick", "Qt6Qml", "Qt6Designer", "Qt6Charts",
    "Qt6DataVisualization", "Qt63D", "Qt6Multimedia", "Qt6SpatialAudio",
    "Qt6ShaderTools", "Qt6Quick3D", "Qt6Location", "Qt6Sensors", "Qt6SerialBus",
    "Qt6SerialPort", "Qt6Scxml", "Qt6StateMachine", "Qt6Bluetooth", "Qt6Nfc",
    "Qt6Positioning", "Qt6RemoteObjects", "Qt6NetworkAuth", "Qt6OpcUa",
    "Qt6PrintSupport", "Qt6Svg", "Qt6Help", "Qt6UiTools", "Qt6Test",
    "Qt6OpenGL",
)
_strip_exact_files = {
    # Software-рендер OpenGL — 20 МБ, не нужен при наличии видеокарты.
    "opengl32sw.dll",
    # Кодеки multimedia.
    "avcodec-61.dll",
    "avformat-61.dll",
    "avutil-59.dll",
    "swresample-5.dll",
    "swscale-8.dll",
}


def _should_strip(name: str) -> bool:
    base = name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
    if base in {f.lower() for f in _strip_exact_files}:
        return True
    if "/plugins/sqldrivers/" in name.replace("\\", "/").lower():
        return True
    if "/plugins/qmltooling/" in name.replace("\\", "/").lower():
        return True
    return any(base.startswith(p.lower()) for p in _strip_dll_prefixes)


a.binaries = [(name, path, kind) for (name, path, kind) in a.binaries if not _should_strip(name)]
a.datas = [
    (name, path, kind)
    for (name, path, kind) in a.datas
    # Не несём переводы/ресурсы Qt и метatypes.
    if not any(
        name.replace("\\", "/").lower().startswith(prefix.lower())
        for prefix in ("PySide6/translations", "PySide6/resources", "PySide6/metatypes")
    )
]

# --- UPX (опционально). Если рядом есть tools/upx.exe — включаем сжатие. ---
import os

_upx = os.path.join(os.path.dirname(SPECPATH), "tools", "upx.exe")
if os.path.exists(_upx):
    upx_dir = os.path.dirname(_upx)
else:
    upx_dir = None

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BoostyDumper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=bool(upx_dir),
    upx_exclude=[
        # Эти файлы UPX может сломать или они плохо сжимаются.
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
    ],
    upx_dir=upx_dir,
    runtime_tmpdir=None,
    console=False,  # --windowed: без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # положите icon.ico рядом и укажите путь при желании
)
