"""
NM i AI 2026 – Tripletex Agent
Regelbasert agent basert på competition docs.
"""

import re
import os
import json
import base64
import random
from pathlib import Path
from typing import Optional
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TODAY = "2026-03-20"

# ---------------------------------------------------------------------------
# TRIPLETEX API WRAPPER
# ---------------------------------------------------------------------------

def tx(method: str, endpoint: str, base_url: str, auth: tuple,
       data: Optional[dict] = None, params: Optional[dict] = None):
    """Gjør ett Tripletex API-kall. Returnerer JSON eller None ved feil."""
    if endpoint.startswith("/v2/"):
        endpoint = endpoint[3:]
    url = base_url.rstrip("/") + endpoint
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    kw = {"auth": auth, "headers": headers, "timeout": 60}
    if params:
        kw["params"] = params
    method = method.upper()
    try:
        if method == "GET":
            resp = requests.get(url, **kw)
        elif method == "POST":
            resp = requests.post(url, json=data or {}, **kw)
        elif method == "PUT":
            if data is not None:
                resp = requests.put(url, json=data, **kw)
            else:
                resp = requests.put(url, **kw)
        elif method == "DELETE":
            resp = requests.delete(url, **kw)
        else:
            return None
        icon = "✓" if resp.status_code < 300 else "✗"
        print(f"  {icon} {method} {endpoint} → {resp.status_code}")
        if resp.status_code >= 400:
            print(f"    {resp.text[:300]}")
        try:
            return resp.json()
        except Exception:
            return {"_status": resp.status_code}
    except Exception as e:
        print(f"  FEIL {method} {endpoint}: {e}")
        return None

def get_id(result) -> Optional[int]:
    if isinstance(result, dict):
        v = result.get("value")
        if isinstance(v, dict):
            return v.get("id")
    return None

def get_values(result) -> list:
    if isinstance(result, dict):
        return result.get("values", [])
    return []

# ---------------------------------------------------------------------------
# TEKSTPARSING
# ---------------------------------------------------------------------------

def extract_email(text: str) -> Optional[str]:
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w{2,}', text)
    return m.group(0) if m else None

def extract_phone(text: str) -> Optional[str]:
    m = re.search(r'(?<!\d)(\+?[0-9]{8,12})(?!\d)', text)
    return m.group(1) if m else None

def extract_orgnr(text: str) -> Optional[str]:
    m = re.search(r'\b(\d{9})\b', text)
    return m.group(1) if m else None

def extract_price(text: str) -> Optional[float]:
    # Match tall IKKE 9 siffer (orgnr) - f.eks. 1500, 1500.00, 1 500
    matches = re.findall(r'\b(\d{1,3}(?:[ .]\d{3})*(?:[.,]\d{1,2})?|\d{1,6}(?:[.,]\d{1,2})?)\b', text)
    for m in matches:
        clean = m.replace(' ', '').replace('.', '').replace(',', '.')
        try:
            val = float(clean)
            if 1 <= val <= 999999 and len(str(int(val))) != 9:
                return val
        except Exception:
            pass
    return None

def extract_date(text: str) -> str:
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{2})[./](\d{2})[./](\d{4})\b', text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return TODAY

def extract_name(text: str) -> tuple:
    """Returner (firstName, lastName) fra tekst."""
    patterns = [
        r'(?:med navn|named?|namn|llamad[ao]|appelé[e]?|namens|mit Namen|tilsett)\s+([A-ZÆØÅ][a-zæøåé]+(?:[ -][A-ZÆØÅ][a-zæøåé]+)*)',
        r'(?:ansatt|employee|Mitarbeiter|empleado|employé[e]?)\s+(?:ved navn\s+|med navn\s+)?([A-ZÆØÅ][a-zæøåé]+(?:\s[A-ZÆØÅ][a-zæøåé]+)+)',
        r'\b([A-ZÆØÅ][a-zæøåé]+\s+[A-ZÆØÅ][a-zæøåé]+(?:\s+[A-ZÆØÅ][a-zæøåé]+)?)\b(?=.*@)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            parts = m.group(1).strip().split()
            if len(parts) >= 2:
                return " ".join(parts[:-1]), parts[-1]
    # Fallback: finn to store ord
    m = re.search(r'\b([A-ZÆØÅ][a-zæøåé]+)\s+([A-ZÆØÅ][a-zæøåé]+)\b', text)
    if m:
        return m.group(1), m.group(2)
    return None, None

def extract_quoted_or_named(text: str, entity_words: list) -> Optional[str]:
    """Finn navn i anførselstegn eller etter entity-ord."""
    # Anførselstegn først
    m = re.search(r'["\u201c\u2018]([^"\u201d\u2019]{2,80})["\u201d\u2019]', text)
    if m:
        return m.group(1).strip()
    # Etter entity-ord
    pattern = '|'.join(re.escape(w) for w in entity_words)
    m = re.search(r'(?:' + pattern + r')\s+(?:med navn\s+|kalt\s+|named?\s+|appelée?\s+|llamad[ao]\s+|namens\s+)?([A-ZÆØÅ0-9][^,\n]{2,60}?)(?:\s*,|\s+med|\s+og|\s+epost|\s+e-post|\s*$)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None

def is_admin(text: str) -> bool:
    words = ["administrator", "kontoadministrator", "admin",
             "administrador", "administrateur", "verwalter",
             "account administrator", "all privileges", "alle rettigheter"]
    return any(w in text.lower() for w in words)

def detect_task(text: str) -> str:
    t = text.lower()
    # Slett/delete
    if any(w in t for w in ["slett", "delete", "fjern", "löschen", "eliminar", "supprimer", "reverser", "kreditnota", "credit note"]):
        if any(w in t for w in ["reiseregning", "travel", "expense", "reisekostenabr"]):
            return "delete_travel"
        if any(w in t for w in ["faktura", "invoice", "rechnung", "factura", "facture"]):
            return "credit_note"
        return "delete_generic"
    # Ordre + faktura
    if any(w in t for w in ["faktura", "invoice", "rechnung", "factura", "facture"]):
        if any(w in t for w in ["betal", "payment", "zahlung", "pago", "paiement"]):
            return "invoice_payment"
        return "invoice"
    # Betaling
    if any(w in t for w in ["betal", "payment", "zahlung", "pago", "paiement"]):
        return "invoice_payment"
    # Reiseregning
    if any(w in t for w in ["reiseregning", "travel expense", "reisekostenabr", "gasto de viaje", "note de frais", "reise"]):
        return "travel"
    # Prosjekt
    if any(w in t for w in ["prosjekt", "project", "projekt", "proyecto", "projet"]):
        return "project"
    # Avdeling
    if any(w in t for w in ["avdeling", "department", "abteilung", "département", "departamento"]):
        return "department"
    # Leverandør
    if any(w in t for w in ["leverandør", "supplier", "lieferant", "proveedor", "fournisseur"]):
        return "supplier"
    # Produkt
    if any(w in t for w in ["produkt", "vare", "product", "producto", "produit", "artikel"]):
        return "product"
    # Kunde
    if any(w in t for w in ["kunde", "customer", "klient", "client", "kunder"]):
        return "customer"
    # Ansatt (inkl. oppdater)
    if any(w in t for w in ["ansatt", "employee", "medarbeider", "mitarbeiter", "empleado",
                              "employé", "tilsett", "tilsatt", "arbeidstaker"]):
        return "employee"
    # Fallback: epost = ansatt
    if extract_email(text):
        return "employee"
    return "unknown"

# ---------------------------------------------------------------------------
# OPPGAVEHANDLERE
# ---------------------------------------------------------------------------

def handle_employee(prompt: str, base_url: str, auth: tuple):
    first, last = extract_name(prompt)
    email = extract_email(prompt)
    phone = extract_phone(prompt)

    if not first or not last:
        print(f"  Kunne ikke parse navn — prøver fallback")
        return

    body = {"firstName": first, "lastName": last}
    if email:
        body["email"] = email
    if phone:
        body["phoneNumberMobile"] = phone

    result = tx("POST", "/employee", base_url, auth, body)
    emp_id = get_id(result)
    print(f"  Ansatt opprettet: id={emp_id}, {first} {last}, email={email}")

    if emp_id and is_admin(prompt):
        # Korrekt metode: grantEntitlementsByTemplate med ALL_PRIVILEGES
        tx("PUT", "/employee/entitlement/:grantEntitlementsByTemplate",
           base_url, auth,
           params={"employeeId": emp_id, "template": "ALL_PRIVILEGES"})
        print(f"  Administrator (ALL_PRIVILEGES) satt")


def handle_customer(prompt: str, base_url: str, auth: tuple) -> Optional[int]:
    name = extract_quoted_or_named(prompt, [
        "kunde", "customer", "klient", "client", "Kunde", "Lieferant", "kunden"
    ])
    if not name:
        first, last = extract_name(prompt)
        name = f"{first} {last}" if first else None
    if not name:
        name = "Ny Kunde AS"

    email = extract_email(prompt)
    phone = extract_phone(prompt)
    orgnr = extract_orgnr(prompt)

    body = {"name": name, "isCustomer": True}
    if email:
        body["email"] = email
    if phone:
        body["phoneNumber"] = phone
    if orgnr:
        body["organizationNumber"] = orgnr

    result = tx("POST", "/customer", base_url, auth, body)
    cust_id = get_id(result)
    print(f"  Kunde opprettet: id={cust_id}, {name}")
    return cust_id


def handle_supplier(prompt: str, base_url: str, auth: tuple):
    name = extract_quoted_or_named(prompt, [
        "leverandør", "supplier", "lieferant", "proveedor", "fournisseur"
    ])
    if not name:
        name = "Ny Leverandør AS"

    email = extract_email(prompt)
    phone = extract_phone(prompt)
    orgnr = extract_orgnr(prompt)

    body = {"name": name, "isSupplier": True}
    if email:
        body["email"] = email
    if phone:
        body["phoneNumber"] = phone
    if orgnr:
        body["organizationNumber"] = orgnr

    result = tx("POST", "/supplier", base_url, auth, body)
    print(f"  Leverandør opprettet: id={get_id(result)}, {name}")


def handle_product(prompt: str, base_url: str, auth: tuple) -> Optional[int]:
    name = extract_quoted_or_named(prompt, [
        "produkt", "vare", "product", "producto", "produit", "artikel", "Produkt"
    ])
    if not name:
        name = "Nytt produkt"

    price = extract_price(prompt)

    prodnr_m = re.search(r'(?:produktnummer|product.?number|Produktnummer|numéro|número|nr)[:\s.]+([A-Za-z0-9-]+)', prompt, re.IGNORECASE)
    if not prodnr_m:
        prodnr_m = re.search(r'\b([A-Z]{1,3}\d{2,6})\b', prompt)

    body = {"name": name}
    if price:
        body["priceExcludingVatCurrency"] = price
    if prodnr_m:
        body["productNumber"] = prodnr_m.group(1)

    result = tx("POST", "/product", base_url, auth, body)
    prod_id = get_id(result)
    print(f"  Produkt opprettet: id={prod_id}, {name}, pris={price}")
    return prod_id


def handle_invoice(prompt: str, base_url: str, auth: tuple):
    """
    Flyt fra docs: GET/POST /customer → POST /order → POST /invoice
    På fresh account: må opprette kunde og produkt/ordre
    """
    date = extract_date(prompt)
    due_date_m = re.search(r'(?:forfallsdato|due.?date|Fälligkeit|fecha.?vencimiento|échéance)[:\s]+(\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4})', prompt, re.IGNORECASE)

    # Forfallsdato: 30 dager etter fakturadato
    due_date = due_date_m.group(1) if due_date_m else "2026-04-19"

    # Opprett kunde
    cust_name = extract_quoted_or_named(prompt, ["kunde", "customer", "klient", "client"])
    if not cust_name:
        first, last = extract_name(prompt)
        cust_name = f"{first} {last}" if first else "Fakturakunde AS"

    email = extract_email(prompt)
    cust_body = {"name": cust_name, "isCustomer": True}
    if email:
        cust_body["email"] = email

    cust_result = tx("POST", "/customer", base_url, auth, cust_body)
    cust_id = get_id(cust_result)
    if not cust_id:
        print("  Kunde-oppretting feilet")
        return

    # Opprett ordre med ordrelinje
    price = extract_price(prompt) or 1000.0
    order_body = {
        "customer": {"id": cust_id},
        "orderDate": date,
        "orderLines": [
            {
                "description": "Tjeneste",
                "count": 1.0,
                "unitPriceExcludingVatCurrency": price
            }
        ]
    }
    order_result = tx("POST", "/order", base_url, auth, order_body)
    order_id = get_id(order_result)
    if not order_id:
        print("  Ordre-oppretting feilet")
        return

    # Opprett faktura
    inv_body = {
        "invoiceDate": date,
        "invoiceDueDate": due_date,
        "customer": {"id": cust_id},
        "orders": [{"id": order_id}]
    }
    inv_result = tx("POST", "/invoice", base_url, auth, inv_body)
    inv_id = get_id(inv_result)
    print(f"  Faktura opprettet: id={inv_id}, kunde={cust_id}, ordre={order_id}")

    # Send faktura via e-post hvis nevnt
    if inv_id and any(w in prompt.lower() for w in ["send", "epost", "e-post", "email", "sende"]):
        tx("PUT", f"/invoice/{inv_id}/:send", base_url, auth,
           params={"sendType": "EMAIL"})
        print(f"  Faktura sendt via e-post")


def handle_invoice_payment(prompt: str, base_url: str, auth: tuple):
    """
    Flyt fra docs: POST /customer → POST /invoice → POST /payment
    """
    date = extract_date(prompt)
    price = extract_price(prompt) or 1000.0

    # Opprett kunde
    cust_name = extract_quoted_or_named(prompt, ["kunde", "customer", "klient", "client"])
    if not cust_name:
        first, last = extract_name(prompt)
        cust_name = f"{first} {last}" if first else "Betalingskunde AS"

    email = extract_email(prompt)
    cust_body = {"name": cust_name, "isCustomer": True}
    if email:
        cust_body["email"] = email

    cust_result = tx("POST", "/customer", base_url, auth, cust_body)
    cust_id = get_id(cust_result)
    if not cust_id:
        return

    # Opprett ordre
    order_result = tx("POST", "/order", base_url, auth, {
        "customer": {"id": cust_id},
        "orderDate": date,
        "orderLines": [{"description": "Tjeneste", "count": 1.0,
                        "unitPriceExcludingVatCurrency": price}]
    })
    order_id = get_id(order_result)
    if not order_id:
        return

    # Opprett faktura
    inv_result = tx("POST", "/invoice", base_url, auth, {
        "invoiceDate": date,
        "invoiceDueDate": "2026-04-19",
        "customer": {"id": cust_id},
        "orders": [{"id": order_id}]
    })
    inv_id = get_id(inv_result)
    if not inv_id:
        return

    # Registrer betaling
    pay_result = tx("POST", f"/invoice/{inv_id}/payment", base_url, auth, {
        "paymentDate": date,
        "amount": price,
        "paymentTypeId": 1
    })
    print(f"  Betaling registrert på faktura {inv_id}: {pay_result}")


def handle_travel(prompt: str, base_url: str, auth: tuple):
    """Opprett reiseregning."""
    date = extract_date(prompt)
    first, last = extract_name(prompt)
    desc_m = re.search(r'(?:beskrivelse|description|reise til|travel to|Reise nach)[:\s]+([^\n,]{3,80})', prompt, re.IGNORECASE)
    desc = desc_m.group(1).strip() if desc_m else "Tjenestereise"

    # Finn eller opprett ansatt
    emp_id = None
    if first and last:
        search = tx("GET", "/employee", base_url, auth,
                    params={"firstName": first, "lastName": last,
                            "fields": "id,firstName,lastName", "count": 5})
        vals = get_values(search)
        if vals:
            emp_id = vals[0]["id"]

    if not emp_id:
        # Hent første ansatt
        search = tx("GET", "/employee", base_url, auth,
                    params={"fields": "id,firstName", "count": 1})
        vals = get_values(search)
        if vals:
            emp_id = vals[0]["id"]

    if not emp_id:
        print("  Ingen ansatt funnet for reiseregning")
        return

    result = tx("POST", "/travelExpense", base_url, auth, {
        "employee": {"id": emp_id},
        "description": desc,
        "travelDetails": {"departureDate": date}
    })
    print(f"  Reiseregning opprettet: id={get_id(result)}")


def handle_delete_travel(prompt: str, base_url: str, auth: tuple):
    """Flyt fra docs: GET /travelExpense → DELETE /travelExpense/{id}"""
    result = tx("GET", "/travelExpense", base_url, auth,
                params={"fields": "id,description", "count": 100})
    vals = get_values(result)
    if not vals:
        print("  Ingen reiseregninger å slette")
        return
    # Slett den siste/første
    expense_id = vals[-1]["id"]
    tx("DELETE", f"/travelExpense/{expense_id}", base_url, auth)
    print(f"  Reiseregning slettet: id={expense_id}")


def handle_project(prompt: str, base_url: str, auth: tuple):
    name_m = re.search(r'(?:prosjekt|project|Projekt|projet|proyecto)\s+(?:kalt\s+|named?\s+|appelée?\s+)?["\']?([A-ZÆØÅa-zæøå][^,\n"\']{2,60})["\']?(?:\s*,|\s+med|\s+fra|\s*$)', prompt, re.IGNORECASE)
    name = name_m.group(1).strip() if name_m else extract_quoted_or_named(prompt, ["prosjekt", "project"]) or "Nytt prosjekt"

    date = extract_date(prompt)
    end_m = re.search(r'(?:sluttdato|end.?date|Enddatum|fecha fin)[:\s]+(\d{4}-\d{2}-\d{2})', prompt, re.IGNORECASE)
    projnr = str(random.randint(1000, 9999))

    # Opprett kunde for prosjektet
    cust_name = extract_quoted_or_named(prompt, ["kunde", "customer", "klient"])
    if not cust_name:
        cust_name = "Prosjektkunde AS"

    cust_result = tx("POST", "/customer", base_url, auth, {
        "name": cust_name, "isCustomer": True
    })
    cust_id = get_id(cust_result)
    if not cust_id:
        return

    body = {
        "name": name,
        "number": projnr,
        "startDate": date,
        "customer": {"id": cust_id}
    }
    if end_m:
        body["endDate"] = end_m.group(1)

    result = tx("POST", "/project", base_url, auth, body)
    print(f"  Prosjekt opprettet: id={get_id(result)}, {name}")


def handle_department(prompt: str, base_url: str, auth: tuple):
    name = extract_quoted_or_named(prompt, [
        "avdeling", "department", "Abteilung", "département", "departamento"
    ])
    if not name:
        name = "Ny avdeling"

    deptnr_m = re.search(r'(?:avdelingsnummer|department.?number|Abteilungsnummer|numéro|número|nummer)[:\s]+(\d+)', prompt, re.IGNORECASE)
    deptnr = deptnr_m.group(1) if deptnr_m else str(random.randint(10, 99))

    result = tx("POST", "/department", base_url, auth, {
        "name": name,
        "departmentNumber": deptnr
    })
    print(f"  Avdeling opprettet: id={get_id(result)}, {name}")


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

        for f in files:
            try:
                data = base64.b64decode(f.get("content_base64", ""))
                Path(f["filename"]).write_bytes(data)
            except Exception:
                pass

        task = detect_task(prompt)
        print(f"  Oppgavetype: {task}")

        if task == "employee":
            handle_employee(prompt, base_url, auth)
        elif task == "customer":
            handle_customer(prompt, base_url, auth)
        elif task == "supplier":
            handle_supplier(prompt, base_url, auth)
        elif task == "product":
            handle_product(prompt, base_url, auth)
        elif task == "invoice":
            handle_invoice(prompt, base_url, auth)
        elif task == "invoice_payment":
            handle_invoice_payment(prompt, base_url, auth)
        elif task == "travel":
            handle_travel(prompt, base_url, auth)
        elif task == "delete_travel":
            handle_delete_travel(prompt, base_url, auth)
        elif task == "project":
            handle_project(prompt, base_url, auth)
        elif task == "department":
            handle_department(prompt, base_url, auth)
        else:
            print(f"  Ukjent oppgavetype")

    except Exception as exc:
        import traceback
        print(f"KRITISK FEIL: {exc}")
        traceback.print_exc()

    return jsonify({"status": "completed"})


# ---------------------------------------------------------------------------
# OPPSTART
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starter Tripletex-agent på port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
