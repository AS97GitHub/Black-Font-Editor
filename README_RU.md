[badge-en]: https://img.shields.io/badge/lang-English%20%F0%9F%87%AC%F0%9F%87%A7-white
[badge-ru]: https://img.shields.io/badge/%D1%8F%D0%B7%D1%8B%D0%BA-%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9%20%F0%9F%87%B7%F0%9F%87%BA-white
[vot-readme-en]: README.md
[vot-readme-ru]: README_RU.md
[![en][badge-en]][vot-readme-en]
[![ru][badge-ru]][vot-readme-ru]

# Black Font Editor

### Визуальный редактор шрифтов для Black.

## Файлы

- `conker_glyph_format.py` — библиотека для чтения и записи формата шрифта и управления хеш-таблицей символов с открытой адресацией (обязательна для работы редактора)
- `conker_texture.py` — чтение и замена текстуры, встроенной в `.bin` (используется библиотекой и редактором)
- `conker_glyph_editor.py` — основной графический редактор (tkinter)

Все файлы должны находиться в одной папке.

## Скриншот

<p align>
  <img src="images/screenshot.png">
</p>

## Установка (Windows)

### Вариант 1 — Скачать готовый исполняемый файл

Скачайте последнюю версию `.exe` со страницы [Releases](https://github.com/AS97GitHub/Conker-Glyph-Editor/releases).

> Устанавливать `python` и дополнительные зависимости не требуется.

### Вариант 2 — Запуск из исходников

1. Убедитесь, что установлен Python 3.8 или новее (tkinter входит в стандартную поставку Python для Windows, отдельно устанавливать его не нужно).
2. Установите Pillow:

   ```
   pip install Pillow
   ```

## Запуск

> ⚠️ В Windows для запуска скрипта можно использовать `python` или `py` — в зависимости от того, как установлен Python.

> ⚠️ В Linux вместо `python` может потребоваться `python3`.

```bash
python conker_glyph_editor.py
```
