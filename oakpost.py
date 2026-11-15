"""oakpost — email header triage for the person writing the sample up."""

import sys
import re
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


# --- Output ----------------------------------------------------------------
# Each takes data and prints it. None of them work anything out.

def print_section(title, pairs):
    """Print a titled block of aligned label/value lines."""
    print(f"\n== {title} ==")
    if not pairs:
        print("(nothing found)")
        return
    width = max(len(key) for key in pairs) + 1
    for key, value in pairs.items():
        print((key + ":").ljust(width), value)


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

    if not args:
        print("usage: oakpost.py [command] <sample.eml>")
        print("commands:", ", ".join(COMMANDS))
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

    for name in chosen:
        print_section(name, COMMANDS[name](msg))


if __name__ == "__main__":
    main()