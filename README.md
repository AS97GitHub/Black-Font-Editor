[![lang-English 🇬🇧](https://img.shields.io/badge/lang-English%20%F0%9F%87%AC%F0%9F%87%A7-white)](README.md)
[![язык-Русский 🇷🇺](https://img.shields.io/badge/%D1%8F%D0%B7%D1%8B%D0%BA-%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9%20%F0%9F%87%B7%F0%9F%87%BA-white)](README_RU.md)

# Black Font Editor

A visual bitmap font editor for the Xbox game **BLACK**. It allows you to inspect, edit glyph metrics, and replace texture atlases for `Big.bin` and `Small.bin` files (format `4.1v`).

## Features
* **Visual Editing:** Move and resize glyph rectangles directly on the texture atlas.
* **Metrics Editing:** Edit UV coordinates, cell sizes, offsets, and advance widths (in pixels or normalized floats).
* **Texture Import/Export:** Export the atlas to PNG, or import a new one from PNG/DDS (automatically re-encoded to DXT3/BC2).
* **Safe Patching:** Preserves all unrecognised bytes, the 128-entry pointer table, and texture headers.
* **Dark Theme UI:** Built with Tkinter, featuring a modern dark interface.

## Files
* `black_font_editor.py` — the main graphical editor (Tkinter GUI).
* `black_font_format.py` — library for reading/writing the `4.1v` font format and glyph records.
* `black_texture.py` — DXT1/3/5 codecs and DDS loader used by the editor.

*All files must be located in the same folder.*

## Requirements
* Python 3.8 or later (`tkinter` is included in standard Python distributions).
* [Pillow](https://python-pillow.org/) library.

### Option 2 — Run from source

Install the required dependency:
```bash
pip install Pillow
```

Run the editor (optionally passing a font file as an argument):
```bash
python black_font_editor.py [Big.bin | Small.bin]
```

> ⚠️ On Windows, you can use either `python` or `py` to run the script, depending on your Python installation.

> ⚠️: On Linux, you may need to use `python3` instead of `python`.

## Screenshot

<p align>
  <img src="images/screenshot.png">
</p>