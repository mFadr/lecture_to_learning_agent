# Lecture-to-Learning Agent

Lokální desktopová aplikace pro Windows, která z přednáškových PDF vytváří studijní podklady:
strukturované shrnutí, pojmy a definice, flashcards, procvičovací testy, tematickou mapu a hlavně
**vizualizace konkrétních příkladů, které jsou v přednášce skutečně uvedeny**. Každý podstatný výstup
odkazuje na stránku původního PDF a dá se jedním kliknutím zobrazit.

- Lokální režim (Ollama) – nic neopouští počítač, po instalaci funguje bez internetu.
- Cloudový režim (OpenAI nebo jiné OpenAI-kompatibilní API) – volitelně, se souhlasem.
- Hybridní režim – PDF se zpracuje lokálně a do cloudu jdou jen vybrané kroky.

---

## 1. Instalace ve Windows 10/11

### 1.1 Python
1. Stáhněte **Python 3.12 nebo 3.13 (64-bit)** z <https://www.python.org/downloads/windows/>.
2. V instalátoru zaškrtněte **„Add python.exe to PATH“** a dokončete instalaci.

### 1.2 Aplikace
1. Rozbalte/zkopírujte složku projektu (cesta může obsahovat mezery i diakritiku).
2. Spusťte **`install.bat`** – vytvoří virtuální prostředí `.venv` a nainstaluje závislosti (několik set MB, hlavně Qt).
3. Aplikaci spouštějte souborem **`run.bat`**.

Ručně (PowerShell):
```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m lecture_to_learning
```


### 1.3 Lokální jazykový model (Ollama)
1. Nainstalujte Ollamu: <https://ollama.com/download> (běží jako služba na `http://localhost:11434`).
2. Stáhněte model, např.:
   ```powershell
   ollama pull llama3.1:8b      # kvalitnější, orientačně ~8 GB RAM, bez GPU pomalejší
   ollama pull qwen2.5:3b       # menší a rychlejší, méně přesný
   ```
3. V aplikaci: **Nastavení → AI model** → zadejte identifikátor modelu → **Ověřit dostupnost a načíst modely**.

Názvy modelů se mění – aplikace žádný nepředpokládá jako trvale platný, vždy ověřuje, co je v Ollamě stažené.
Doporučení: model s dobrou podporou češtiny a JSON výstupu. Pokud je model pomalý nebo nestíhá kontext,
snižte *Max. velikost textu v požadavku* (např. 5000 znaků) nebo použijte předvolbu **Rychlost**.
Aplikace sama o sobě GPU nepotřebuje; nároky určuje zvolený model. Časy zpracování negarantujeme –
závisí na hardwaru a modelu.

### 1.4 Cloudové API (volitelné)
1. **Nastavení → Cloudové API**: adresa API (výchozí `https://api.openai.com/v1`, lze použít libovolnou
   OpenAI-kompatibilní službu), identifikátor modelu, limit počtu volání na běh.
2. API klíč zadejte jedním ze způsobů (klíč se nikdy neukládá do nastavení ani do logů):
   - uložit v aplikaci do **Správce pověření Windows** (doporučeno), nebo
   - proměnná prostředí `L2L_API_KEY` (případně `OPENAI_API_KEY`) – viz `.env.example`.
3. Přepněte režim na **Cloud** nebo **Hybridní** a potvrďte souhlas s odesíláním dat.

Do cloudu se odesílají jen extrahované úseky textu potřebné pro daný krok (rozdělené podle limitu požadavku),
nikdy celé soubory. Rozsah lze dále omezit výběrem stránek dokumentu (pravé tlačítko na dokumentu →
*Rozsah stránek*). Volání API je zpoplatněno poskytovatelem; limit volání na běh chrání před nečekanými náklady.

### 1.5 OCR skenovaných PDF (volitelné)
Nainstalujte Tesseract (<https://github.com/UB-Mannheim/tesseract/wiki>, zvolte jazyk *Czech*) a v
**Nastavení → OCR** zapněte OCR. Bez OCR aplikace skenované stránky označí jako „bez textu“ a nic si nevymýšlí.

---

## 2. Použití

1. **➕ Nový projekt** (např. název předmětu).
2. **📄 Přidat PDF** nebo soubory přetáhněte do okna. Zobrazí se název, počet stran a stav.
3. **▶ Spustit analýzu** – vyberte kroky (lze spustit jen některé), úroveň detailu shrnutí, počty kartiček a otázek.
   Zpracování běží na pozadí, lze ho **zrušit**; hotové části se ukládají do mezipaměti, takže opakovaný běh
   pokračuje tam, kde skončil, a nevolá model znovu.
4. Výsledky v záložkách:
   | Záložka | Obsah |
   |---|---|
   | Přehled | stav kroků, výsledky kontroly kvality, upozornění |
   | Dokument | text po stránkách, problémy s extrakcí, témata, fulltextové hledání |
   | Shrnutí | kapitoly podle struktury přednášky; čísla stran jsou odkazy na zdroj; doplňky modelu jsou označené |
   | Pojmy a definice | definice s ověřenou doslovnou citací / parafrází, vzorce a proměnné, témata společná více PDF |
   | Flashcards | upravitelná tabulka + režim učení |
   | Test | interaktivní procvičování s vyhodnocením, vysvětlením a zdrojem |
   | Tematická mapa | myšlenková mapa témat a mapa vztahů mezi pojmy |
   | Příklady | katalog konkrétních příkladů z PDF – výběr k vizualizaci, změna typu |
   | Galerie vizualizací | filtrování dle tématu/typu/dokumentu, zvětšení, zdrojová stránka, nová verze, export PNG/SVG |
5. **💾 Exportovat** – Markdown, HTML, DOCX, CSV, Anki (TSV), JSON, PNG, SVG, Mermaid (`.mmd`).

**Import kartiček do Anki:** Soubor → Importovat → `flashcards_anki.txt` (oddělovač tabulátor, 1. pole =
přední strana, 2. = zadní, 3. = štítky; hlavička souboru to Anki sdělí automaticky).

### Příkazová řádka (bez GUI)
```powershell
.venv\Scripts\python -m lecture_to_learning.cli check
.venv\Scripts\python -m lecture_to_learning.cli process prednaska.pdf --project "Ekonomie" --export C:\vystup
```

### Ukázkové PDF
`samples\ukazkova_prednaska.pdf` (3 strany, nabídka a poptávka) lze znovu vytvořit:
`.venv\Scripts\python -m lecture_to_learning.sample_pdf samples\ukazkova_prednaska.pdf`.

---

## 3. Architektura

```
lecture_to_learning/
├── __main__.py            spuštění GUI (python -m lecture_to_learning)
├── cli.py                 příkazová řádka
├── config.py              nastavení (JSON v %LOCALAPPDATA%\LectureToLearning\settings.json)
├── models.py              Pydantic modely: dokument, stránka, téma, pojem, příklad, vizualizace, otázka…
├── security.py            validace cest, bezpečné názvy souborů, ošetření textu z PDF
├── logging_setup.py       logování s maskováním API klíčů
├── storage.py             projekty, extrakce, cache, výsledky; SQLite index + FTS5 fulltext
├── sample_pdf.py          generátor ukázkové přednášky
├── pdf/extractor.py       PDF Processing Engine (PyMuPDF): text po stránkách, nadpisy, odrážky,
│                          vzorce, tabulky, detekce skenů, volitelné OCR, náhled stránky
├── llm/                   LLM Provider Layer
│   ├── base.py            abstraktní rozhraní + typované chyby (nedostupnost, auth, limit, timeout…)
│   ├── ollama.py          lokální Ollama (streaming, zrušení, num_ctx, JSON režim s fallbackem)
│   ├── openai_compat.py   OpenAI-kompatibilní API (SSE, adaptace nepodporovaných parametrů)
│   ├── http_util.py       opakování s exponenciálním čekáním (429/5xx/síť), překlad chyb
│   ├── json_utils.py      robustní parsování JSON + validace + oprava odpovědi modelem
│   ├── factory.py         výběr poskytovatele dle režimu a kroku, limit volání cloudu
│   └── secrets.py         API klíč z proměnné prostředí / Správce pověření Windows
├── agent/                 Lecture Understanding Engine + Agent Orchestrator
│   ├── orchestrator.py    workflow, cache, částečné výsledky, validace, katalog příkladů, vizualizace
│   ├── chunking.py        dělení dokumentu podle limitu modelu se značkami stránek
│   ├── grounding.py       ověřování stránek, doslovných citací a čísel proti textu PDF
│   ├── prompts.py         prompty (text PDF vždy jen jako ohraničená data)
│   └── schemas.py         tolerantní schémata odpovědí LLM
├── viz/                   vizualizace z kontrolovaných šablon
│   ├── svg.py             vývojový diagram, stavy, kauzální/konceptuální diagram, časová osa,
│   │                      strom, myšlenková mapa, srovnávací matice/tabulka, Vennův diagram
│   ├── charts.py          Matplotlib: sloupcový, spojnicový, bodový graf, histogram, graf funkce
│   ├── safe_math.py       bezpečné vyhodnocení výrazu (AST whitelist, žádné eval)
│   ├── validate.py        kontrola dat (uzly, osy, popisky, čísla ze zdroje, falešná přesnost)
│   ├── mermaid.py         editovatelný export do Mermaid
│   └── render.py          zápis SVG/PNG, převod SVG→PNG (QtSvg)
├── export/exporters.py    MD, HTML (CSP, escapování), DOCX, CSV, Anki TSV, JSON, PNG/SVG
└── ui/                    PySide6: hlavní okno, záložky, dialogy, pracovní vlákno
tests/                     pytest (syntetická PDF, testovací LLM dvojník, mock HTTP)
```

### Workflow agenta
`extract → analyze (+ sjednocení, katalog příkladů) → summary → flashcards → quiz → visualize → topic_map → validate`

1. **Extrakce** po stránkách (číslo stránky, nadpisy, odrážky, vzorce, tabulky); kontrola kvality textu;
   výsledky se cachují podle SHA-256 obsahu souboru, takže stejný soubor se nezpracovává znovu.
2. **Analýza** po částech v limitu modelu; model dostává seznam již nalezených témat (kontext napříč částmi).
   Každá položka se ověří: stránky musí existovat v dané části, doslovné citace se dohledají v textu
   (ověřená citace vs. parafráze), nenalezené stránky se nedoplňují odhadem.
3. **Sjednocení** – deduplikace témat/pojmů/vzorců/příkladů, spojení stránek, témata společná více PDF.
4. **Výstupy** – shrnutí po kapitolách (body mají původ: *zdroj* / *odvozeno* / *externí vysvětlení modelu*),
   kartičky a otázky ze strukturovaných ověřených položek (správná odpověď MC musí být mezi možnostmi,
   možnosti se míchají, neplatné otázky se vyřadí).
5. **Vizualizace** – model vrací jen *data* (uzly, hrany, řady…), nikdy kód. Data se validují; čísla v grafech
   a tabulkách musí být ve zdrojových stránkách, jinak je vizualizace odmítnuta (nebo musí být výslovně
   označena jako modelové hodnoty). Při chybě validace následuje jeden opravný pokus, pak je příklad
   označen jako neúspěšný a lze zvolit jiný typ vizualizace.
6. **Kontrola kvality** – chybějící/neexistující stránky, prázdná shrnutí, poměr ověřených citací,
   doplňky modelu, neplatné vizualizace.

Každý krok lze spustit samostatně; selhání jednoho kroku nezastaví nezávislé kroky a výsledek se nikdy
nevydává za kompletní (stav *částečně* / *selhalo* v Přehledu).

### Data projektu
`%LOCALAPPDATA%\LectureToLearning\`
```
settings.json, app.db (index projektů + fulltext), logs\app.log
projects\<project_id>\
    project.json        metadata a seznam PDF (originály se nikdy nemažou ani nepřesouvají)
    source\             volitelná kopie PDF (Nastavení → Data)
    extracted\          extrahovaný text a metadata po stránkách (JSON)
    study_materials\    results.json – všechny výstupy a stav kroků
    visualizations\     SVG/PNG vizualizací, tematická mapa
    cache\              mezivýsledky volání LLM
    flashcards\ quizzes\ exports\
```

---

## 4. Bezpečnost a ochrana dat
- Lokální režim neodesílá obsah dokumentů nikam; cloud jen po výslovném souhlasu, s upozorněním v UI.
- API klíče: proměnné prostředí nebo Správce pověření Windows; logy maskují klíče a hlavičky Authorization.
- Text z PDF je v promptu ohraničen jako data; model má pokyn ignorovat instrukce v dokumentu a značky
  ohraničení se z textu PDF odstraňují (ochrana proti prompt injection – viz test s ukázkovou přednáškou).
- Model ani PDF nikdy nespouští kód: vizualizace vznikají z validovaných dat pevnými šablonami, výrazy
  funkcí se vyhodnocují bezpečným AST interpretem. Veškerý text v SVG/HTML je escapován, exportované HTML
  má Content-Security-Policy bez skriptů, náhledy v aplikaci neotevírají externí odkazy.
- Zápisy jen do datové složky aplikace a do uživatelem zvolené exportní složky (validace cest, bezpečné
  názvy souborů pro Windows, ošetření kolizí a české diakritiky).

---

## 5. Testy
```powershell
.venv\Scripts\python -m pytest
```
Standardní sada nevyžaduje síť ani placené API (LLM nahrazuje testovací dvojník, HTTP protokoly Ollamy a
OpenAI se testují přes mock transport). Pokrývá: načtení PDF a číslování stran, sken bez textu, poškozené
PDF, validaci odpovědí modelu, shrnutí, kartičky a testy, identifikaci příkladu a jeho stránky, SVG a grafy
z ověřených dat, odmítnutí vymyšlených čísel, export, přepínání lokální/cloud/hybrid, nedostupný model,
souhlas s cloudem, limit volání, bezpečnost cest, maskování klíčů, uložení a znovunačtení projektu, cache.

Volitelné testy proti skutečnému modelu (viz `.env.example`):
```powershell
set L2L_RUN_OLLAMA_TESTS=1 & .venv\Scripts\python -m pytest -m ollama
set L2L_RUN_CLOUD_TESTS=1 & set L2L_API_KEY=... & .venv\Scripts\python -m pytest -m cloud
```

## 6. Samostatný .exe (volitelné)
`build_exe.bat` vytvoří `dist\LectureToLearning\LectureToLearning.exe` (PyInstaller). Modely Ollamy nejsou
součástí balíčku – instalují se zvlášť.

---

## 7. Známá omezení
- **Kvalita výstupů závisí na zvoleném modelu.** Malé lokální modely mohou vracet neúplné nebo méně přesné
  analýzy; aplikace neplatné odpovědi odmítne a opakuje, ale nemůže zaručit úplnost obsahu.
- Ověření citací a čísel je textové (normalizované porovnání). Parafráze nelze automaticky ověřit – jsou
  proto označeny jako parafráze; obsah odvozený modelem je nutné kontrolovat podle odkazované stránky.
- Rozpoznání struktury PDF je heuristické (velikost a tučnost písma); vícesloupcové sazby, složité vzorce
  (LaTeX jako obrázky) a grafy/obrázky v PDF se neanalyzují – pracuje se jen s textem.
- Detekce tabulek používá PyMuPDF `find_tables` a nemusí zachytit všechny tabulky.
- OCR vyžaduje samostatně nainstalovaný Tesseract; kvalita OCR závisí na skenu.
- Prostorové/fyzikální situace se zobrazují jako konceptuální diagram (šablona pro silové diagramy a
  geometrii zatím není). Rozložení velkých diagramů (> ~25 uzlů) je funkční, ale méně přehledné.
- Mermaid se exportuje jako text (`.mmd`), v aplikaci se nevykresluje.
- Úpravy v UI: kartičky lze upravovat, přidávat a mazat; shrnutí a pojmy se upravují v exportovaném
  Markdown/DOCX. Opětovné spuštění kroku jeho výstupy přepíše.
- Při rozdílných definicích téhož pojmu ve více PDF se definice zobrazí zvlášť a téma se označí jako
  společné; sémantické rozpory se automaticky nevyhodnocují.
- Hybridní režim volí poskytovatele po celých krocích (např. shrnutí v cloudu, analýza lokálně).
- Export do PDF není implementován (lze vytisknout HTML do PDF z prohlížeče).

## 8. Řešení problémů
| Problém | Řešení |
|---|---|
| „Ollama neběží nebo není nainstalovaná“ | spusťte Ollamu (ikona v oznamovací oblasti) nebo `ollama serve` |
| „Model … není stažený“ | `ollama pull <model>` nebo vyberte stažený model v Nastavení |
| Časový limit | menší model, menší *Max. velikost textu v požadavku*, delší časový limit |
| „Stránka nemá textovou vrstvu“ | sken – nainstalujte Tesseract a zapněte OCR |
| HTTP 401 / 429 v cloudu | zkontrolujte API klíč / kredit a limity poskytovatele |
| Log aplikace | `%LOCALAPPDATA%\LectureToLearning\logs\app.log` |


*Built by Matěj Fadrhons  [github.com/mFadr](https://github.com/mFadr)*
