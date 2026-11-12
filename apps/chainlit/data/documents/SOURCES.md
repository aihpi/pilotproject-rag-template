# Example corpus — sources & licence / Beispielkorpus — Quellen & Lizenz

**EN** — This folder ships three open-access articles so the template works right
after cloning. All three are published in *Scientific Reports* under the
[Creative Commons Attribution 4.0 International Licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/),
which permits redistribution provided the authors and source are credited — that
is what this file does. The articles are **unmodified**. They are example data
only and carry no relationship to this template's authors.

**DE** — Dieser Ordner enthält drei Open-Access-Artikel, damit das Template
direkt nach dem Clonen funktioniert. Alle drei sind in *Scientific Reports* unter
der [Creative-Commons-Namensnennung-4.0-Lizenz (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/deed.de)
veröffentlicht, die die Weitergabe bei Nennung von Autor:innen und Quelle
erlaubt — genau dazu dient diese Datei. Die Artikel sind **unverändert**. Sie
dienen ausschließlich als Beispieldaten.

---

### `Kage_2018_SciReports.pdf`
- **Title:** Luminescence lifetime encoding in time-domain flow cytometry
- **Authors:** Daniel Kage, Katrin Hoffmann, Marc Wittkamp, Jens Ameskamp, Wolfgang Göhde, Ute Resch-Genger
- **Source:** *Scientific Reports* **8**, 16715 (2018)
- **DOI:** [10.1038/s41598-018-35137-5](https://doi.org/10.1038/s41598-018-35137-5)
- **Licence:** CC BY 4.0

### `Schmidt_2022_SciReports.pdf`
- **Title:** A multiparametric fluorescence assay for screening aptamer–protein interactions based on microbeads
- **Authors:** Carsten Schmidt, Anne Kammel, Julian A. Tanner, Andrew B. Kinghorn, Muhammad Moman Khan, Werner Lehmann, Marcus Menger, Uwe Schedler et al.
- **Source:** *Scientific Reports* **12**, 2961 (2022)
- **DOI:** [10.1038/s41598-022-06817-0](https://doi.org/10.1038/s41598-022-06817-0)
- **Licence:** CC BY 4.0

### `Lin_2024_SciReports.pdf`
- **Title:** Coupling a recurrent neural network to SPAD TCSPC systems for real-time fluorescence lifetime imaging
- **Authors:** Yang Lin, Paul Mos, Andrei Ardelean, Claudio Bruschini, Edoardo Charbon
- **Source:** *Scientific Reports* **14**, 3286 (2024)
- **DOI:** [10.1038/s41598-024-52966-9](https://doi.org/10.1038/s41598-024-52966-9)
- **Licence:** CC BY 4.0

---

## Replacing the corpus / Korpus austauschen

**EN** — Drop your own PDFs into this folder, pick a fresh
`vector_store.collection` in your config, then re-ingest:
`RAG_CONFIG=examples/papers/my-rag.yaml python -m kb.ingest --recreate`.
Your files stay local — `.gitignore` only whitelists the three examples above.
You may delete the examples; nothing in the code depends on them.

**DE** — Eigene PDFs einfach hier ablegen, in der Config eine neue
`vector_store.collection` wählen und neu ingesten:
`RAG_CONFIG=examples/papers/my-rag.yaml python -m kb.ingest --recreate`.
Eigene Dateien bleiben lokal — die `.gitignore` lässt nur die drei Beispiele
oben zu. Die Beispiele dürfen gelöscht werden; kein Code hängt an ihnen.
