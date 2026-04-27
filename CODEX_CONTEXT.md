# CODEX CONTEXT

Ovo je elektro algoritam za:

1. citanje podloge (`DWG/DXF`)
2. citanje projektnog zadatka i uslova (`PDF/TXT`)
3. nalazenje anchor tacaka
4. generisanje trasa
5. crtanje izlaznog `DXF/DWG`

## Glavni fajlovi

- `build_project.py` - CLI ulaz
- `gui_app.py` - GUI
- `run_gui.ps1` - pokretanje GUI-ja
- `electro_alg/` - glavno jezgro algoritma

Najvazniji moduli:

- `electro_alg/task_parser.py`
- `electro_alg/pipeline.py`
- `electro_alg/dxf_ops.py`
- `electro_alg/models.py`
- `electro_alg/run_state.py`

## Pravila rada

- Uvek koristi `full_latest` algoritam.
- Za novi projekat koristi puni tok, ne parcijalni ili dijagnosticki fallback, osim ako je to eksplicitno trazeno.
- Projektni zadatak i uslovi su autoritet.
- Ono sto pise u uslovima za tacke i trase tretira se kao `must`.
- `k.p.` je `must`.
- Ako parcelni uslov nije geometrijski potvrden, treba da postoji warning.

## Tacke

- Postojece tacke su `fixed_existing`.
- Nove projektovane tranzitne tacke su `adjustable_projected`.
- Funkcionalni cilj i fizicka tacka nisu uvek isto:
  - primer: `PTS/TS/MBTS` moze biti funkcionalni cilj
  - fizicka tacka moze biti `UZB stub`, spoljasnji prikljucak, nova sahta

## Trase

- Trase se dobijaju iz uslova i to je `must`.
- Mora da se cita:
  - pocetna tacka
  - krajnja tacka
  - koridor
  - redosled parcela
  - prelaz podzemno/nadzemno
- Ako `must` koridor nije nadjen, algoritam treba da stane ili izbaci warning, a ne da crta proizvoljan fallback.

## OCR i citanje inputa

- Za novi input je vaznije da sve bude procitano i razumljivo nego da bude brzo.
- Cache je dozvoljen za ponovna citanja istih dokumenata.
- Za novi projekat citanje mora biti temeljno.

## GUI sinhronizacija

- GUI i background run-ovi treba da dele isto stanje inputa/izlaza.
- To ide preko `last_run_inputs.json`.
- Kad se algoritam pusta u pozadini, GUI treba da se osvezi novim inputima da korisnik moze odmah da proveri.

## Kratko uputstvo za Codex na drugom uredjaju

Ako otvaras ovaj projekat prvi put, kreni od:

1. procitaj ovaj fajl
2. pogledaj `build_project.py`
3. pogledaj `gui_app.py`
4. pogledaj `electro_alg/task_parser.py`
5. pogledaj `electro_alg/pipeline.py`
6. pogledaj `electro_alg/dxf_ops.py`

Najvaznije: ne izmisljaj trasu ni tacke mimo uslova. Prvo procitaj inpute, pa tek onda generisi geometriju.
