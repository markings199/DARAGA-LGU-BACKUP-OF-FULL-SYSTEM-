# OCR assets

Used at runtime by the Flask app. Do not mix these folders.

| Folder | Used by | Contents |
|--------|---------|----------|
| `forms/birth`, `forms/marriage`, `forms/death` | `engine.py` | Keyword/regex JSON templates |
| `templates/` | `field_boxes.py` | Official form photos plus `*_boxes.json` write-in coordinates |
| `title_templates/` | `detect.py` | Cropped title PNGs and `source/` full-page photos for document-type matching |
| Python modules in this folder | `app.py`, `worker.py` | Deskew, PaddleOCR, birth/marriage/death extractors |
