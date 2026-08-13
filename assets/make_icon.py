"""Build the app icon from the source artwork.

    python assets/make_icon.py

Writes assets/BeatForge.ico (multi-resolution, used by the PyInstaller build)
and web/favicon.ico for the browser tab.

The source tile puts the B in the upper two thirds with a BEAT FORGE wordmark
underneath. The wordmark is illegible below about 48px and turns to mush in a
taskbar, so the icon is cropped to the monogram, which fills the frame and
still reads at 16px.
"""

import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SOURCE = os.path.join(HERE, "icon-source.png")
CROP = (160, 125, 460, 425)          # the B, wordmark excluded
SIZES = [16, 24, 32, 48, 64, 128, 256]


def build():
    src = Image.open(SOURCE).convert("RGBA")
    mark = src.crop(CROP)

    layers = []
    for size in SIZES:
        layers.append(mark.resize((size, size), Image.LANCZOS))

    ico_path = os.path.join(HERE, "BeatForge.ico")
    layers[-1].save(ico_path, format="ICO",
                    sizes=[(s, s) for s in SIZES])

    fav_path = os.path.join(ROOT, "web", "favicon.ico")
    layers[-1].save(fav_path, format="ICO",
                    sizes=[(s, s) for s in (16, 32, 48)])

    png_path = os.path.join(ROOT, "web", "icon.png")
    mark.resize((256, 256), Image.LANCZOS).save(png_path)

    for path in (ico_path, fav_path, png_path):
        print("%-28s %6.1f KB" % (os.path.relpath(path, ROOT),
                                  os.path.getsize(path) / 1024.0))


if __name__ == "__main__":
    build()
