"""
NM i AI 2026 – Tripletex Agent
Regelbasert parsing + direkte API-kall. Krever ingen LLM/API-nøkkel.
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

# ---------------------------------------------------------------------------
# HELPERS – TRIPLETEX API
# ---------------------------------------------------------------------------

def tx(method: str, endpoint: str, base_url: str, auth: tuple,
       data: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    """Gjør ett Tripletex API-kall og returner JSON-svaret."""
    if endpoint.startswith("/v2/"):
        endpoint = endpoint[3:]
    url = base_url.rstrip("/") + endpoint
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    kwargs = {"auth": auth, "headers": headers, "timeout": 60}
    if params:
        kwargs["params"] = params
    method = method.upper()
    try:
        if method == "GET":
            resp = requests.get(url, **kwargs)
        elif method == "POST":
            resp = requests.post(url, json=data or {}, **kwargs)
        elif method == "PUT":
            resp = requests.put(url, json=data or {}, **kwargs)
        elif method == "DELETE":
            resp = requests.delete(url, **kwargs)
        else:
            return {"error": "ukjent metode"}
        icon = "✓" if resp.status_code < 300 else "✗"
        print(f"  {icon} {method} {endpoint} → {resp.status_code}")
        if resp.status_code >= 400:
            print(f"    {resp.text[:300]}")
        try:
            return resp.json()
        except Exception:
            return {"status_code": resp.status_code, "text": resp.text[:200]}
    except Exception as e:
        print(f"  FEIL {method} {endpoint}: {e}")
        return {"error": str(e)}

def val(result: dict) -> Optional[dict]:
    """Hent value-objekt fra Tripletex-respons."""
    return result.get("value") if isinstance(result, dict) else None

def get_id(result: dict) -> Optional[int]:
    v = val(result)
    return v.get("id") if v else None

# ---------------------------------------------------------------------------
# HELPERS – REGEX-BASERT FELTUTHENTING
# ---------------------------------------------------------------------------

def extract_email(text: str) -> Optional[str]:
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w{2,}', text)
    return m.group(0) if m else None

def extract_phone(text: str) -> Optional[str]:
    m = re.search(r'\b([\+]?[0-9]{8,12})\b', text)
    return m.group(1) if m else None

def extract_orgnr(text: str) -> Optional[str]:
    m = re.search(r'\b(\d{9})\b', text)
    return m.group(1) if m else None

def extract_price(text: str) -> Optional[float]:
    # Match tall med komma/punktum som desimalskilletegn
    m = re.search(r'\b(\d{1,7}(?:[.,]\d{1,2})?)\s*(?:kr|krone|kroner|NOK|couronnes|coronas|Kronen)?\b', text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(',', '.'))
    return None

def extract_date(text: str) -> Optional[str]:
    # ISO-format
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
    if m:
        return m.group(1)
    # DD.MM.YYYY
    m = re.search(r'\b(\d{2})\.(\d{2})\.(\d{4})\b', text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return "2026-03-20"

def extract_number_str(text: str, label_patterns: list) -> Optional[str]:
    """Finn et tall etter ett av label_patterns."""
    for pat in label_patterns:
        m = re.search(pat + r'[\s:]*(\d+)', text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None

def extract_name(text: str) -> tuple:
    """
    Prøver å finne fornavn og etternavn fra teksten.
    Returnerer (firstName, lastName) eller (None, None).
    """
    patterns = [
        # "navn X Y", "name X Y", "named X Y", "namn X Y", "llamado X Y"
        r'(?:navn|name|named|namn|llamado|appelé|namens|tilsett)\s+([A-ZÆØÅ][a-zæøåé]+(?:\s[A-ZÆØÅ][a-zæøåé]+)*)',
        # "ansatt/employee/Mitarbeiter X Y"
        r'(?:ansatt|employee|Mitarbeiter|empleado|employé)\s+(?:med navn\s+)?([A-ZÆØÅ][a-zæøåé]+(?:\s[A-ZÆØÅ][a-zæøåé]+)*)',
        # "med navn X Y"
        r'med\s+navn\s+([A-ZÆØÅ][a-zæøåé]+(?:\s[A-ZÆØÅ][a-zæøåé]+)*)',
        # Fallback: to store bokstaver i teksten
        r'\b([A-ZÆØÅ][a-zæøåé]+)\s+([A-ZÆØÅ][a-zæøåé]+)\b',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            full = m.group(1).strip()
            parts = full.split()
            if len(parts) >= 2:
                return " ".join(parts[:-1]), parts[-1]
            elif len(parts) == 1:
                # Prøv neste match
                continue
    return None, None

def extract_company_name(text: str) -> Optional[str]:
    """Finn firmanavn (typisk inneholder AS, ASA, Ltd osv.)"""
    patterns = [
        r'(?:kunde|customer|leverandør|supplier|klient|client|Kunde|Lieferant|fournisseur|proveedor)\s+(?:med navn\s+)?["\']?([A-ZÆØÅ][^,\n"\']{2,50}?(?:AS|ASA|Ltd|GmbH|AB|BV|SRL|Inc)?)["\']?(?:\s*,|\s+med|\s+og|\s*$)',
        r'(?:kalt|called|heißt|llamado|appelé|namens)\s+["\']?([A-ZÆØÅ][^,\n"\']{2,50})["\']?',
        r'["\']([A-ZÆØÅ][^"\']{3,50}(?:AS|ASA|Ltd|GmbH)?)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

def extract_product_name(text: str) -> Optional[str]:
    patterns = [
        r'(?:produkt|product|Produkt|produit|producto)\s+(?:med navn\s+)?["\']?([A-ZÆØÅa-zæøå][^,\n"\']{2,60})["\']?(?:\s*,|\s+med|\s+pris|\s+og|\s*$)',
        r'(?:kalt|called|appelé|llamado|namens)\s+["\']?([^,\n"\']{3,60})["\']?',
        r'["\']([^"\']{3,60})["\']',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

def extract_department_name(text: str) -> Optional[str]:
    patterns = [
        r'(?:avdeling|department|Abteilung|département|departamento)\s+(?:med navn\s+)?["\']?([A-ZÆØÅa-zæøå][^,\n"\']{2,60})["\']?(?:\s*,|\s+med|\s+nummer|\s*$)',
        r'(?:kalt|called|namens|appelée?|llamad[ao])\s+["\']?([^,\n"\']{3,60})["\']?',
        r'["\']([^"\']{3,60})["\']',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

def is_admin_task(text: str) -> bool:
    keywords = ["administrator", "kontoadministrator", "admin",
                 "administrador", "administrateur", "verwalter",
                 "account administrator", "konto administrator"]
    return any(k in text.lower() for k in keywords)

def detect_task_type(text: str) -> str:
    t = text.lower()
    # Sjekk i prioritert rekkefølge
    if any(k in t for k in ["reiseregning", "travel expense", "reisekostenabr", "gasto de viaje", "note de frais"]):
        return "travelExpense"
    if any(k in t for k in ["faktura", "invoice", "rechnung", "factura", "facture"]):
        return "invoice"
    if any(k in t for k in ["ordre", "order", "bestellung", "pedido", "commande"]):
        return "order"
    if any(k in t for k in ["prosjekt", "project", "projekt", "proyecto", "projet"]):
        return "project"
    if any(k in t for k in ["avdeling", "department", "abteilung", "département", "departamento"]):
        return "department"
    if any(k in t for k in ["leverandør", "supplier", "lieferant", "proveedor", "fournisseur"]):
        return "supplier"
    if any(k in t for k in ["produkt", "product", "producto", "produit"]):
        return "product"
    if any(k in t for k in ["kunde", "customer", "klient", "client", "kunde"]):
        return "customer"
    if any(k in t for k in ["ansatt", "employee", "medarbeider", "mitarbeiter", "empleado", "employé", "tilsett", "tilsatt"]):
        return "employee"
    # Fallback: se etter navn/epost = trolig ansatt
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
        print("  Kunne ikke parse navn fra prompt")
        return

    body = {"firstName": first, "lastName": last}
    if email:
        body["email"] = email
    if phone:
        body["phoneNumberMobile"] = phone

    result = tx("POST", "/employee", base_url, auth, body)
    emp_id = get_id(result)
    print(f"  Ansatt opprettet: id={emp_id}, {first} {last}")

    if emp_id and is_admin_task(prompt):
        # Korrekt måte å sette administrator i Tripletex:
        # PUT /employee/entitlement/:grantEntitlementsByTemplate?employeeId=X&template=ALL_PRIVILEGES
        tx("PUT", "/employee/entitlement/:grantEntitlementsByTemplate",
           base_url, auth, params={"employeeId": emp_id, "template": "ALL_PRIVILEGES"})
        print(f"  Administrator-rolle (ALL_PRIVILEGES) satt for {first} {last}")


def handle_customer(prompt: str, base_url: str, auth: tuple):
    name = extract_company_name(prompt)
    if not name:
        # Fallback: bruk personnavn
        first, last = extract_name(prompt)
        name = f"{first} {last}" if first else "Ukjent Kunde AS"

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

    # Adresse
    city_m = re.search(r'\b(Oslo|Bergen|Trondheim|Stavanger|Tromsø|Kristiansand|Drammen|Fredrikstad|Sandnes|Bodø)\b', prompt, re.IGNORECASE)
    street_m = re.search(r'(?:adresse|address|Adresse)\s+([A-ZÆØÅ][^,\n]{3,40})', prompt, re.IGNORECASE)
    postal_m = re.search(r'\b(\d{4})\b', prompt)

    if city_m or street_m:
        addr = {"country": {"id": 1}}
        if street_m:
            addr["addressLine1"] = street_m.group(1).strip()
        if city_m:
            addr["city"] = city_m.group(1)
        if postal_m:
            addr["postalCode"] = postal_m.group(1)
        body["physicalAddress"] = addr

    result = tx("POST", "/customer", base_url, auth, body)
    print(f"  Kunde opprettet: id={get_id(result)}, {name}")


def handle_supplier(prompt: str, base_url: str, auth: tuple):
    name = extract_company_name(prompt)
    if not name:
        first, last = extract_name(prompt)
        name = f"{first} {last}" if first else "Ukjent Leverandør AS"

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


def handle_product(prompt: str, base_url: str, auth: tuple):
    name = extract_product_name(prompt)
    if not name:
        name = "Produkt"

    price = extract_price(prompt)
    prodnr_m = re.search(r'(?:produktnummer|product.?number|Produktnummer|numéro)[:\s]+([A-Za-z0-9-]+)', prompt, re.IGNORECASE)
    if not prodnr_m:
        prodnr_m = re.search(r'\b(P\d{2,6})\b', prompt)

    body = {"name": name}
    if price:
        body["priceExcludingVatCurrency"] = price
    if prodnr_m:
        body["productNumber"] = prodnr_m.group(1)

    desc_m = re.search(r'(?:beskrivelse|description|Beschreibung|descripción)[:\s]+([^,\n]{3,100})', prompt, re.IGNORECASE)
    if desc_m:
        body["description"] = desc_m.group(1).strip()

    result = tx("POST", "/product", base_url, auth, body)
    print(f"  Produkt opprettet: id={get_id(result)}, {name}")


def handle_department(prompt: str, base_url: str, auth: tuple):
    name = extract_department_name(prompt)
    if not name:
        name = "Ny avdeling"

    deptnr = extract_number_str(prompt, [
        r'avdelingsnummer', r'department.?number', r'Abteilungsnummer',
        r'numéro', r'número', r'nummer'
    ])
    if not deptnr:
        deptnr = str(random.randint(10, 99))

    body = {"name": name, "departmentNumber": deptnr}
    result = tx("POST", "/department", base_url, auth, body)
    print(f"  Avdeling opprettet: id={get_id(result)}, {name}")


def handle_project(prompt: str, base_url: str, auth: tuple):
    # Finn prosjektnavn
    name_m = re.search(r'(?:prosjekt|project|Projekt|projet|proyecto)\s+(?:kalt\s+|named\s+|appelé\s+)?["\']?([A-ZÆØÅa-zæøå][^,\n"\']{2,60})["\']?', prompt, re.IGNORECASE)
    name = name_m.group(1).strip() if name_m else "Nytt prosjekt"

    date = extract_date(prompt)
    end_m = re.search(r'(?:sluttdato|end.?date|Enddatum)[:\s]+(\d{4}-\d{2}-\d{2})', prompt, re.IGNORECASE)

    projnr = extract_number_str(prompt, [r'prosjektnummer', r'project.?number', r'nummer'])
    if not projnr:
        projnr = str(random.randint(1000, 9999))

    # Trenger kunde — søk etter eksisterende eller opprett dummy
    cust_result = tx("GET", "/customer", base_url, auth, params={"count": 1, "fields": "id,name"})
    cust_values = cust_result.get("values", []) if isinstance(cust_result, dict) else []
    if cust_values:
        cust_id = cust_values[0]["id"]
    else:
        # Opprett en dummy-kunde
        c = tx("POST", "/customer", base_url, auth, {"name": "Prosjektkunde AS", "isCustomer": True})
        cust_id = get_id(c) or 0

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


def handle_travel_expense(prompt: str, base_url: str, auth: tuple):
    date = extract_date(prompt)
    desc_m = re.search(r'(?:beskrivelse|description|Beschreibung|descripción)[:\s]+([^,\n]{3,100})', prompt, re.IGNORECASE)
    desc = desc_m.group(1).strip() if desc_m else "Reise"

    # Finn eller opprett ansatt
    first, last = extract_name(prompt)
    emp_id = None

    if first and last:
        search = tx("GET", "/employee", base_url, auth,
                    params={"firstName": first, "lastName": last, "fields": "id,firstName,lastName", "count": 5})
        vals = search.get("values", []) if isinstance(search, dict) else []
        if vals:
            emp_id = vals[0]["id"]

    if not emp_id:
        # Hent første ansatt
        search = tx("GET", "/employee", base_url, auth, params={"fields": "id,firstName", "count": 1})
        vals = search.get("values", []) if isinstance(search, dict) else []
        if vals:
            emp_id = vals[0]["id"]

    if not emp_id:
        print("  Ingen ansatt funnet for reiseregning")
        return

    body = {
        "employee": {"id": emp_id},
        "description": desc,
        "travelDetails": {"departureDate": date}
    }
    result = tx("POST", "/travelExpense", base_url, auth, body)
    print(f"  Reiseregning opprettet: id={get_id(result)}")


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

        # Håndter vedlagte filer
        for f in files:
            try:
                data = base64.b64decode(f.get("content_base64", ""))
                Path(f["filename"]).write_bytes(data)
            except Exception:
                pass

        task = detect_task_type(prompt)
        print(f"  Oppgavetype: {task}")

        if task == "employee":
            handle_employee(prompt, base_url, auth)
        elif task == "customer":
            handle_customer(prompt, base_url, auth)
        elif task == "supplier":
            handle_supplier(prompt, base_url, auth)
        elif task == "product":
            handle_product(prompt, base_url, auth)
        elif task == "department":
            handle_department(prompt, base_url, auth)
        elif task == "project":
            handle_project(prompt, base_url, auth)
        elif task == "travelExpense":
            handle_travel_expense(prompt, base_url, auth)
        else:
            print(f"  Ukjent oppgavetype for: {prompt[:100]}")

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
