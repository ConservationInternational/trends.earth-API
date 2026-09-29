#!/usr/bin/env python3
"""Prepopulate untranslated PO entries with Google Cloud Translation.

Usage:
    python scripts/machine_translate.py --target-langs es,fr,ar \
        --translations-dir gefapi/translations

Requires google-cloud-translate and polib, and GOOGLE_APPLICATION_CREDENTIALS.
Machine translations are flagged fuzzy for human review in Transifex.
"""

import argparse
import html
from pathlib import Path
import re
import sys

try:
    from google.cloud import translate_v2 as translate
    import polib
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install google-cloud-translate polib")
    sys.exit(1)

GOOGLE_LANGUAGE_CODES = {"zh": "zh-CN"}

# A mangled placeholder raises at render time, so these must survive intact.
PLACEHOLDER_RE = re.compile(r"%\([A-Za-z_]\w*\)[sd]|%[sd]|\{[A-Za-z_]\w*\}")
NO_TRANSLATE_SPAN_RE = re.compile(r'<span translate="no">(.*?)</span>', re.DOTALL)


def _protect(text):
    """HTML-escape text and wrap placeholders so Google leaves them alone."""
    parts = []
    last = 0
    for match in PLACEHOLDER_RE.finditer(text):
        parts.append(html.escape(text[last : match.start()], quote=False))
        parts.append(f'<span translate="no">{html.escape(match.group())}</span>')
        last = match.end()
    parts.append(html.escape(text[last:], quote=False))
    return "".join(parts)


def _unprotect(text):
    return html.unescape(NO_TRANSLATE_SPAN_RE.sub(r"\1", text))


def translate_text(client, text, target_lang):
    try:
        result = client.translate(
            _protect(text),
            source_language="en",
            target_language=GOOGLE_LANGUAGE_CODES.get(target_lang, target_lang),
            format_="html",
        )
    except Exception as e:  # one failed string must not stop the run
        print(f"  Warning: translation failed for {text[:50]!r}: {e}")
        return ""
    translated = _unprotect(result["translatedText"])
    if sorted(PLACEHOLDER_RE.findall(translated)) != sorted(
        PLACEHOLDER_RE.findall(text)
    ):
        print(f"  Warning: placeholders changed, skipping {text[:50]!r}")
        return ""
    return translated


def process_po_file(client, po_path, target_lang, overwrite):
    po = polib.pofile(str(po_path))
    translated = skipped = 0
    for entry in po:
        if entry.obsolete or not entry.msgid.strip():
            continue
        # Plural forms need per-language form counts; leave them to translators.
        if entry.msgid_plural:
            continue
        if entry.msgstr.strip() and not overwrite:
            skipped += 1
            continue
        translation = translate_text(client, entry.msgid, target_lang)
        if translation:
            entry.msgstr = translation
            if "fuzzy" not in entry.flags:
                entry.flags.append("fuzzy")
            translated += 1
    po.save()
    return translated, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-langs", required=True)
    parser.add_argument("--translations-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    client = translate.Client()
    for target_lang in (lang.strip() for lang in args.target_langs.split(",")):
        po_path = args.translations_dir / target_lang / "LC_MESSAGES" / "messages.po"
        if not po_path.exists():
            print(f"Skipping {target_lang}: {po_path} not found")
            continue
        translated, skipped = process_po_file(
            client, po_path, target_lang, args.overwrite
        )
        print(f"{target_lang}: translated {translated}, kept {skipped} existing")


if __name__ == "__main__":
    main()
