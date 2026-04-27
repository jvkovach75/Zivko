# Corridor Routing Spec

Ovaj dokument definise kako algoritam klasifikuje geometriju podloge u:

- `allowed corridor`
- `preferred corridor`
- `forbidden zone`
- `conditional crossing`

Osnovni cilj algoritma je:

`najkraca validna ruta izmedju anchor tacaka koja postuje projektni zadatak i uslove`

## 1. Ulazi

Algoritam koristi tri izvora:

1. `projektni zadatak`
2. `uslovi`
3. `semanticka podloga (DWG/DXF)`

### 1.1 Projektni zadatak

Iz projektnog zadatka se izvodi:

- pocetna tacka izvoda
- krajnja tacka izvoda
- tip voda
- da li je vod `underground` ili `overhead`
- da li postoji prelaz `kabl -> stub -> nadzemni vod`
- osnovni tip kabla/provodnika

### 1.2 Uslovi

Iz uslova se izvode ogranicenja:

- putni koridor
- most
- gas
- vodovod
- telekom
- posebne cevi i zastite
- minimalna odstojanja
- uslovi ukrstanja i paralelnog vodjenja

### 1.3 Podloga

Iz podloge se izvode:

- geometrija koridora
- postojeci objekti i infrastruktura
- putevi, mostovi, kanali, parcele
- semanticki anchor signali

## 2. Klasifikacija geometrije

Svaka grana ili geometrijski segment u mrezi dobija jednu od sledecih klasa:

- `preferred`
- `allowed`
- `conditional`
- `forbidden`

## 3. Pravila po klasi

### 3.1 Preferred corridor

Segment je `preferred` ako predstavlja prirodan infrastrukturni koridor za vodjenje trase.

Tipicni primeri:

- putni pojas
- ivica puta
- bankina
- pesacka staza mosta
- postojeca trasa / cev / infrastrukturni pojas
- kanal uz koji se trasa logicno vodi

Efekat:

- koristi se kao prva opcija
- ima najmanji trosak po metru

### 3.2 Allowed corridor

Segment je `allowed` ako je fizicki i uslovno prihvatljiv, ali nije prioritetan kao vodjenje uz gotov koridor.

Tipicni primeri:

- otvoren teren
- prostor izmedju parcela
- geometrija koja ne krsi uslove, ali ne prati logicnu infrastrukturu

Efekat:

- dozvoljen za rutiranje
- veci trosak od `preferred`

### 3.3 Conditional corridor

Segment je `conditional` ako je dozvoljen samo uz dodatni uslov iz dokumentacije.

Tipicni primeri:

- prelaz preko mosta
- ukrstanje sa putem
- vođenje kroz cev
- deo trase u zoni posebne zastite

Efekat:

- moze se koristiti samo ako zadatak/uslovi dozvoljavaju taj tip prelaza
- trosak je veci od `allowed`
- pri izlazu treba generisati i napomenu o zastiti ili cevi

### 3.4 Forbidden zone

Segment ili zona je `forbidden` ako je u suprotnosti sa uslovima ili predstavlja geometriju kroz koju se trasa ne sme voditi.

Tipicni primeri:

- objekat bez dozvoljenog prelaza
- vodotok bez propisanog prelaza
- gasna zastitna zona bez dozvoljenog ukrstanja
- most ili put ako uslovi ne dozvoljavaju dati prelaz

Efekat:

- zabranjeno za rutiranje
- segment se ne sme koristiti u grafu

## 4. Mapiranje layer-a na koridore

Ovo mapiranje je inicijalno i moze se prosirivati pravilima po projektu.

### 4.1 Putni i saobracajni slojevi

Layer paterni:

- `put`
- `kolovoz`
- `trotoar`
- `bankina`
- `osa`
- `stac`
- `stations`
- `ivica`
- `ivice`
- `rehab`

Klasifikacija:

- `preferred`

Razlog:

- najcesce predstavljaju prirodan koridor za vodjenje uz put

### 4.2 Most i mostovske geometrije

Layer paterni:

- `most`
- `pesacka staza mosta`
- `stubovi mosta`

Klasifikacija:

- podrazumevano `conditional`

Dodatno pravilo:

- prelaz preko mosta je dozvoljen samo ako uslovi predvidjaju takvo resenje
- ako je dozvoljeno, segment ostaje u mrezi sa povecanim troskom
- ako nije dozvoljeno, tretira se kao `forbidden`

### 4.3 Kanal / vodotok / hidrotehnika

Layer paterni:

- `kanal`
- `reka`
- `potok`

Klasifikacija:

- linijski koridori uz koje se moze voditi trasa: `allowed`
- direktno sečenje vodotoka bez propisanog prelaza: `forbidden`

### 4.4 Postojeca infrastruktura

Layer paterni:

- `telekom`
- `vodovod`
- `cijev`
- `tcg_trasa`
- `tcg_cijev`
- `tcg_cijev_oznaka`

Klasifikacija:

- `preferred` ili `conditional`, zavisno od uslova

Napomena:

- ovi slojevi nisu po sebi elektro koridor, ali daju signal gde vec postoji infrastrukturni pojas

### 4.5 Objekti i granice objekata

Layer paterni:

- `zgrade`
- `objekat`
- `gran_objek`
- `vis_lin1_gran_objek`

Klasifikacija:

- sam objekat: `forbidden`
- granica objekta / tacka prikljucenja: koristi se za anchor refinement, ne kao glavni koridor

### 4.6 Geodetski i katastarski slojevi

Layer paterni:

- `parcela`
- `granica`
- `broj parcele`
- `visinske`
- `padnice`
- `zemljisni_oblici`

Klasifikacija:

- ne koriste se kao direktan koridor
- koriste se kao podrska za geometriju i prostornu orijentaciju

## 5. Anchor pravila

Anchor ne treba vezivati za sam tekst ako postoji bolji lokalni objekat.

Pravilo:

1. tekstualni anchor se detektuje iz semantickog sloja
2. u lokalnom radijusu se trazi stvarni objekat/simbol
3. ako postoji blizak i smislen kandidat, anchor se premesta na njega

Tipicni lokalni slojevi za anchor refinement:

- `Mreza`
- `SAHT`
- `GRALIN-1`
- `0`
- `Vis_lin1_gran_objek`

## 6. Cost model

Svaki segment u mrezi dobija cenu:

`ukupna cena = duzina * tezina_koridora + kazne`

Gde je:

- `preferred` -> najmanja tezina
- `allowed` -> srednja tezina
- `conditional` -> veca tezina
- `forbidden` -> segment nije dozvoljen

Primer relativnih tezina:

- `preferred = 1.0`
- `allowed = 1.5`
- `conditional = 3.0`
- `forbidden = inf`

Dodatne kazne:

- mnogo lomova
- izlaz kroz los anchor pristup
- nepotrebno udaljavanje od infrastrukturnog pojasa
- prelaz preko mosta ili specijalne zone

## 7. Logika izbora rute

Algoritam radi ovim redom:

1. detektuj anchor tacke
2. refine-uj anchor na stvarni lokalni objekat gde je moguce
3. izgradi graf iz `preferred`, `allowed` i `conditional` segmenata
4. primeni zabrane iz uslova
5. za svaki izvod nadji najkraci validni put kroz graf
6. ako takav put ne postoji:
   - probaj prosireni koridor fallback
   - tek na kraju koristi prost ortogonalni fallback

## 8. Sta jos treba implementirati

Da bi ovaj model bio pun, treba jos:

- eksplicitno mapiranje layer pattern -> corridor class
- pravila za gasne zastitne zone
- pravila za ukrstanje puta
- pravila za prelaz preko mosta
- lokalni izlaz iz TS / MBTS / PTS / UZB zone
- penalizaciju za prevelik broj lomova

## 9. Trenutni status

U trenutnoj implementaciji vec postoji deo ovog modela:

- automatsko nalazenje anchor tacaka
- anchor refinement sa teksta na lokalni objekat
- corridor graph fallback iz podloge
- stitch malih geometrijskih gap-ova

Ali jos nije potpuno implementirano:

- formalno `preferred / allowed / conditional / forbidden` bodovanje po segmentu
- pun constrained shortest path nad svim uslovima
