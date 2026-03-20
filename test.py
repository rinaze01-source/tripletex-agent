"""
Test-script for NM i AI 2026 Tripletex Agent.
Kjører 10 tilfeldige oppgaver mot sandkassen og viser resultater.
"""

import json
import random
import time
import requests

# ---------------------------------------------------------------------------
# KONFIGURASJON
# ---------------------------------------------------------------------------
AGENT_URL = "https://tripletex-agent.onrender.com/solve"  # Bytt til http://localhost:8000/solve for lokal test
SANDBOX_TOKEN = "eyJ0b2tlbklkIjoyMTQ3NjMyNzMzLCJ0b2tlbiI6IjZmNzMxNzI3LWI4YzAtNDUwNS04NDYyLTdiMjZhNjY5ZWJjOCJ9"
SANDBOX_BASE_URL = "https://kkpqfuj-amager.tripletex.dev/v2"

# ---------------------------------------------------------------------------
# TESTDATA
# ---------------------------------------------------------------------------
FORNAVN = ["Kari", "Ole", "Anne", "Per", "Ingrid", "Lars", "Marte", "Erik", "Hilde", "Bjørn",
           "Silje", "Tor", "Maria", "Hans", "Liv", "Geir", "Nina", "Rolf", "Eva", "Svein"]
ETTERNAVN = ["Nordmann", "Hansen", "Olsen", "Larsen", "Andersen", "Nilsen", "Pedersen",
             "Johansen", "Berg", "Halvorsen", "Kristiansen", "Jensen", "Dahl", "Bakke", "Moen"]
FIRMANAVN = ["Acme AS", "Berg & Co", "Norsk Handel AS", "Fjord Tech AS", "Oslo Bygg AS",
             "Viking Solutions AS", "Nordisk Data AS", "Havfisk AS", "Alphatech Norge AS"]
PRODUKTNAVN = ["Kontorstol Pro", "Laptop Dell 15", "Skrivebord hev/senk", "Monitor 27 tommer",
               "Headset Jabra", "Webkamera HD", "Tastatur trådløst", "Mus ergonomisk"]
BYER = ["Oslo", "Bergen", "Trondheim", "Stavanger", "Tromsø", "Kristiansand", "Drammen"]


def tilfeldig_navn():
    return random.choice(FORNAVN), random.choice(ETTERNAVN)


def tilfeldig_epost(fornavn, etternavn):
    domener = ["example.org", "test.no", "firma.no", "bedrift.com"]
    return f"{fornavn.lower()}.{etternavn.lower()}@{random.choice(domener)}"


def tilfeldig_pris():
    return round(random.uniform(100, 9999), 2)


def tilfeldig_orgnr():
    return str(random.randint(100000000, 999999999))


# ---------------------------------------------------------------------------
# 10 TESTOPPGAVER
# ---------------------------------------------------------------------------
def generer_oppgaver():
    fn1, ln1 = tilfeldig_navn()
    fn2, ln2 = tilfeldig_navn()
    fn3, ln3 = tilfeldig_navn()
    epost1 = tilfeldig_epost(fn1, ln1)
    epost2 = tilfeldig_epost(fn2, ln2)
    firma1 = random.choice(FIRMANAVN)
    firma2 = random.choice(FIRMANAVN)
    produkt1 = random.choice(PRODUKTNAVN)
    pris1 = tilfeldig_pris()
    orgnr1 = tilfeldig_orgnr()
    by1 = random.choice(BYER)

    return [
        {
            "navn": "1. Opprett ansatt (enkel)",
            "prompt": f"Opprett en ansatt med navn {fn1} {ln1} og epost {epost1}."
        },
        {
            "navn": "2. Opprett ansatt med administrator-rolle",
            "prompt": f"Opprett en ansatt med navn {fn2} {ln2}, epost {epost2}. "
                      f"Personen skal være kontoadministrator."
        },
        {
            "navn": "3. Opprett kunde med organisasjonsnummer",
            "prompt": f"Opprett en kunde med navn {firma1}, organisasjonsnummer {orgnr1}, "
                      f"epost post@{firma1.lower().replace(' ', '').replace('as','')}.no og "
                      f"adresse Storgata 1, {by1}."
        },
        {
            "navn": "4. Opprett leverandør",
            "prompt": f"Create a new supplier called {firma2} with organization number {tilfeldig_orgnr()} "
                      f"and email contact@supplier.no."
        },
        {
            "navn": "5. Opprett produkt med pris",
            "prompt": f"Opprett et produkt med navn '{produkt1}', pris {pris1} kroner eks. mva, "
                      f"og produktnummer P{random.randint(100,999)}."
        },
        {
            "navn": "6. Opprett avdeling",
            "prompt": f"Erstelle eine neue Abteilung namens 'Salg og marked' mit Abteilungsnummer "
                      f"{random.randint(10,99)}."
        },
        {
            "navn": "7. Opprett ansatt på engelsk",
            "prompt": f"Create an employee named {fn3} {ln3} with email {tilfeldig_epost(fn3, ln3)} "
                      f"and phone number 9{random.randint(1000000,9999999)}."
        },
        {
            "navn": "8. Opprett kunde på spansk",
            "prompt": f"Crea un nuevo cliente llamado 'Nordic Solutions AS' con correo electrónico "
                      f"info@nordicsolutions.no y número de organización {tilfeldig_orgnr()}."
        },
        {
            "navn": "9. Opprett produkt på fransk",
            "prompt": f"Créez un nouveau produit appelé 'Bureau réglable en hauteur' avec un prix de "
                      f"{round(random.uniform(2000, 8000), 2)} couronnes hors TVA."
        },
        {
            "navn": "10. Opprett ansatt med administrator på nynorsk",
            "prompt": f"Opprett ein tilsett med namn {fn1} {ln1}, epost {epost1}. "
                      f"Vedkomande skal vera kontoadministrator."
        },
    ]


# ---------------------------------------------------------------------------
# KJØR TESTER
# ---------------------------------------------------------------------------
def kjør_test(oppgave: dict, index: int, total: int) -> dict:
    print(f"\n{'='*60}")
    print(f"Test {index}/{total}: {oppgave['navn']}")
    print(f"Prompt: {oppgave['prompt'][:100]}...")
    print(f"{'='*60}")

    payload = {
        "prompt": oppgave["prompt"],
        "files": [],
        "tripletex_credentials": {
            "base_url": SANDBOX_BASE_URL,
            "session_token": SANDBOX_TOKEN,
        }
    }

    start = time.time()
    try:
        resp = requests.post(AGENT_URL, json=payload, timeout=120)
        elapsed = round(time.time() - start, 1)

        if resp.status_code == 200 and resp.json().get("status") == "completed":
            print(f"✓ FULLFØRT på {elapsed}s")
            return {"navn": oppgave["navn"], "status": "OK", "tid": elapsed}
        else:
            print(f"✗ FEIL: HTTP {resp.status_code} — {resp.text[:200]}")
            return {"navn": oppgave["navn"], "status": "FEIL", "tid": elapsed, "detalj": resp.text[:200]}

    except requests.exceptions.ConnectionError:
        print("✗ TILKOBLINGSFEIL — er serveren startet? Kjør: python3 app.py")
        return {"navn": oppgave["navn"], "status": "TILKOBLINGSFEIL", "tid": 0}
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        print(f"✗ UNNTAK: {exc}")
        return {"navn": oppgave["navn"], "status": "FEIL", "tid": elapsed, "detalj": str(exc)}


def main():
    print("=" * 60)
    print("NM i AI 2026 – Tripletex Agent Testsuite")
    print(f"Agent URL: {AGENT_URL}")
    print(f"Sandbox:   {SANDBOX_BASE_URL}")
    print("=" * 60)

    oppgaver = generer_oppgaver()
    resultater = []

    for i, oppgave in enumerate(oppgaver, 1):
        resultat = kjør_test(oppgave, i, len(oppgaver))
        resultater.append(resultat)
        if i < len(oppgaver):
            time.sleep(2)  # liten pause mellom tester

    # Oppsummering
    print(f"\n{'='*60}")
    print("OPPSUMMERING")
    print(f"{'='*60}")
    ok = sum(1 for r in resultater if r["status"] == "OK")
    print(f"Resultat: {ok}/{len(resultater)} fullført\n")
    for r in resultater:
        icon = "✓" if r["status"] == "OK" else "✗"
        tid = f"{r['tid']}s" if r["tid"] else ""
        print(f"  {icon} {r['navn']} {tid}")
        if "detalj" in r:
            print(f"      → {r['detalj']}")

    print(f"\n{'='*60}")
    if ok == len(resultater):
        print("Alle tester fullført! Klar for innsending.")
    else:
        print(f"{len(resultater)-ok} test(er) feilet — sjekk loggene over.")


if __name__ == "__main__":
    main()
