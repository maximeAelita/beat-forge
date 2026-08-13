# PyInstaller build spec -- see README ("Building a standalone app").
#
#   python -m PyInstaller BeatForge.spec
#
# Produces dist/BeatForge.exe: the whole studio, including a Python
# interpreter, in one file. It stays a console app on purpose -- the MCP
# server talks over stdin/stdout, and the console is where the URL is printed.

block_cipher = None

a = Analysis(
    ['beatforge.py'],
    pathex=['.'],
    binaries=[],
    datas=[('web', 'web')],          # the studio UI, read back out of _MEIPASS
    hiddenimports=['bf.midi', 'bf.paths'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc_data', 'lib2to3', 'test'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BeatForge',
    icon='assets/BeatForge.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
