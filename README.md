# Radar Sassello

Mappa delle precipitazioni entro **10 km da Sassello** (44.47917 N, 8.48736 E), basata sul radar della Protezione Civile. La raccolta dell’acqua gira su **GitHub Actions ogni 5 minuti**, anche con telefono e computer spenti.

## Consultazione

Il sito è predisposto per [GitHub Pages](https://giambattistag-cloud.github.io/Radar-sassello/). Questo indirizzo diventa operativo dopo l'attivazione di Pages e una pubblicazione riuscita.

1. Aprire [Settings → Pages](https://github.com/giambattistag-cloud/Radar-sassello/settings/pages).
2. In **Build and deployment → Source**, scegliere **GitHub Actions**.
3. Aprire [Actions → Radar Sassello](https://github.com/giambattistag-cloud/Radar-sassello/actions/workflows/radar.yml) e scegliere **Run workflow**, oppure attendere il successivo aggiornamento orario.

GitHub Pages su repository privati richiede un piano compatibile, ad esempio GitHub Pro. Con GitHub Free, Pages è disponibile per repository pubblici. **Il repository non viene reso pubblico da questo progetto.** La raccolta e l'archivio funzionano indipendentemente da Pages, entro i limiti Actions del proprio piano. Ogni esecuzione conserva anche un pacchetto `radar-sassello-mappa` scaricabile dalla pagina della run.

La copia privata ospitata in ChatGPT prova a leggere i dati dal sito Pages; finché Pages non è disponibile mostra i dati inclusi all'ultima pubblicazione, segnalandoli come copia salvata. Non aggiorna da sola il proprio archivio.

## Mappa

- **Colore:** scala fissa 0, 10, 25, 50, 100+ mm, dal giallo al blu scuro.
- **Età:** media pesata per i millimetri, riferita al centro degli intervalli orari. Trasparenza 90% per pioggia appena caduta, decrescente fino al 5% a 8 giorni; resta al 5% fino a 10 giorni. L'età non è una previsione della nascita dei porcini.
- **Periodo:** 14 giorni selezionabili, con gli ultimi 7 accesi all’apertura. La sommatoria è una vista separata richiamabile con un pulsante.
- **Qualità:** celle grigie quando manca oltre il 10% delle ore. Nella scheda il totale è dichiarato parziale e la copertura è espressa in ore; zero osservato e dato assente restano distinti.
- **Dettaglio:** toccare una cella per coordinate, cumulata, età media, ultima ora con almeno 1 mm, temperatura e grafici giornalieri. Esportazione CSV con campi vuoti nelle ore mancanti.
- **Storico:** dati orari conservati in `archive/`; campioni SRI a cinque minuti conservati separatamente. La mappa operativa conserva fino a 14 giorni e lo storico può arrivare a 40 giorni.

## Raccolta e accuratezza

Il prodotto **SRT1** è la pioggia cumulata nell'ora precedente, aggiornato ogni 5 minuti. Il programma conserva una sola misura per ciascuna ora per costruire i totali senza doppio conteggio. Il prodotto **SRI** è l’intensità in mm/h aggiornata ogni 5 minuti: viene conservato separatamente per il grafico dei rovesci e non viene sommato a SRT1. Recupera le ore mancanti ancora disponibili; le ore non recuperabili restano mancanti.

La griglia mantiene i pixel radar nativi da circa **1 km** entro il raggio di 10 km, senza inventare dettaglio a 100 m. Dati negativi, non finiti o fuori dall'intervallo 0–500 vengono marcati mancanti. La temperatura `TEMP` e il modello solare sono riportati sulle celle radar ma non acquistano una precisione territoriale maggiore.

Al primo avvio si tenta il recupero delle ultime 24 ore, non di dieci giorni già trascorsi. La finestra completa cresce con la raccolta. Ritardi o interruzioni della fonte/Actions sono visibili come buchi e indicazione di dati in ritardo. Le run sono pianificate al minuto 17; GitHub può ritardarle o saltarle sotto carico. Le pianificazioni dei repository pubblici possono essere disabilitate dopo 60 giorni senza attività. Nessuna continuità assoluta è garantita.

## Autenticazione e credenziali

- La sorgente DPC è Open Access: non richiede una chiave personale. Le richieste includono `Origin` e `Referer` documentati.
- `POST /downloadProduct` restituisce un URL S3 firmato con durata breve: viene usato subito e non registrato nei dati o nei log.
- GitHub genera `GITHUB_TOKEN` per ciascuna run. Il job di raccolta ha `contents: write` per salvare solo l'archivio; il job di pubblicazione ha `pages: write` e `id-token: write` per la pubblicazione ufficiale Pages. Nessun PAT o password nel codice o nel browser.
- La visibilità del repository e quella del sito Pages sono impostazioni distinte. Non includere dati personali nell'output pubblico.

## Sviluppo e verifiche

Richiede Python 3.12 o successivo:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/collect.py
python -m http.server 8000 --directory dist
```

`python scripts/collect.py --offline` rigenera la mappa usando l'archivio locale. Il controllo finale segnala anche se le misure sono in ritardo. La raccolta scrive un checkpoint dopo ogni ora di pioggia, prima di acquisire la temperatura. Il workflow conserva i checkpoint anche quando la fonte non risponde.

## Fonti e licenza dati

- [DPC: prodotti, caratteristiche e licenza](https://dpc-radar.readthedocs.io/it/latest/)
- [DPC: API aggiornate](https://dpc-radar.readthedocs.io/it/latest/api.html)
- [DPC: metadati e proiezioni GeoTIFF](https://dpc-radar.readthedocs.io/it/latest/clientwss.html)
- [GitHub Pages: disponibilità per piano](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [GitHub Actions: eventi e pianificazioni](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

Dati e mappe derivate: **Radar-DPC — Dipartimento della Protezione Civile, CC BY-SA 4.0**. Cartografia © OpenStreetMap contributors. Leaflet 1.9.4: BSD-2-Clause, licenza in `dist/vendor/LICENSE`.
