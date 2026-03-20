"""
NM i AI 2026 – Tripletex Agent
One-shot Gemini-planlegging + regelbasert fallback.
1 Gemini-kall per oppgave (ned fra 3-10).
"""

import json
import os
import re
from datetime import date, timedelta
from typing import Optional
import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import google.generativeai as genai

app = FastAPI()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

TODAY = date.today().isoformat()
DUE = (date.today() + timedelta(days=30)).isoformat()

# ---------------------------------------------------------------------------
# TRIPLETEX API
# ---------------------------------------------------------------------------

def tx(method: str, endpoint: str, base_url: str, auth: tuple,
       data: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    if endpoint.startswith("/v2/"):
        endpoint = endpoint[3:]
    url = base_url.rstrip("/") + endpoint
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    kwargs: dict = {"auth": auth, "timeout": 60, "headers": headers}
    if params:
        kwargs["params"] = params
    m = method.upper()
    if m == "GET":
        resp = requests.get(url, **kwargs)
    elif m == "POST":
        resp = requests.post(url, json=data or {}, **kwargs)
    elif m == "PUT":
        resp = requests.put(url, json=data or {}, **kwargs)
    elif m == "DELETE":
        resp = requests.delete(url, **kwargs)
    else:
        return {"error": f"Unknown method: {method}"}

    icon = "✓" if resp.status_code < 300 else "✗"
    print(f"  {icon} {m} {endpoint} → {resp.status_code}")
    if resp.status_code >= 400:
        print(f"    Feil: {resp.text[:400]}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text[:300]}


def get_id(resp: dict) -> Optional[int]:
    try:
        return resp["value"]["id"]
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# ONE-SHOT GEMINI SYSTEM PROMPT
# ---------------------------------------------------------------------------

SYSTEM = f"""Du er en ekspert Tripletex API-agent for NM i AI 2026.
Oppgaver kommer på norsk, nynorsk, engelsk, spansk, tysk, portugisisk eller fransk.
Dagens dato: {TODAY}. Forfallsdato fakturaer: {DUE}.

Returner KUN et gyldig JSON-objekt – ingen annen tekst, ingen markdown.

FORMAT:
{{
  "calls": [
    {{"ref": "c0", "method": "POST", "endpoint": "/customer", "body": {{"name": "Acme AS", "isCustomer": true}}, "params": {{}}}},
    {{"ref": "c1", "method": "POST", "endpoint": "/order", "body": {{"customer": {{"id": "{{c0.id}}"}}}}, "params": {{}}}}
  ]
}}

{{cN.id}} = value.id fra svaret på kallet med ref=cN (for liste-svar: {{cN.values0id}})

## REGLER
- Kontoen er ALLTID TOM. Aldri GET for å søke. Alltid POST for å opprette.
- Bruk {{ref.id}} for ID fra tidligere kall.
- Minimer antall kall.

## NAVNEPARSING (kritisk!)
"Kari Nordmann" → firstName="Kari", lastName="Nordmann"
"Ole Martin Hansen" → firstName="Ole Martin", lastName="Hansen"
ALDRI hele navnet i firstName alene.

## ENDEPUNKTER

ANSATT:
  POST /employee: {{firstName, lastName, email?, phoneNumberMobile?}}

ADMIN-ANSATT (2 kall):
  c0: POST /employee
  c1: PUT /employee/entitlement/:grantEntitlementsByTemplate, body={{}}, params={{"employeeId": "{{c0.id}}", "template": "ALL_PRIVILEGES"}}

KUNDE:
  POST /customer: {{name, isCustomer: true, email?, organizationNumber?}}

LEVERANDØR:
  POST /supplier: {{name, isSupplier: true, email?, organizationNumber?}}

PRODUKT:
  POST /product: {{name, priceExcludingVatCurrency?, productNumber?}}

AVDELING:
  POST /department: {{name, departmentNumber: "42"}}

ORDRE (med ett eller flere produkter):
  c0: POST /customer: {{name, isCustomer: true, organizationNumber?}}
  c1: POST /product: {{name, priceExcludingVatCurrency: pris, productNumber?}}
  c2: POST /product: {{...}} (ett per ekstra produkt)
  c3: POST /order: {{
    customer: {{id: "{{c0.id}}"}},
    orderDate: "{TODAY}",
    orderLines: [
      {{product: {{id: "{{c1.id}}"}}, count: 1.0, unitPriceExcludingVatCurrency: pris}},
      {{product: {{id: "{{c2.id}}"}}, count: 1.0, unitPriceExcludingVatCurrency: pris2}}
    ]
  }}

FAKTURA (med produktlinjer):
  c0: POST /customer
  c1..cN: POST /product (ett per linje)
  cN1: POST /order: {{customer: {{id: "{{c0.id}}"}}, orderDate: "{TODAY}", orderLines: [...]}}
  cN2: POST /invoice: {{invoiceDate: "{TODAY}", invoiceDueDate: "{DUE}", customer: {{id: "{{c0.id}}"}}, orders: [{{id: "{{cN1.id}}"}}]}}

PROSJEKT:
  c0: POST /customer: {{name, isCustomer: true}}
  c1: POST /project: {{name, number: "1001", startDate: "{TODAY}", customer: {{id: "{{c0.id}}"}}}}

REISEREGNING:
  c0: GET /employee, body={{}}, params={{"fields": "id", "count": 1}}
  c1: POST /travelExpense: {{employee: {{id: "{{c0.values0id}}"}}, travelDetails: {{departureDate: "{TODAY}"}}}}
  NB: Liste-svar bruker "values":[{{"id":123}}] ikke "value":{{"id":123}}. Bruk {{cN.values0id}} for dette.
"""


def resolve_refs(obj, results: dict):
    """Erstatt {cN.id} og {cN.values0id} med faktiske verdier fra tidligere kall."""
    if isinstance(obj, str):
        # Hel streng er en ref → returner int
        m = re.fullmatch(r'\{(\w+)\.id\}', obj)
        if m and m.group(1) in results:
            r = results[m.group(1)]
            val = r.get("value", {}) if isinstance(r, dict) else {}
            if isinstance(val, dict) and "id" in val:
                return val["id"]

        m = re.fullmatch(r'\{(\w+)\.values0id\}', obj)
        if m and m.group(1) in results:
            r = results[m.group(1)]
            vals = r.get("values", []) if isinstance(r, dict) else []
            if vals and isinstance(vals[0], dict) and "id" in vals[0]:
                return vals[0]["id"]

        # Ref innebygd i streng → erstatt med streng-versjon
        def sub(match):
            ref, key = match.group(1), match.group(2)
            if ref not in results:
                return match.group(0)
            r = results[ref]
            if key == "id":
                val = r.get("value", {}) if isinstance(r, dict) else {}
                return str(val.get("id", match.group(0)))
            if key == "values0id":
                vals = r.get("values", []) if isinstance(r, dict) else []
                return str(vals[0]["id"]) if vals else match.group(0)
            return match.group(0)

        return re.sub(r'\{(\w+)\.(id|values0id)\}', sub, obj)

    elif isinstance(obj, dict):
        return {k: resolve_refs(v, results) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_refs(item, results) for item in obj]
    return obj


def get_gemini_plan(prompt: str) -> Optional[list]:
    """Kall Gemini én gang og få tilbake en plan med API-kall."""
    if not GEMINI_API_KEY:
        print("  Ingen Gemini API-nøkkel – bruker regelbasert fallback")
        return None
    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM,
        )
        response = model.generate_content(f"Oppgave: {prompt}")
        text = response.text.strip()
        print(f"  Gemini svar (første 200 tegn): {text[:200]}")
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            data = json.loads(m.group())
            calls = data.get("calls", [])
            print(f"  Gemini plan: {len(calls)} kall")
            return calls
    except Exception as e:
        print(f"  Gemini feil: {e}")
    return None


def execute_plan(calls: list, base_url: str, auth: tuple):
    results = {}
    for call in calls:
        ref = call.get("ref", f"c{len(results)}")
        method = call.get("method", "POST")
        endpoint = call.get("endpoint", "")
        body = resolve_refs(call.get("body") or {}, results)
        params = resolve_refs(call.get("params") or {}, results)
        result = tx(method, endpoint, base_url, auth,
                    data=body if body else None,
                    params=params if params else None)
        results[ref] = result


# ---------------------------------------------------------------------------
# REGELBASERT FALLBACK
# ---------------------------------------------------------------------------

def extract_email(text: str) -> Optional[str]:
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w+', text)
    return m.group() if m else None


def extract_phone(text: str) -> Optional[str]:
    m = re.search(r'\b([49]\d{7})\b', text)
    return m.group() if m else None


def extract_org_number(text: str) -> Optional[str]:
    m = re.search(r'\b(\d{9})\b', text)
    return m.group() if m else None


def extract_name(text: str) -> tuple:
    STOP = {
        'Opprett', 'Create', 'Crea', 'Erstelle', 'Créez', 'En', 'Et', 'Ein', 'Eit',
        'Une', 'Un', 'Nuevo', 'Nueva', 'Neue', 'Ansatt', 'Employee', 'Kunde',
        'Customer', 'Med', 'Named', 'With', 'Og', 'Tilsett', 'Medarbeider',
    }
    m = re.search(
        r'(?:med\s+navn|med\s+namn|named?\s*(?:is\s*)?|called?\s*(?:is\s*)?|namens|'
        r'llamad[oa]|appel[eé][e]?|com\s+nome|nommé[e]?|mit\s+namen)\s+'
        r'([A-ZÆØÅ][a-zæøåéèêëàâîïôùûüç]+(?:\s+[A-ZÆØÅ][a-zæøåéèêëàâîïôùûüç]+)+)',
        text, re.IGNORECASE
    )
    if m:
        parts = m.group(1).split()
        return " ".join(parts[:-1]), parts[-1]
    words = [w for w in re.findall(r'\b[A-ZÆØÅ][a-zæøåéèêëàâîïôùûüç]+\b', text) if w not in STOP]
    if len(words) >= 2:
        return words[0], words[1]
    if len(words) == 1:
        return words[0], "Person"
    return "Ukjent", "Person"


def extract_company_name(text: str) -> Optional[str]:
    m = re.search(r"['\"]([^'\"]{2,60})['\"]", text)
    if m:
        return m.group(1).strip()
    m = re.search(r'\b([A-ZÆØÅ][\w\s&.-]{1,40}(?:\s+AS|\s+Ltd|\s+GmbH|\s+SAS|\s+BV|\s+SARL))\b', text)
    if m:
        return m.group(1).strip()
    return None


def is_admin(prompt: str) -> bool:
    p = prompt.lower()
    return any(k in p for k in ['administrator', 'kontoadministrator', 'admin',
                                  'alle rettigheter', 'all privileges', 'administrateur',
                                  'administrador', 'administrativ'])


def detect_task(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ['faktura', 'invoice', 'rechnung', 'facture', 'factura', 'fatura']):
        return 'invoice'
    if any(k in p for k in ['ordre', 'order', 'bestilling']):
        return 'order'
    if any(k in p for k in ['reiseregning', 'travel expense', 'reiseutlegg', 'note de frais']):
        return 'travel_expense'
    if any(k in p for k in ['avdeling', 'department', 'abteilung', 'département']):
        return 'department'
    if any(k in p for k in ['leverandør', 'supplier', 'lieferant', 'fournisseur', 'proveedor']):
        return 'supplier'
    if any(k in p for k in ['produkt', 'product', 'produit', 'producto', 'vare']):
        return 'product'
    if any(k in p for k in ['prosjekt', 'project', 'projekt', 'projet', 'proyecto']):
        return 'project'
    if any(k in p for k in ['kunde', 'customer', 'client', 'klient', 'cliente', 'nouveau client']):
        return 'customer'
    if any(k in p for k in ['ansatt', 'employee', 'medarbeider', 'mitarbeiter', 'employé',
                              'empleado', 'funcionário', 'tilsett']):
        return 'employee'
    if extract_email(prompt):
        return 'employee'
    return 'employee'


def rule_based_solve(prompt: str, base_url: str, auth: tuple):
    task = detect_task(prompt)
    print(f"  Regelbasert: {task}")

    if task == "employee":
        first, last = extract_name(prompt)
        body: dict = {"firstName": first, "lastName": last}
        email = extract_email(prompt)
        phone = extract_phone(prompt)
        if email:
            body["email"] = email
        if phone:
            body["phoneNumberMobile"] = phone
        result = tx("POST", "/employee", base_url, auth, body)
        emp_id = get_id(result)
        if emp_id and is_admin(prompt):
            tx("PUT", "/employee/entitlement/:grantEntitlementsByTemplate",
               base_url, auth,
               params={"employeeId": emp_id, "template": "ALL_PRIVILEGES"})

    elif task == "customer":
        name = extract_company_name(prompt)
        if not name:
            first, last = extract_name(prompt)
            name = f"{first} {last}".strip()
        body = {"name": name, "isCustomer": True}
        email = extract_email(prompt)
        org = extract_org_number(prompt)
        if email:
            body["email"] = email
        if org:
            body["organizationNumber"] = org
        tx("POST", "/customer", base_url, auth, body)

    elif task == "supplier":
        name = extract_company_name(prompt)
        if not name:
            first, last = extract_name(prompt)
            name = f"{first} {last}".strip()
        body = {"name": name, "isSupplier": True}
        org = extract_org_number(prompt)
        if org:
            body["organizationNumber"] = org
        tx("POST", "/supplier", base_url, auth, body)

    elif task == "product":
        m = re.search(r"['\"]([^'\"]+)['\"]", prompt)
        name = m.group(1) if m else "Produkt"
        body = {"name": name}
        m2 = re.search(r'(\d[\d\s]*[.,]?\d*)\s*(?:kr|NOK)', prompt, re.IGNORECASE)
        if m2:
            body["priceExcludingVatCurrency"] = float(m2.group(1).replace(',', '.'))
        tx("POST", "/product", base_url, auth, body)

    elif task == "department":
        m = re.search(r"['\"]([^'\"]+)['\"]", prompt)
        name = m.group(1) if m else "Avdeling"
        m2 = re.search(r'\b(\d{1,4})\b', prompt)
        dept_nr = m2.group(1) if m2 else "1"
        tx("POST", "/department", base_url, auth, {"name": name, "departmentNumber": dept_nr})

    elif task in ("invoice", "order"):
        name = extract_company_name(prompt)
        if not name:
            name = "Fakturakunde AS"
        body = {"name": name, "isCustomer": True}
        org = extract_org_number(prompt)
        if org:
            body["organizationNumber"] = org
        r = tx("POST", "/customer", base_url, auth, body)
        cust_id = get_id(r)
        if not cust_id:
            return
        r = tx("POST", "/order", base_url, auth, {
            "customer": {"id": cust_id},
            "orderDate": TODAY,
        })
        order_id = get_id(r)
        if task == "invoice" and order_id:
            tx("POST", "/invoice", base_url, auth, {
                "invoiceDate": TODAY,
                "invoiceDueDate": DUE,
                "customer": {"id": cust_id},
                "orders": [{"id": order_id}],
            })

    elif task == "project":
        m = re.search(r"['\"]([^'\"]+)['\"]", prompt)
        name = m.group(1) if m else "Prosjekt"
        company = extract_company_name(prompt)
        cust_body: dict = {"name": company or "Prosjektkunde AS", "isCustomer": True}
        r = tx("POST", "/customer", base_url, auth, cust_body)
        cust_id = get_id(r)
        proj_body: dict = {"name": name, "number": "1001", "startDate": TODAY}
        if cust_id:
            proj_body["customer"] = {"id": cust_id}
        tx("POST", "/project", base_url, auth, proj_body)

    elif task == "travel_expense":
        r = tx("GET", "/employee", base_url, auth,
               params={"fields": "id", "count": 1})
        vals = r.get("values", [])
        if vals:
            tx("POST", "/travelExpense", base_url, auth, {
                "employee": {"id": vals[0]["id"]},
                "travelDetails": {"departureDate": TODAY},
            })


# ---------------------------------------------------------------------------
# FASTAPI RUTER
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "gemini_key": bool(GEMINI_API_KEY)}


@app.post("/solve")
async def solve(request_body: dict):
    try:
        prompt: str = request_body.get("prompt", "")
        creds: dict = request_body.get("tripletex_credentials", {})
        base_url: str = creds.get("base_url", "").rstrip("/")
        auth = ("0", creds.get("session_token", ""))

        print(f"\n=== OPPGAVE ===\n{prompt}")

        # Prøv Gemini one-shot
        calls = get_gemini_plan(prompt)
        if calls:
            execute_plan(calls, base_url, auth)
        else:
            # Regelbasert fallback
            rule_based_solve(prompt, base_url, auth)

    except Exception as exc:
        import traceback
        print(f"FEIL: {exc}")
        traceback.print_exc()

    return JSONResponse({"status": "completed"})
