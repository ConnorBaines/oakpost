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


# --- Command registry ------------------------------------------------------
# Subcommand name -> function. Adding an analysis is one line here.

COMMANDS = {
    "headers": basic_headers,
    "received": received_chain,
    "auth": auth_results,
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

def main():
    args = sys.argv[1:]

    if not args:
        print("usage: oakpost.py [command] <sample.eml>")
        print("commands:", ", ".join(COMMANDS))
        return

    if args[0] in COMMANDS:
        chosen = [args[0]]        # a scalpel
        path = args[1]
    else:
        chosen = list(COMMANDS)   # no command given, so run everything
        path = args[0]

    msg = load(path)
    for name in chosen:
        print_section(name, COMMANDS[name](msg))


if __name__ == "__main__":
    main()