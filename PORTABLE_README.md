# Prenosivi paket

Ovo je izdvojeni paket koda za prenos na drugi uredjaj.

## Sadrzaj

- `build_project.py` - CLI ulaz
- `gui_app.py` - GUI
- `run_gui.ps1` - pokretanje GUI-ja
- `windows_ocr.ps1` - Windows OCR helper
- `electro_alg/` - glavno jezgro algoritma
- `tessdata/` - lokalni OCR jezicki podaci
- `requirements-portable.txt` - Python paketi

## Python paketi

Instaliraj:

```powershell
pip install -r requirements-portable.txt
```

## Spoljni alati

Za pun rad na novom uredjaju i dalje su pozeljni:

- `Tesseract OCR`
- `ODA File Converter` ili drugi DWG/DXF alat za DWG izlaz

## Pokretanje

GUI:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_gui.ps1
```

CLI primer:

```powershell
python .\build_project.py design --dxf PODLOGA.dwg --project-task ZADATAK.pdf --condition-text USLOV1.pdf --output-json design.json
```

## Napomena

Ovaj paket nosi kod i lokalne OCR resurse. Ne nosi sve radne debug/generisane izlaze iz glavnog workspace-a.
