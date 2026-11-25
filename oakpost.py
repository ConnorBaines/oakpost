"""oakpost — email header triage for the person writing the sample up."""

import os
import sys
import re
import unicodedata
from email import policy
from email.parser import BytesParser


# --- Loading ---------------------------------------------------------------

def load(path):
    """Read a .eml off disk and return a parsed message object."""
    with open(path, "rb") as f:
        return BytesParser(policy=policy.default).parse(f)


# --- Analysis --------------------------------------------------------------
# Each takes a parsed message and returns a dict. None of them print.

def basic_headers(msg):
    """The four identity headers."""
    return {
        "From": msg["From"],
        "To": msg["To"],
        "Subject": msg["Subject"],
        "Date": msg["Date"],
    }

def received_chain(msg):
    """The Received chain, oldest hop first — the order the mail travelled."""
    hops = msg.get_all("Received") or []
    hops = list(reversed(hops))
    return {
        str(number): " ".join(str(hop).split())
        for number, hop in enumerate(hops, start=1)
    }

AUTH_METHODS = ("spf", "dkim", "dmarc")

def auth_results(msg):
    """SPF, DKIM and DMARC verdicts pulled out of Authentication-Results."""
    blob = " ".join(str(h) for h in (msg.get_all("Authentication-Results") or []))
    verdicts = {}
    for method in AUTH_METHODS:
        found = re.findall(
            rf"\b{method}=(\w+)(\s*\([^)]*\))?", blob, re.IGNORECASE
        )
        parts = [v + (" " + r.strip() if r else "") for v, r in found]
        verdicts[method.upper()] = "/".join(dict.fromkeys(parts)) or "not present"
    return verdicts

URL_PATTERN = re.compile(r"https?:\/\/[^\s\"'<>]+", re.IGNORECASE)
ANCHOR_PATTERN = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)

def extract_urls(msg):
    """Every URL in the plain-text and HTML parts, tagged with where it was found."""
    found = {}
    for part in msg.walk():
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        label = "plain" if kind == "text/plain" else "html"
        # get_content() decodes quoted-printable, so links split across lines come back whole
        for url in URL_PATTERN.findall(part.get_content()):
            found.setdefault(url, [])
            if label not in found[url]:
                found[url].append(label)
        # In HTML, show the whole link when its visible text is a different web address
        if label == "html":
            for href, inner in ANCHOR_PATTERN.findall(part.get_content()):
                shown = re.sub(r"<[^>]+>", "", inner).strip()
                if href in found and shown != href and URL_PATTERN.search(shown):
                    found[href].append(f'<a href="{href}">{shown}</a>')
    return {
        str(number): f"{url}  ({', '.join(where)})"
        for number, (url, where) in enumerate(found.items(), start=1)
    }

# --- Command registry ------------------------------------------------------
# Subcommand name -> function. Adding an analysis is one line here.

COMMANDS = {
    "headers": basic_headers,
    "received": received_chain,
    "auth": auth_results,
    "urls": extract_urls,
}


# --- Display helpers -------------------------------------------------------
# Text in, text out. They change how a value looks on screen, never what was found,
# so the analysis above always works on the real, raw email.

SCHEME_PATTERN = re.compile(r"\bhttp(s?)://", re.IGNORECASE)
IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d{1,3})\b")
DOMAIN_PATTERN = re.compile(r"\b((?:[\w-]+\.)*[\w-]+)\.([^\W\d_]{2,63})\b")

def defang(text):
    """Make links, domains and IPs safe to show: hxxps://, example[.]com, 203.0.113[.]25."""
    text = SCHEME_PATTERN.sub(r"hxxp\1://", text)
    text = IP_PATTERN.sub(r"\1[.]\2", text)
    return DOMAIN_PATTERN.sub(r"\1[.]\2", text)

def is_lookalike(char, word):
    """True if char is a non-Latin stand-in for a plain letter or digit."""
    if char.isascii() or not char.isalnum():
        return False
    plain = unicodedata.normalize("NFKC", char)
    if plain.isascii() and plain.isalnum():
        return True  # a styled copy of a plain letter, e.g. mathematical sans-serif or fullwidth
    script = unicodedata.name(char, "").split(" ")[0]
    # a Greek or Cyrillic letter inside an otherwise English word, e.g. the Greek T in "TV LICENSING"
    return script != "LATIN" and any(c.isascii() and c.isalpha() for c in word)

LOOKALIKE_COLOUR = "\033[1;97;41m"  # bold white on a red background
RESET_COLOUR = "\033[0m"
WORD_PATTERN = re.compile(r"\w+")

def colour_lookalikes(text):
    """Colour lookalike characters so they stand out in the terminal."""
    def colour_word(match):
        word = match.group()
        return "".join(
            f"{LOOKALIKE_COLOUR}{c}{RESET_COLOUR}" if is_lookalike(c, word) else c
            for c in word
        )
    return WORD_PATTERN.sub(colour_word, text)

def count_lookalikes(text):
    """How many lookalike characters a value contains."""
    return sum(
        is_lookalike(c, word) for word in WORD_PATTERN.findall(text) for c in word
    )


# --- Output ----------------------------------------------------------------
# Each takes data and prints it. None of them work anything out.

def print_section(title, pairs, fang=False, colour=False):
    """Print a titled block of aligned label/value lines.

    Every value is defanged on the way out unless fang is True, so nothing can print
    a live link by accident. colour marks lookalike characters (terminal only).
    Returns how many lookalike characters it printed, for the note at the end.
    """
    print(f"\n== {title} ==")
    if not pairs:
        print("(nothing found)")
        return 0
    width = max(len(key) for key in pairs) + 1
    lookalikes = 0
    for key, value in pairs.items():
        value = str(value)
        if not fang:
            value = defang(value)
        lookalikes += count_lookalikes(value)
        if colour:
            value = colour_lookalikes(value)
        print((key + ":").ljust(width), value)
    return lookalikes

def print_lookalike_note(count, colour):
    """One sentence explaining lookalike letters, printed only when some were found."""
    if not count:
        return
    shown = " (highlighted in red)" if colour else ""
    print(
        f"\nNote: {count} lookalike letter{'s' if count != 1 else ''} found{shown}. "
        "Disguised characters are often used to dodge keyword filters. "
        "This could be an indicator if unexpected."
    )


# --- Entry point -----------------------------------------------------------

def fail(message):
    """Print an error and exit with a failure code."""
    print(f"oakpost: {message}", file=sys.stderr)
    sys.exit(1)

EXPECTED_HEADERS = ("From", "To", "Subject", "Date", "Received", "Message-ID")

def looks_like_email(msg):
    """True if the parsed message carries at least one header a real email would."""
    return any(msg[h] for h in EXPECTED_HEADERS)

def main():
    args = sys.argv[1:]

    # --fang can go anywhere in the command. It shows links live instead of defanged.
    fang = "--fang" in args
    args = [a for a in args if a != "--fang"]

    if not args:
        print("usage: oakpost.py [command] [--fang] <sample.eml>")
        print("commands:", ", ".join(COMMANDS))
        print("--fang:   show links live instead of defanged (they're defanged by default)")
        return

    if args[0] in COMMANDS:
        if len(args) < 2:
            fail(f"'{args[0]}' needs a file, e.g. oakpost.py {args[0]} sample.eml")
        chosen = [args[0]]        # a scalpel
        path = args[1]
    else:
        chosen = list(COMMANDS)   # no command given, so run everything
        path = args[0]

    try:
        msg = load(path)
    except OSError as e:
        fail(f"can't read {path}: {e.strerror}")

    if not looks_like_email(msg):
        fail(f"{path} doesn't look like an email (none of: {', '.join(EXPECTED_HEADERS)})")

    # Colour only when printing straight to a terminal. Saved to a file or piped into
    # another tool, the colour codes would show up as junk characters.
    colour = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    if colour and os.name == "nt":
        os.system("")  # switches on colour support in the older Windows console

    if fang:
        print("WARNING: --fang is on, so links below are live. Don't click them.")

    lookalikes = 0
    for name in chosen:
        lookalikes += print_section(name, COMMANDS[name](msg), fang=fang, colour=colour)
    print_lookalike_note(lookalikes, colour)


if __name__ == "__main__":
    main()