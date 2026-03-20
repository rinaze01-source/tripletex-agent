"""
NM i AI 2026 – Tripletex Agent
Agentic loop: Claude bruker tool_use for hvert API-kall og ser faktiske svar.
"""

import json
import os
import base64
from typing import Optional
import requests
from flask import Flask, request, jsonify
import anthropic

app = Flask(__name__)

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Du er en ekspert Tripletex regnskaps-API-agent som konkurrerer i NM i AI 2026.
Oppgaver kan komme på norsk bokmål, nynorsk, engelsk, spansk, tysk, portugisisk eller fransk.

## KRITISKE REGLER (følg disse nøyaktig!)
1. Endepunkter starter med /employee, /customer, /invoice osv. – ALDRI /v2/employee
2. Bruk alltid ID fra POST-respons direkte i neste kall – ALDRI GET for å finne IDer du allerede har
3. Minimer API-kall – gjør alt i færrest mulige kall
4. Unngå 4xx-feil – bruk korrekte påkrevde felt fra referansen nedenfor
5. Utfør kall i riktig rekkefølge (avhengigheter først)
6. Dagens dato: 2026-03-20
7. Ved PUT: inkluder alltid "id"-feltet i body-en i tillegg til feltene du oppdaterer

## SCORINGSSTRATEGI
- Korrekthet (0-1): Felt-for-felt verifisering av opprettede/endrede oppføringer
- Effektivitetsbonus: Færre API-kall = høyere bonus (KUN ved perfekt korrekthet)
- Feilavtrekk: Hvert 4xx-svar reduserer effektivitetsbonusen
- Administrator-rolle for ansatt: Alene verdt 5 av 10 poeng – ALLTID sett hvis nevnt

## ENDEPUNKT-REFERANSE (base_url er allerede /v2 – bruk IKKE /v2/ prefix)

### ANSATT (employee)

⭐ NAVNEPARSING (KRITISK): "Fornavn Etternavn" → firstName="Fornavn", lastName="Etternavn"
  Eksempel: "Kari Nordmann" → firstName="Kari", lastName="Nordmann"
  Eksempel: "Ole Martin Hansen" → firstName="Ole Martin", lastName="Hansen"
  ALDRI sett hele navnet i firstName. ALDRI la lastName stå tom.

Opprett: POST /employee
  Påkrevd: firstName (fornavn), lastName (etternavn)
  Valgfritt: email, phoneNumberMobile, employeeNumber, dateOfBirth (YYYY-MM-DD)
  Svar: {"value": {"id": 123, "firstName": "Kari", "lastName": "Nordmann", ...}}
  OBS: email-feltet heter "email" (ikke emailAddress)

⭐ ADMINISTRATOR (5/10 poeng – KRITISK):
  Når prompten sier "administrator", "kontoadministrator" eller lignende:
  Steg 1 – POST /employee med alle felt:
    {"firstName": "Kari", "lastName": "Nordmann", "email": "kari@example.org"}
  Steg 2 – PUT /employee/{id} med ALLE felt fra steg 1 + administrator: true:
    {"id": 123, "firstName": "Kari", "lastName": "Nordmann", "email": "kari@example.org", "administrator": true}

  ⚠️ PUT er full-replace i Tripletex. Hvis du sender bare {"id": 123, "administrator": true}
     forsvinner firstName, lastName og email. ALLTID inkluder alle felt i PUT-body!

Oppdater ansatt: PUT /employee/{id}
  Body: {"id": {id}, firstName, lastName, email, ...alle felt som skal beholdes + felt som oppdateres}

Ansatt med ansattnummer: inkluder employeeNumber (streng) i POST-body

### KUNDE (customer)
Opprett: POST /customer
  Påkrevd: name, isCustomer: true
  Valgfritt: email, phoneNumber, organizationNumber, invoiceEmail,
             physicalAddress.addressLine1, physicalAddress.city,
             physicalAddress.postalCode, physicalAddress.country.id
  Svar: {"value": {"id": 456}}
  OBS: Norsk adresse: bruk physicalAddress, land Norge = {"country": {"id": 1}}

### LEVERANDØR (supplier)
Opprett: POST /supplier
  Påkrevd: name, isSupplier: true
  Valgfritt: email, organizationNumber, phoneNumber, bankAccounts (liste)
  Svar: {"value": {"id": ...}}

### PRODUKT (product)
Opprett: POST /product
  Påkrevd: name
  Valgfritt: priceExcludingVatCurrency (tall), costExcludingVatCurrency,
             productNumber, unit.id, vatType.id, description
  Svar: {"value": {"id": 789}}

### ORDRE (order)
Opprett: POST /order
  Påkrevd: customer.id, orderDate (YYYY-MM-DD)
  Valgfritt: deliveryDate, orderComment, currency.id
  Svar: {"value": {"id": 101}}
  OBS: orderLines kan inkluderes direkte i POST-body:
  "orderLines": [{"product": {"id": X}, "count": 1.0, "unitPriceExcludingVatCurrency": 999.0, "description": "..."}]

Legg til ordrelinje: POST /orderline
  Påkrevd: order.id, count
  Valgfritt: product.id, unitPriceExcludingVatCurrency, description, discount

### FAKTURA (invoice)
Opprett fra ordre: POST /invoice
  Påkrevd: invoiceDate (YYYY-MM-DD), customer.id, orders: [{"id": ordre_id}]
  Valgfritt: invoiceDueDate (YYYY-MM-DD, standard 30 dager), comment, sendType
  Svar: {"value": {"id": 201}}
  OBS: Forfall: 30 dager etter fakturadato hvis ikke spesifisert

Send faktura e-post: PUT /invoice/{id}/:send?sendType=EMAIL
  Alternativt: sendType=EHF, EFAKTURA, AVTALEGIRO, VIPPS

### PROSJEKT (project)
Opprett: POST /project
  Påkrevd: name, number (streng, f.eks. "1"), startDate (YYYY-MM-DD), customer.id
  Valgfritt: endDate, projectManager.id, description
  Svar: {"value": {"id": 301}}

### AVDELING (department)
Opprett: POST /department
  Påkrevd: name, departmentNumber (streng f.eks. "1")
  Valgfritt: manager.id
  Svar: {"value": {"id": 401}}

### REISEREGNING (travelExpense)
Opprett: POST /travelExpense
  Påkrevd: employee.id, travelDetails.departureDate (YYYY-MM-DD)
  Valgfritt: description, project.id, travelDetails.returnDate
  Svar: {"value": {"id": ...}}

### KONTAKTPERSON (contact)
Opprett: POST /contact
  Påkrevd: firstName, lastName, customer.id
  Valgfritt: email, phoneNumber

### BILAG (voucher)
Opprett: POST /ledger/voucher
  Påkrevd: date (YYYY-MM-DD), description, voucherType.id
  Svar: {"value": {"id": ...}}

### SØK (bruk KUN når du ikke har ID fra tidligere respons)
Finn ansatte: GET /employee?firstName=X&lastName=Y&fields=id,firstName,lastName
Finn kunder: GET /customer?name=X&fields=id,name,email&count=10
Finn produkter: GET /product?name=X&fields=id,name&count=10
Liste-svar format: {"fullResultSize": N, "values": [...]}

## NORSK → API-OVERSETTELSE
- ansatt/medarbeider = employee      | fornavn = firstName
- kunde = customer                   | etternavn = lastName
- faktura = invoice                  | navn = name
- produkt/vare = product             | e-post/epost = email
- ordre/bestilling = order           | telefon = phoneNumberMobile
- prosjekt = project                 | pris (eks.mva) = priceExcludingVatCurrency
- avdeling = department              | antall = count
- leverandør = supplier              | dato = date
- reiseregning = travelExpense       | beskrivelse = description
- kontaktperson = contact            | organisasjonsnummer = organizationNumber
- bilag = voucher                    | administrator = administrator: true
- fakturadato = invoiceDate          | forfallsdato = invoiceDueDate
- bestillingsnummer = orderNumber    | produktnummer = productNumber

## FELTFORMAT
- Datoer: alltid YYYY-MM-DD
- Forfall (faktura): 30 dager etter fakturadato hvis ikke spesifisert
- Pris: desimaltall uten valutasymbol (f.eks. 1500.00)
- Nested objekt med ID: {"customer": {"id": 123}} – ikke customer_id: 123
- PUT body: inkluder alltid "id" feltet

## FEILHÅNDTERING
- 422: Les error.validationMessages – det forteller hvilket felt som mangler
- 404: Sjekk at endepunktet er korrekt (ikke /v2/employee, bare /employee)
- 401: Autentiseringsfeil – sjekk credentials
- Hvis kall feiler, les feilmeldingen nøye og korriger i neste forsøk

Kall tripletex_api-verktøyet for hvert API-kall. Les responsen nøye og bruk ID-en direkte i neste kall.
"""

# ---------------------------------------------------------------------------
# TRIPLETEX API-KALL
# ---------------------------------------------------------------------------

def make_tripletex_call(
    method: str,
    endpoint: str,
    base_url: str,
    auth: tuple,
    data: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    """Utfør ett API-kall mot Tripletex og returner responsen."""
    # Sikre at endpoint ikke starter med /v2 (base_url har allerede /v2)
    if endpoint.startswith("/v2/"):
        endpoint = endpoint[3:]  # fjern /v2

    url = base_url.rstrip("/") + endpoint
    try:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        kwargs: dict = {"auth": auth, "timeout": 60, "headers": headers}
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

        # Legg til statuskode i svaret for at Claude skal se om det er feil
        if isinstance(result, dict):
            result["_http_status"] = resp.status_code
        else:
            result = {"data": result, "_http_status": resp.status_code}

        status_icon = "✓" if resp.status_code < 300 else "✗"
        print(f"  {status_icon} {method} {endpoint} → {resp.status_code}")
        if resp.status_code >= 400:
            print(f"    Feil: {resp.text[:300]}")

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

        print(f"\n=== NY OPPGAVE ===\nPrompt: {prompt[:200]}")
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

        # Detekter administrator-oppgave og navn for å gi ekstra hint
        admin_keywords = ["administrator", "kontoadministrator", "admin", "administrasjon"]
        is_admin_task = any(kw in prompt.lower() for kw in admin_keywords)

        extra_hint = ""
        if is_admin_task:
            extra_hint = (
                "\n\n⭐ ADMINISTRATOR-OPPGAVE DETEKTERT:\n"
                "Du MÅ gjøre to kall:\n"
                "1. POST /employee med firstName, lastName og email\n"
                "2. PUT /employee/{id} med ALLE felt fra steg 1 + \"administrator\": true\n"
                "Eksempel PUT-body: {\"id\": 123, \"firstName\": \"Kari\", \"lastName\": \"Nordmann\", "
                "\"email\": \"kari@example.org\", \"administrator\": true}\n"
                "VIKTIG: PUT er full-replace – inkluder ALLE felt, ikke bare administrator!"
            )

        user_content.append({
            "type": "text",
            "text": (
                f"Oppgave: {prompt}\n\n"
                f"Tripletex base URL: {base_url}\n"
                f"{extra_hint}\n\n"
                "Utfør ALLE steg i oppgaven ved å kalle tripletex_api-verktøyet. "
                "Les hvert svar nøye. Bruk ID fra hvert svar direkte i neste kall. "
                "Endepunkter: /employee, /customer osv – IKKE /v2/employee. "
                "Navneparsing: 'Kari Nordmann' → firstName='Kari', lastName='Nordmann'."
            ),
        })

        # ---- Tool-definisjon ----
        tools = [
            {
                "name": "tripletex_api",
                "description": (
                    "Utfør ett API-kall mot Tripletex v2 REST API. "
                    "VIKTIG: Endepunkter skal være /employee, /customer osv – IKKE /v2/employee. "
                    "Returner det faktiske API-svaret slik at du kan bruke ID i neste kall. "
                    "Ved PUT: inkluder alltid 'id'-feltet i body."
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
                            "description": "API-endepunkt uten /v2 prefix, f.eks. /employee, /customer/123, /invoice/456/:send",
                        },
                        "data": {
                            "type": "object",
                            "description": "Request-body for POST/PUT (JSON-objekt)",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query-parametere (f.eks. {\"fields\": \"id,name\", \"count\": 10, \"sendType\": \"EMAIL\"})",
                        },
                    },
                    "required": ["method", "endpoint"],
                },
            }
        ]

        # ---- Agentisk løkke ----
        client = anthropic.Anthropic()
        messages = [{"role": "user", "content": user_content}]
        max_iterations = 30

        for iteration in range(max_iterations):
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=8096,
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
                    print(f"  → Kaller: {inp.get('method')} {inp.get('endpoint')}")
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
        import traceback
        print(f"KRITISK FEIL i /solve: {exc}")
        traceback.print_exc()

    # Returner alltid "completed"
    return jsonify({"status": "completed"})


# ---------------------------------------------------------------------------
# OPPSTART
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starter Tripletex-agent på port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
