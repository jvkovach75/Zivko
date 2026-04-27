# Electro Algorithm Base

Ovo je osnovni kostur za pravi tok rada:

1. podloga
2. projektni zadatak
3. uslovi
4. generisanje crteza
5. merenje kolicina iz crteza
6. tek onda pravljenje nacrta za Glavnu svesku

Znaci:

- Glavna sveska nije glavni ulaz
- crtez je primarni model
- kolicine se vade iz generisanog crteza

## Sta trenutno radi

- ucitava podlogu iz DXF
- cita projektni zadatak iz tekst fajla
- cita tekstove uslova
- klasifikuje layer-e
- trazi anchor tacke u modelu
- pravi interni design model
- pravi radnu DXF kopiju sa `EL_*` layer-ima
- meri aproksimativne duzine iz generisanih trasa
- pravi JSON payload za kasniji draft Glavne sveske

## Glavni fajlovi

- CLI: [build_project.py](C:/Users/Vladica/Documents/Codex/2026-04-18-cao/build_project.py)
- modeli: [electro_alg/models.py](C:/Users/Vladica/Documents/Codex/2026-04-18-cao/electro_alg/models.py)
- parser zadatka/uslova: [electro_alg/task_parser.py](C:/Users/Vladica/Documents/Codex/2026-04-18-cao/electro_alg/task_parser.py)
- CAD operacije: [electro_alg/dxf_ops.py](C:/Users/Vladica/Documents/Codex/2026-04-18-cao/electro_alg/dxf_ops.py)
- pravila: [electro_alg/rules.py](C:/Users/Vladica/Documents/Codex/2026-04-18-cao/electro_alg/rules.py)
- pipeline: [electro_alg/pipeline.py](C:/Users/Vladica/Documents/Codex/2026-04-18-cao/electro_alg/pipeline.py)

## Komande

Design model:

```powershell
& 'C:\Users\Vladica\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  .\build_project.py design `
  --dxf .\zivko_dwg_out2\zivko_situacija_podloga_clean_imported.dxf `
  --project-task .\zivko_glavna_sveska.txt `
  --condition-text .\zivko_ascii\03_gas_yugorosgaz.txt `
  --condition-text .\zivko_ascii\06_uslovi_putevi.txt `
  --output-json .\design_model.json
```

Radni crtez:

```powershell
& 'C:\Users\Vladica\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  .\build_project.py draw `
  --dxf .\zivko_dwg_out2\zivko_situacija_podloga_clean_imported.dxf `
  --project-task .\zivko_glavna_sveska.txt `
  --condition-text .\zivko_ascii\03_gas_yugorosgaz.txt `
  --condition-text .\zivko_ascii\06_uslovi_putevi.txt `
  --output-dxf .\generated_project.dxf `
  --output-json .\generated_project.json
```

Kolicine:

```powershell
& 'C:\Users\Vladica\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  .\build_project.py quantify `
  --dxf .\zivko_dwg_out2\zivko_situacija_podloga_clean_imported.dxf `
  --project-task .\zivko_glavna_sveska.txt `
  --condition-text .\zivko_ascii\03_gas_yugorosgaz.txt `
  --condition-text .\zivko_ascii\06_uslovi_putevi.txt `
  --output-json .\quantities.json
```

Draft payload za svesku:

```powershell
& 'C:\Users\Vladica\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  .\build_project.py report `
  --dxf .\zivko_dwg_out2\zivko_situacija_podloga_clean_imported.dxf `
  --project-task .\zivko_glavna_sveska.txt `
  --condition-text .\zivko_ascii\03_gas_yugorosgaz.txt `
  --condition-text .\zivko_ascii\06_uslovi_putevi.txt `
  --output-json .\report_payload.json
```

## Sledeci koraci

- precizno rutiranje trase po pravilima
- razdvajanje podzemno / nadzemno / most / ukrstanje
- automatski izbor cevi i zastite po uslovima
- tacniji predmer iz stvarne geometrije
- generisanje tehnickog opisa i tabela za Glavnu svesku
