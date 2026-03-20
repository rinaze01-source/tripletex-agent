"""
NM i AI 2026 – Tripletex Agent
Agentic loop: Claude bruker tool_use for hvert API-kall og ser faktiske svar.
"""

import json
import os
import base64
import requests
from flask import Flask, request, jsonify
import anthropic

app = Flask(__name__)

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Du er en ekspert Tripletex regnskaps-API-agent som konkurrerer i NM i AI 2026.

## SCORINGSSTRATEGI (kritisk viktig!)
- Korrekthet (0-1): Felt-for-felt verifisering av opprettede/endrede oppføringer
- Effektivitetsbonus: Færre API-kall = høyere bonus (KUN ved perfekt korrekthet)
- Feilavtrekk: Hvert 4xx-svar reduserer effektivitetsbonusen
- Administrator-rolle: Alene verdt 5 av 10 poeng for ansattoppgaver – ALLTID sett dette hvis nevnt

## KRITISKE REGLER
1. Bruk ALLTID ID fra POST-respons direkte i neste kall – ALDRI GET for å finne IDer du allerede har
2. Minimer antall API-kall – gjør alt i færrest mulige kall
3. Unngå 4xx-feil – bruk korrekte påkrevde felt
4. Utfør kall i riktig rekkefølge (lag avhengigheter først)
5. Dagens dato: 2026-03-20

## TRIPLETEX API-REFERANSE

### ANSATT (employee)
Opprett: POST /v2/employee
  Påkrevd: firstName, lastName
  Valgfritt: email, phoneNumberMobile, phoneNumberHome, employeeNumber, division.id
  Svar: {"value": {"id": 123, ...}}

Sett administrator: PUT /v2/employee/{id}
  Body: {"administrator": true}
  ⭐ KRITISK: Gjør ALLTID dette etter opprettelse hvis "administrator" nevnes (5/10 poeng!)

Oppdater ansatt: PUT /v2/employee/{id}
  Body: {feltene som skal oppdateres}

### KUNDE (customer)
Opprett: POST /v2/customer
  Påkrevd: name, isCustomer: true
  Valgfritt: email, phoneNumber, organizationNumber, address.street, address.city,
             address.postalCode, address.country.id (Norge=NO: bruk {"id":1})
  Svar: {"value": {"id": 456}}

### LEVERANDØR (supplier)
Opprett: POST /v2/supplier
  Påkrevd: name, isSupplier: true
  Valgfritt: email, organizationNumber, phoneNumber

### PRODUKT (product)
Opprett: POST /v2/product
  Påkrevd: name
  Valgfritt: priceExcludingVatCurrency (tall), productNumber, unit.id, vatType.id
  Svar: {"value": {"id": 789}}

### ORDRE (order)
Opprett: POST /v2/order
  Påkrevd: customer.id, orderDate (YYYY-MM-DD)
  Valgfritt: deliveryDate, orderComment, currency.id, contactPersonId
  Svar: {"value": {"id": 101}}

Legg til ordrelinje: POST /v2/orderline
  Påkrevd: order.id, count
  Valgfritt: product.id, unitPriceExcludingVatCurrency, description, discount

### FAKTURA (invoice)
Opprett: POST /v2/invoice
  Påkrevd: invoiceDate (YYYY-MM-DD), invoiceDueDate (YYYY-MM-DD),
           customer.id, orders: [{"id": ordre_id}]
  Valgfritt: comment, paymentTypeId
  Svar: {"value": {"id": 201}}

Send faktura: PUT /v2/invoice/{id}/:send
  Params: sendType=EMAIL (eller EHF, EFAKTURA, AVTALEGIRO)

### PROSJEKT (project)
Opprett: POST /v2/project
  Påkrevd: name, number (streng f.eks. "1"), startDate (YYYY-MM-DD), customer.id
  Valgfritt: endDate, manager.id, description, projectManager.id
  Svar: {"value": {"id": 301}}

### AVDELING (department)
Opprett: POST /v2/department
  Påkrevd: name, departmentNumber (streng f.eks. "1")
  Valgfritt: manager.id
  Svar: {"value": {"id": 401}}

### REISEREGNING (travelExpense)
Opprett: POST /v2/travelExpense
  Påkrevd: employee.id, travelDetails.departureDate (YYYY-MM-DD)
  Valgfritt: description, project.id, travelDetails.returnDate

### KONTAKTPERSON (contact)
Opprett: POST /v2/contact
  Påkrevd: firstName, lastName, customer.id
  Valgfritt: email, phoneNumber

### BILAG (voucher)
Opprett: POST /v2/ledger/voucher
  Påkrevd: date (YYYY-MM-DD), description, voucherType.id
  Voucherlinjer: POST /v2/ledger/voucher/{id}/voucherReception

### SØK (bruk kun når absolutt nødvendig)
Finn ansatte: GET /v2/employee?firstName=X&lastName=Y&fields=id,firstName,lastName
Finn kunder: GET /v2/customer?name=X&fields=id,name,email&count=10
Finn produkter: GET /v2/product?name=X&fields=id,name&count=10

## NORSK → API-OVERSETTELSE
- ansatt = employee            | fornavn = firstName
- kunde = customer             | etternavn = lastName
- faktura = invoice            | navn = name
- produkt = product            | e-post/epost = email
- ordre/bestilling = order     | telefon = phoneNumberMobile
- prosjekt = project           | pris = priceExcludingVatCurrency
- avdeling = department        | antall = count
- leverandør = supplier        | dato = date
- reiseregning = travelExpense | beskrivelse = description
- kontaktperson = contact      | organisasjonsnummer = organizationNumber
- bilag = voucher              | administrator = administrator: true (PUT /v2/employee/{id})

## FELTFORMAT
- Datoer: alltid YYYY-MM-DD
- Forfall (faktura): bruk 14 dager etter fakturadato hvis ikke spesifisert
- Pris: tall uten valutasymbol (f.eks. 1500.00)
- Nested objekt med ID: {"customer": {"id": 123}} – ikke bare customer_id: 123

Kall tripletex_api-verktøyet for hvert API-kall. Se responsen og bruk ID-en direkte i neste kall.
"""

# ---------------------------------------------------------------------------
# TRIPLETEX API-KALL
# ---------------------------------------------------------------------------

def make_tripletex_call(
    method: str,
    endpoint: str,
    base_url: str,
    auth: tuple,
    data: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Utfør ett API-kall mot Tripletex og returner responsen."""
    url = base_url.rstrip("/") + endpoint
    try:
        kwargs: dict = {"auth": auth, "timeout": 45, "headers": {"Accept": "application/json"}}
        if params:
            kwargs["params"] = params

        method = method.upper()
        if method == "GET":
            resp = requests.get(url, **kwargs)
        elif method == "POST":
            resp = requests.post(url, json=data or {}, **kwargs)
        elif method == "PUT":
            resp = requests.put(url, json=data or {}, **kwargs)
        elif method == "DELETE":
            resp = requests.delete(url, **kwargs)
        else:
            return {"error": f"Ukjent metode: {method}"}

        try:
            result = resp.json()
        except Exception:
            result = {"status_code": resp.status_code, "text": resp.text[:500]}

        print(f"  {method} {endpoint} → {resp.status_code}")
        return result

    except Exception as exc:
        print(f"  FEIL ved {method} {endpoint}: {exc}")
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# FLASK-RUTER
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/solve", methods=["POST"])
def solve():
    try:
        body = request.get_json(force=True, silent=True) or {}
        prompt: str = body.get("prompt", "")
        files: list = body.get("files", [])
        creds: dict = body.get("tripletex_credentials", {})

        base_url: str = creds.get("base_url", "").rstrip("/")
        session_token: str = creds.get("session_token", "")
        auth = ("0", session_token)

        print(f"\n=== NY OPPGAVE ===\nPrompt: {prompt[:120]}")
        print(f"Base URL: {base_url}")

        # ---- Bygg brukermelding ----
        user_content: list = []

        # Legg til vedlagte filer
        for f in files:
            fname = f.get("filename", "fil")
            mime = f.get("mime_type", "application/octet-stream")
            b64 = f.get("content_base64", "")
            try:
                if mime.startswith("image/"):
                    user_content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": b64},
                    })
                elif mime == "application/pdf":
                    user_content.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    })
                else:
                    text = base64.b64decode(b64).decode("utf-8", errors="replace")
                    user_content.append({"type": "text", "text": f"[Fil: {fname}]\n{text}"})
            except Exception:
                user_content.append({"type": "text", "text": f"[Fil: {fname} – kunne ikke leses]"})

        user_content.append({
            "type": "text",
            "text": (
                f"Oppgave: {prompt}\n\n"
                f"Tripletex base URL: {base_url}\n\n"
                "Utfør denne oppgaven ved å kalle tripletex_api-verktøyet. "
                "Bruk ID fra hvert svar direkte i neste kall. Minimer antall kall."
            ),
        })

        # ---- Tool-definisjon ----
        tools = [
            {
                "name": "tripletex_api",
                "description": (
                    "Utfør ett API-kall mot Tripletex v2 REST API. "
                    "Returner alltid det faktiske API-svaret slik at du kan bruke ID i neste kall."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PUT", "DELETE"],
                            "description": "HTTP-metode",
                        },
                        "endpoint": {
                            "type": "string",
                            "description": "API-endepunkt, f.eks. /v2/employee eller /v2/employee/123",
                        },
                        "data": {
                            "type": "object",
                            "description": "Request-body for POST/PUT",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query-parametere for GET (f.eks. fields, count)",
                        },
                    },
                    "required": ["method", "endpoint"],
                },
            }
        ]

        # ---- Agentisk løkke ----
        client = anthropic.Anthropic()
        messages = [{"role": "user", "content": user_content}]
        max_iterations = 25  # maks runder for å unngå uendelig løkke

        for iteration in range(max_iterations):
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

            print(f"  Iterasjon {iteration + 1}: stop_reason={response.stop_reason}")

            # Legg til assistent-svar i meldingshistorikk
            messages.append({"role": "assistant", "content": response.content})

            # Ferdig – Claude svarte med tekst og ingen flere verktøykall
            if response.stop_reason == "end_turn":
                print("  Claude ferdig.")
                break

            # Prosesser verktøykall
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    inp = block.input
                    api_result = make_tripletex_call(
                        method=inp.get("method", "GET"),
                        endpoint=inp.get("endpoint", ""),
                        base_url=base_url,
                        auth=auth,
                        data=inp.get("data"),
                        params=inp.get("params"),
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(api_result, ensure_ascii=False),
                    })

                messages.append({"role": "user", "content": tool_results})
            else:
                # Ukjent stop_reason – avslutt
                break

    except Exception as exc:
        print(f"KRITISK FEIL i /solve: {exc}")

    # Returner alltid "completed"
    return jsonify({"status": "completed"})


# ---------------------------------------------------------------------------
# OPPSTART
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starter Tripletex-agent på port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
