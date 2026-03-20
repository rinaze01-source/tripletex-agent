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
SYSTEM_PROMPT = """Du er en ekspert Tripletex regnskaps-API-agent for NM i AI 2026.
Oppgaver kan komme på: norsk bokmål, nynorsk, engelsk, tysk, spansk, portugisisk, fransk.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ABSOLUTTE REGLER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. ALDRI bruk /v2/ prefix — base_url har allerede /v2. Bruk /employee, /customer osv.
2. Bruk ID fra POST-respons direkte — ALDRI GET for å finne IDer du allerede har
3. PUT er FULL-REPLACE i Tripletex — inkluder ALLTID alle felt i PUT-body
4. Minimer antall API-kall (effektivitetsbonus ved perfekt korrekthet)
5. Utfør kall i riktig rekkefølge (opprett avhengigheter først)
6. Dagens dato: 2026-03-20
7. Les 4xx-feilmeldinger nøye og korriger i neste kall

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## NAVNEPARSING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Fornavn Etternavn" → firstName="Fornavn", lastName="Etternavn"
"Ole Martin Hansen" → firstName="Ole Martin", lastName="Hansen"
ALDRI sett hele navnet i firstName alene. ALDRI la lastName stå tom.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## OPPGAVETYPER MED EKSEMPLER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1. OPPRETT ANSATT (uten administrator)
POST /employee
Body: {"firstName": "Kari", "lastName": "Nordmann", "email": "kari@example.org"}
Valgfritt: phoneNumberMobile, employeeNumber (streng), dateOfBirth (YYYY-MM-DD)
Svar: {"value": {"id": 123, ...}}

### 2. OPPRETT ANSATT MED ADMINISTRATOR-ROLLE ⭐ (5/10 poeng!)
Kall 1 – POST /employee:
  {"firstName": "Kari", "lastName": "Nordmann", "email": "kari@example.org"}
  → Hent id fra svar, f.eks. 123

Kall 2 – PUT /employee/123 (inkluder ALLE felt + administrator):
  {"id": 123, "firstName": "Kari", "lastName": "Nordmann", "email": "kari@example.org", "administrator": true}

Trigger-ord: administrator, kontoadministrator, admin, account administrator, Kontoadministrator,
  administrador, administrateur, Verwalter, administrador de conta

### 3. OPPRETT KUNDE
POST /customer
Body: {"name": "Acme AS", "isCustomer": true, "email": "post@acme.no",
       "organizationNumber": "123456789", "phoneNumber": "22334455"}
Adresse: "physicalAddress": {"addressLine1": "Gateveien 1", "city": "Oslo",
          "postalCode": "0150", "country": {"id": 1}}
Svar: {"value": {"id": 456}}
OBS: Land Norge = country.id = 1. isCustomer MÅ være true.

### 4. OPPRETT LEVERANDØR
POST /supplier
Body: {"name": "Leverandør AS", "isSupplier": true, "email": "post@lev.no",
       "organizationNumber": "987654321", "phoneNumber": "33445566"}
Svar: {"value": {"id": ...}}
OBS: isSupplier MÅ være true.

### 5. OPPRETT PRODUKT
POST /product
Body: {"name": "Produkt X", "priceExcludingVatCurrency": 999.00,
       "productNumber": "P001", "description": "Beskrivelse her"}
Svar: {"value": {"id": 789}}

### 6. OPPRETT ORDRE MED ORDRELINJER
POST /order
Body: {
  "customer": {"id": KUNDE_ID},
  "orderDate": "2026-03-20",
  "orderLines": [
    {"product": {"id": PRODUKT_ID}, "count": 2.0,
     "unitPriceExcludingVatCurrency": 500.00, "description": "Varebeskrivelse"}
  ]
}
Svar: {"value": {"id": 101}}
OBS: Inkluder orderLines direkte i POST — sparer ett kall.

### 7. OPPRETT FAKTURA (fra ordre)
POST /invoice
Body: {
  "invoiceDate": "2026-03-20",
  "invoiceDueDate": "2026-04-19",
  "customer": {"id": KUNDE_ID},
  "orders": [{"id": ORDRE_ID}]
}
Svar: {"value": {"id": 201}}
OBS: Forfall = fakturadato + 30 dager hvis ikke spesifisert.
OBS: orders er en liste med ordre-objekter.

### 8. SEND FAKTURA
PUT /invoice/201/:send
Params: {"sendType": "EMAIL"}  ← eller EHF, EFAKTURA, AVTALEGIRO, VIPPS
OBS: sendType sendes som query-parameter, ikke i body.

### 9. REGISTRER BETALING PÅ FAKTURA
POST /invoice/201/payment
Body: {
  "paymentDate": "2026-03-20",
  "amount": 1000.00,
  "paymentTypeId": 1
}
OBS: paymentTypeId 1 = bank. Bruk amount fra fakturaen.

### 10. OPPRETT PROSJEKT
POST /project
Body: {
  "name": "Prosjekt Alpha",
  "number": "1001",
  "startDate": "2026-03-20",
  "endDate": "2026-12-31",
  "customer": {"id": KUNDE_ID},
  "projectManager": {"id": ANSATT_ID}
}
Svar: {"value": {"id": 301}}
OBS: number er en STRENG (ikke tall). projectManager krever ansatt-id.

### 11. OPPRETT AVDELING
POST /department
Body: {"name": "Salgsavdelingen", "departmentNumber": "10"}
Valgfritt: "manager": {"id": ANSATT_ID}
Svar: {"value": {"id": 401}}
OBS: departmentNumber er en STRENG.

### 12. OPPRETT REISEREGNING
POST /travelExpense
Body: {
  "employee": {"id": ANSATT_ID},
  "description": "Reise til Oslo",
  "travelDetails": {
    "departureDate": "2026-03-20",
    "returnDate": "2026-03-21"
  }
}
Svar: {"value": {"id": ...}}

### 13. OPPRETT KONTAKTPERSON
POST /contact
Body: {"firstName": "Per", "lastName": "Hansen",
       "customer": {"id": KUNDE_ID}, "email": "per@kunde.no"}
Svar: {"value": {"id": ...}}

### 14. SLETT ENTITET
Finn først: GET /employee?firstName=X&lastName=Y&fields=id,firstName,lastName&count=5
Slett: DELETE /employee/{id}
OBS: Verifiser at du sletter riktig entitet ved å sjekke navn/data.

### 15. OPPDATER EKSISTERENDE ENTITET
Finn: GET /customer?name=X&fields=id,name,email&count=5
Hent: GET /customer/{id}   ← for å se ALLE eksisterende felt
Oppdater: PUT /customer/{id} med ALLE eksisterende felt + endringene
OBS: PUT er full-replace — hent alle felt med GET /customer/{id} FØR du PUT-er!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## SØK (kun når du ikke allerede har ID)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GET /employee?firstName=X&lastName=Y&fields=id,firstName,lastName,email&count=10
GET /customer?name=X&fields=id,name,email,organizationNumber&count=10
GET /product?name=X&fields=id,name,priceExcludingVatCurrency&count=10
GET /order?fields=id,orderDate&count=10
Liste-format: {"fullResultSize": N, "values": [...]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## FLERSPRÅKLIG ORDBOK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NORSK          | ENGELSK          | TYSK              | SPANSK           | FRANSK
ansatt         | employee         | Mitarbeiter       | empleado         | employé
kunde          | customer         | Kunde             | cliente          | client
faktura        | invoice          | Rechnung          | factura          | facture
ordre          | order            | Bestellung        | pedido           | commande
produkt        | product          | Produkt           | producto         | produit
prosjekt       | project          | Projekt           | proyecto         | projet
avdeling       | department       | Abteilung         | departamento     | département
leverandør     | supplier         | Lieferant         | proveedor        | fournisseur
reiseregning   | travel expense   | Reisekostenabr.   | gasto de viaje   | note de frais
kontaktperson  | contact          | Kontaktperson     | contacto         | contact
administrator  | administrator    | Administrator     | administrador    | administrateur
fornavn        | first name       | Vorname           | nombre           | prénom
etternavn      | last name        | Nachname          | apellido         | nom de famille
e-post         | email            | E-Mail            | correo           | e-mail
telefon        | phone            | Telefon           | teléfono         | téléphone
pris           | price            | Preis             | precio           | prix
beskrivelse    | description      | Beschreibung      | descripción      | description
opprett/lag    | create           | erstellen         | crear            | créer
slett          | delete           | löschen           | eliminar         | supprimer
oppdater       | update           | aktualisieren     | actualizar       | mettre à jour
send           | send             | senden            | enviar           | envoyer
betal/betaling | payment          | Zahlung           | pago             | paiement

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## FELTFORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Datoer: YYYY-MM-DD
- Pris: desimaltall (1500.00) — ingen valutasymbol
- Nested ID: {"customer": {"id": 123}} — ikke customer_id: 123
- Strenger: employeeNumber, productNumber, departmentNumber, project.number er STRENGER
- PUT body: inkluder ALLTID "id"-feltet og alle andre felt (full-replace!)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## FEILHÅNDTERING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
422: Les validationMessages — forteller hvilket felt som mangler/er feil
404: Sjekk endpoint-format (ikke /v2/employee, bare /employee)
401: Autentiseringsfeil
400: Les error.message for detaljer
Ved feil: korriger og prøv én gang til med riktig data.
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
        endpoint = endpoint[3:]

    url = base_url.rstrip("/") + endpoint
    try:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        kwargs = {"auth": auth, "timeout": 60, "headers": headers}
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

        if isinstance(result, dict):
            result["_http_status"] = resp.status_code
        else:
            result = {"data": result, "_http_status": resp.status_code}

        icon = "✓" if resp.status_code < 300 else "✗"
        print(f"  {icon} {method} {endpoint} → {resp.status_code}")
        if resp.status_code >= 400:
            print(f"    Feil: {resp.text[:400]}")

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
        user_content = []

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

        # Detekter oppgavetype for å gi ekstra hint
        prompt_lower = prompt.lower()
        hints = []

        admin_words = ["administrator", "kontoadministrator", "admin",
                       "administrador", "administrateur", "verwalter"]
        if any(w in prompt_lower for w in admin_words):
            hints.append(
                "⭐ ADMINISTRATOR-OPPGAVE: Gjør KUN to kall:\n"
                "1. POST /employee med firstName, lastName, email\n"
                "2. PUT /employee/{id} med ALLE felt + \"administrator\": true\n"
                "PUT-body EKSEMPEL: {\"id\": 123, \"firstName\": \"Kari\", "
                "\"lastName\": \"Nordmann\", \"email\": \"kari@example.org\", \"administrator\": true}"
            )

        delete_words = ["slett", "delete", "fjern", "löschen", "eliminar", "supprimer"]
        if any(w in prompt_lower for w in delete_words):
            hints.append(
                "SLETT-OPPGAVE: Finn entiteten med GET (søk på navn), "
                "verifiser at det er riktig, deretter DELETE /{entity}/{id}"
            )

        update_words = ["oppdater", "endre", "update", "aktualisier", "actualiz", "mettre à jour"]
        if any(w in prompt_lower for w in update_words):
            hints.append(
                "OPPDATER-OPPGAVE: 1) Finn entiteten med GET, "
                "2) Hent full detalj med GET /{entity}/{id}, "
                "3) PUT med ALLE eksisterende felt + endringene (full-replace!)"
            )

        hint_text = ("\n\n" + "\n\n".join(hints)) if hints else ""

        user_content.append({
            "type": "text",
            "text": (
                f"Oppgave: {prompt}\n\n"
                f"Tripletex base URL: {base_url}"
                f"{hint_text}\n\n"
                "Utfør ALLE steg. Les hvert API-svar nøye. "
                "Navneparsing: 'Kari Nordmann' → firstName='Kari', lastName='Nordmann'. "
                "Endepunkter: /employee, /customer osv – IKKE /v2/employee."
            ),
        })

        # ---- Tool-definisjon ----
        tools = [
            {
                "name": "tripletex_api",
                "description": (
                    "Kall Tripletex v2 REST API. "
                    "Endepunkter: /employee, /customer osv (IKKE /v2/employee). "
                    "PUT er full-replace — inkluder alltid alle felt + id i body."
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
                            "description": "Endepunkt uten /v2, f.eks. /employee, /customer/123, /invoice/456/:send",
                        },
                        "data": {
                            "type": "object",
                            "description": "Request-body for POST/PUT",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query-parametere, f.eks. {\"sendType\": \"EMAIL\", \"fields\": \"id,name\"}",
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
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

            print(f"  Iterasjon {iteration + 1}: stop_reason={response.stop_reason}")
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                print("  Claude ferdig.")
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    inp = block.input
                    print(f"  → {inp.get('method')} {inp.get('endpoint')}")
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
                break

    except Exception as exc:
        import traceback
        print(f"KRITISK FEIL i /solve: {exc}")
        traceback.print_exc()

    return jsonify({"status": "completed"})


# ---------------------------------------------------------------------------
# OPPSTART
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starter Tripletex-agent på port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
