M3U Merger v2

Funkcje:
- dowolna liczba źródeł lokalnych i HTTP(S),
- deduplikacja wyłącznie po identycznym adresie streamu,
- delete, tvg-id, name, group-title,
- dopasowanie po wielu polach jednocześnie (AND),
- regex dla pól match,
- zachowanie pozostałych danych wpisu.

Uruchomienie:
    python merge_m3u.py

Wynik:
    merged.m3u

sources.txt:
    jedno źródło w każdej linii.

rules.json:
    match:
      tvg_id      -> tvg-id
      name        -> atrybut name; gdy go nie ma, tytuł po przecinku
      group_title -> group-title
      inne nazwy  -> odpowiedni atrybut EXTINF

Kilka pól w match oznacza AND, czyli wszystkie muszą pasować.

Regex:
    "name": "regex:.*News.*"

Akcje:
    "delete": true
    "tvg_id": "nowy.id"
    "name": "Nowa nazwa"
    "group_title": "Nowa grupa"

Deduplikacja:
Porównywany jest dokładny tekst adresu streamu, bez normalizacji URL.
Deduplikacja następuje po zastosowaniu rules.json.
