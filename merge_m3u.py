#!/usr/bin/env python3

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SOURCES_FILE = Path("/media/kodi/m3u/sources.txt")
RULES_FILE = Path("/media/kodi/m3u/rules.json")
OUTPUT_FILE = Path("/media/kodi/m3u/merged.m3u")
DELETED_FILE = Path("/media/kodi/m3u/usuniete.m3u")

# Alternatywnie pliki w katalogu skryptu:
# SOURCES_FILE = BASE_DIR / "sources.txt"
# RULES_FILE = BASE_DIR / "rules.json"
# OUTPUT_FILE = BASE_DIR / "merged.m3u"
# DELETED_FILE = BASE_DIR / "usuniete.m3u"

TIMEOUT_SECONDS = 30


def read_source(source):
    source = source.strip()

    if source.lower().startswith(("http://", "https://")):
        request = urllib.request.Request(
            source,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; M3UMerger/2.0)"
            },
        )
        with urllib.request.urlopen(
            request, timeout=TIMEOUT_SECONDS
        ) as response:
            charset = response.headers.get_content_charset() or "utf-8-sig"
            return response.read().decode(charset, errors="replace")

    path = Path(source).expanduser()

    if not path.is_absolute():
        path = BASE_DIR / path

    return path.read_text(encoding="utf-8-sig", errors="replace")


def load_sources():
    if not SOURCES_FILE.exists():
        raise FileNotFoundError(f"Brak pliku: {SOURCES_FILE}")

    sources = []

    for raw in SOURCES_FILE.read_text(
        encoding="utf-8-sig"
    ).splitlines():
        line = raw.strip()

        if line and not line.startswith("#"):
            sources.append(line)

    if not sources:
        raise ValueError("sources.txt nie zawiera żadnych źródeł.")

    return sources


def load_rules():
    if not RULES_FILE.exists():
        return []

    data = json.loads(
        RULES_FILE.read_text(encoding="utf-8-sig")
    )

    rules = data.get("rules", [])

    if not isinstance(rules, list):
        raise ValueError('Pole "rules" musi być listą.')

    return [
        rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("enabled", True)
    ]


def get_attribute(extinf, attribute):
    pattern = (
        r'(?:^|\s)'
        + re.escape(attribute)
        + r'\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,]+))'
    )

    match = re.search(pattern, extinf, flags=re.IGNORECASE)

    if not match:
        return None

    return next(
        (value for value in match.groups() if value is not None),
        "",
    )


def set_attribute(extinf, attribute, value):
    pattern = (
        r'(?P<prefix>(?:^|\s)'
        + re.escape(attribute)
        + r'\s*=\s*)'
        + r'(?:"[^"]*"|\'[^\']*\'|[^\s,]+)'
    )

    replacement = (
        lambda match:
        match.group("prefix") + '"' + value.replace('"', r'\"') + '"'
    )

    updated, count = re.subn(
        pattern,
        replacement,
        extinf,
        count=1,
        flags=re.IGNORECASE,
    )

    if count:
        return updated

    comma = updated.find(",")

    if comma == -1:
        return updated + f' {attribute}="{value}"'

    return (
        updated[:comma]
        + f' {attribute}="{value}"'
        + updated[comma:]
    )


def get_display_name(extinf):
    comma = extinf.find(",")

    return extinf[comma + 1:].strip() if comma >= 0 else ""


def set_display_name(extinf, name):
    comma = extinf.find(",")

    if comma < 0:
        return extinf + "," + name

    return extinf[:comma + 1] + name


def mark_deleted(entry, reason):
    """Zwraca kopię wpisu z powodem usunięcia dopisanym do nazwy kanału."""
    marked = entry.copy()
    marked[0] = set_display_name(
        marked[0],
        f"{get_display_name(marked[0])} [{reason}]",
    )
    return marked


def parse_playlist(content):
    lines = (
        content.replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    preamble = []
    entries = []
    current = None

    for line in lines:
        if line.startswith("#EXTINF"):
            if current is not None:
                entries.append(current)

            current = [line]

        elif current is not None:
            current.append(line)

        elif line.strip():
            preamble.append(line)

    if current is not None:
        entries.append(current)

    for entry in entries:
        while entry and not entry[-1].strip():
            entry.pop()

    return preamble, [entry for entry in entries if entry]


def get_stream_url(entry):
    for line in entry[1:]:
        line = line.strip()

        if line and not line.startswith("#"):
            return line

    return None


def get_match_value(extinf, key):
    aliases = {
        "tvg_id": "tvg-id",
        "tvg_chno": "tvg-chno",
        "group_title": "group-title",
        "tvg_name": "tvg-name",
    }

    attribute = aliases.get(key, key)

    if attribute in (
        "tvg-id",
        "tvg-chno",
        "name",
        "group-title",
        "tvg-name",
    ):
        value = get_attribute(extinf, attribute)

        if value is not None:
            return value

    if key == "name":
        return get_display_name(extinf)

    return None


def values_match(actual, expected):
    if actual is None:
        return False

    if isinstance(expected, str) and expected.startswith("regex:"):
        return (
            re.search(
                expected[6:],
                actual,
                flags=re.IGNORECASE,
            )
            is not None
        )

    return actual == str(expected)


def rule_matches(rule, extinf):
    match = rule.get("match", {})

    if not isinstance(match, dict) or not match:
        return False

    # AND: wszystkie określone pola muszą pasować.
    for key, expected in match.items():
        if not values_match(get_match_value(extinf, key), expected):
            return False

    return True


def apply_rules(entry, rules, unify_name=False):
    extinf = entry[0]
    changed = False
    forced_chno = False

    for rule in rules:
        if not rule_matches(rule, extinf):
            continue

        action = rule.get("action", {})

        if not isinstance(action, dict):
            continue

        if action.get("delete") is True:
            return None, True, forced_chno

        if "tvg_id" in action:
            extinf = set_attribute(
                extinf,
                "tvg-id",
                str(action["tvg_id"]),
            )
            changed = True

        if "tvg_name" in action:
            extinf = set_attribute(
                extinf,
                "tvg-name",
                str(action["tvg_name"]),
            )
            changed = True

        if "tvg_chno" in action:
            extinf = set_attribute(
                extinf,
                "tvg-chno",
                str(action["tvg_chno"]),
            )
            changed = True
            forced_chno = True

        if "name" in action:
            name = str(action["name"])

            extinf = set_attribute(extinf, "name", name)
            extinf = set_display_name(extinf, name)

            changed = True

        if "group_title" in action:
            extinf = set_attribute(
                extinf,
                "group-title",
                str(action["group_title"]),
            )
            changed = True

        # Opcjonalnie ustaw nazwę po przecinku na tvg_id z pasującej reguły.
        # Celowo wykonujemy to na końcu, aby --unify_name miało pierwszeństwo
        # przed ewentualnym polem action.name.
        if unify_name and "tvg_name" in action:
            extinf = set_display_name(extinf, str(action["tvg_name"]))
            changed = True

        entry[0] = extinf
        break

    return entry, changed, forced_chno


def assign_channel_numbers(entries, forced_flags):
    # Zarezerwuj dodatnie numery wymuszone w rules.json.
    reserved = set()

    for entry, forced in zip(entries, forced_flags):
        if not forced:
            continue

        value = get_attribute(entry[0], "tvg-chno")

        if value is not None:
            try:
                number = int(value)

                if number > 0:
                    reserved.add(number)

            except ValueError:
                # Nienumeryczna wartość wymuszona pozostaje bez zmian.
                pass

    # Automatyczna numeracja według kolejności źródeł.
    next_number = 1

    for entry, forced in zip(entries, forced_flags):
        if forced:
            continue

        while next_number in reserved:
            next_number += 1

        entry[0] = set_attribute(
            entry[0],
            "tvg-chno",
            str(next_number),
        )

        next_number += 1


def main():
    parser = argparse.ArgumentParser(
        description="Łączenie list M3U z obsługą reguł i usuwaniem duplikatów."
    )
    parser.add_argument(
        "--unify_name",
        action="store_true",
        help="Ustaw nazwę kanału po przecinku na tvg_id z pasującej reguły.",
    )
    args = parser.parse_args()

    try:
        sources = load_sources()
        rules = load_rules()

    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Błąd konfiguracji: {exc}", file=sys.stderr)
        return 1

    all_entries = []
    source_stats = []
    header = "#EXTM3U"

    for source in sources:
        try:
            content = read_source(source)
            preamble, entries = parse_playlist(content)

            if not source_stats:
                header = next(
                    (
                        line
                        for line in preamble
                        if line.startswith("#EXTM3U")
                    ),
                    "#EXTM3U",
                )

            all_entries.extend(entries)
            source_stats.append((source, len(entries)))

            print(f"OK: {source} — {len(entries)} pozycji")

        except Exception as exc:
            print(
                f"OSTRZEŻENIE: {source}: {exc}",
                file=sys.stderr,
            )

    if not source_stats:
        print(
            "Nie udało się wczytać żadnej listy M3U.",
            file=sys.stderr,
        )
        return 2

    output_entries = []
    forced_flags = []
    deleted_entries = []

    deleted = 0
    changed = 0
    duplicates = 0

    seen_urls = set()

    for original_entry in all_entries:
        updated, was_changed, forced_chno = apply_rules(
            original_entry.copy(),
            rules,
            unify_name=args.unify_name,
        )

        if updated is None:
            deleted += 1
            deleted_entries.append(mark_deleted(original_entry, "REGUŁA"))
            continue

        if was_changed:
            changed += 1

        stream_url = get_stream_url(updated)

        # Porównanie dokładnego tekstu URL bez normalizacji.
        if stream_url:
            if stream_url in seen_urls:
                duplicates += 1
                deleted_entries.append(mark_deleted(updated, "DUPLIKAT"))
                continue

            seen_urls.add(stream_url)

        output_entries.append(updated)
        forced_flags.append(forced_chno)

    # Numerowanie po regułach i usunięciu duplikatów.
    assign_channel_numbers(output_entries, forced_flags)

    output_lines = [header]

    for entry in output_entries:
        output_lines.extend(entry)

    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

        OUTPUT_FILE.write_text(
            "\n".join(output_lines).rstrip() + "\n\n",
            encoding="utf-8",
        )

    except OSError as exc:
        print(
            f"Błąd zapisu pliku {OUTPUT_FILE}: {exc}",
            file=sys.stderr,
        )
        return 3

    # Zapis pliku z usuniętymi pozycjami
    try:
        if deleted_entries:
            deleted_lines = [header]
            for entry in deleted_entries:
                deleted_lines.extend(entry)

            DELETED_FILE.write_text(
                "\n".join(deleted_lines).rstrip() + "\n\n",
                encoding="utf-8",
            )
        elif DELETED_FILE.exists():
            DELETED_FILE.unlink()

    except OSError as exc:
        print(
            f"Błąd zapisu pliku {DELETED_FILE}: {exc}",
            file=sys.stderr,
        )

    print("\nPodsumowanie:")
    print(f"  Źródła:             {len(source_stats)}")
    print(f"  Pozycje wejściowe:  {sum(n for _, n in source_stats)}")
    print(f"  Zmieniono regułami: {changed}")
    print(f"  Usunięto regułami:  {deleted}")
    print(f"  Duplikaty URL:      {duplicates}")
    print(f"  Pozycje wynikowe:   {len(output_entries)}")
    print(f"  Plik główny:        {OUTPUT_FILE}")
    print(f"  Plik usuniętych:    {DELETED_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
