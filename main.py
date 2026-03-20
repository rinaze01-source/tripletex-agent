"""
NM i AI 2026 – Tripletex Agent
Regelbasert agent – ingen API-nøkkel nødvendig.
Støtter: norsk, nynorsk, engelsk, spansk, tysk, portugisisk, fransk.
"""

import re
import random
from datetime import date, timedelta
from typing import Optional
import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()


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
        print(f"    Feil: {resp.text[:300]}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text[:200]}


def get_id(resp: dict) -> Optional[int]:
    try:
        return resp["value"]["id"]
    except (KeyError, TypeError):
        return None


def find_or_create_customer(name: str, base_url: str, auth: tuple,
                             org_nr: Optional[str] = None,
                             email: Optional[str] = None) -> Optional[int]:
    """Søk etter kunde, opprett hvis ikke funnet."""
    r = tx("GET", "/customer", base_url, auth,
           params={"name": name, "fields": "id,name", "count": 5})
    values = r.get("values", [])
    if values:
        return values[0]["id"]
    body: dict = {"name": name, "isCustomer": True}
    if org_nr:
        body["organizationNumber"] = org_nr
    if email:
        body["email"] = email
    r = tx("POST", "/customer", base_url, auth, body)
    return get_id(r)


def find_or_create_product(name: str, base_url: str, auth: tuple,
                            price: Optional[float] = None,
                            prod_nr: Optional[str] = None) -> Optional[int]:
    """Søk etter produkt, opprett hvis ikke funnet."""
    r = tx("GET", "/product", base_url, auth,
           params={"name": name, "fields": "id,name", "count": 5})
    values = r.get("values", [])
    if values:
        return values[0]["id"]
    body: dict = {"name": name}
    if price is not None:
        body["priceExcludingVatCurrency"] = price
    if prod_nr:
        body["productNumber"] = prod_nr
    r = tx("POST", "/product", base_url, auth, body)
    return get_id(r)


# ---------------------------------------------------------------------------
# TEKSTUTREKK
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


def extract_price(text: str) -> Optional[float]:
    # "1500.00 kroner/kr/NOK/couronnes/coronas"
    m = re.search(
        r'(\d[\d\s]*[.,]?\d*)\s*(?:kroner?|kr\.?|NOK|couronnes?|coronas?|coroas?|crowns?)',
        text, re.IGNORECASE
    )
    if m:
        raw = m.group(1).replace(' ', '').replace(',', '.')
        try:
            return float(raw)
        except ValueError:
            pass
    # "pris/price/prix/precio/preis/preço X"
    m = re.search(
        r'(?:pris|price|prix|precio|preis|preço)\s+(?:på\s+)?(\d[\d\s]*[.,]?\d*)',
        text, re.IGNORECASE
    )
    if m:
        raw = m.group(1).replace(' ', '').replace(',', '.')
        try:
            return float(raw)
        except ValueError:
            pass
    # "de X couronnes" (French)
    m = re.search(r'de\s+(\d[\d\s]*[.,]?\d*)\s+couronnes', text, re.IGNORECASE)
    if m:
        raw = m.group(1).replace(' ', '').replace(',', '.')
        try:
            return float(raw)
        except ValueError:
            pass
    # "til X kr/NOK" (Norwegian order price)
    m = re.search(r'til\s+(\d[\d\s]*[.,]?\d*)\s*(?:kr|NOK)', text, re.IGNORECASE)
    if m:
        raw = m.group(1).replace(' ', '').replace(',', '.')
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def extract_product_number(text: str) -> Optional[str]:
    m = re.search(
        r'(?:produktnummer|product\s*number|numéro\s*produit|número\s*(?:de\s*)?producto|produktnummer)\s*[:\s]*(P?\d+)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1)
    m = re.search(r'\b(P\d{2,})\b', text)
    if m:
        return m.group(1)
    return None


def extract_department_number(text: str) -> Optional[str]:
    m = re.search(
        r'(?:avdelingsnummer|abteilungsnummer|department\s*number|numéro\s*(?:de\s*)?(?:département|service)|número\s*(?:de\s*)?departamento)\s*[:\s]*(\d+)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1)
    m = re.search(r'(?:mit|with|med)\s+\w*nummer\s+(\d+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def extract_name(text: str) -> tuple:
    """Extract (firstName, lastName) from multilingual prompts."""
    m = re.search(
        r'(?:med\s+navn|med\s+namn|named?\s*(?:is\s*)?|called?\s*(?:is\s*)?|namens|'
        r'llamad[oa]|appel[eé][e]?|com\s+nome|nommé[e]?|mit\s+namen)\s+'
        r'([A-ZÆØÅ][a-zæøåéèêëàâîïôùûüç]+(?:\s+[A-ZÆØÅ][a-zæøåéèêëàâîïôùûüç]+)+)',
        text, re.IGNORECASE
    )
    if m:
        parts = m.group(1).split()
        return " ".join(parts[:-1]), parts[-1]

    m = re.search(
        r'["\']([A-ZÆØÅ][a-zæøå]+(?:\s+[A-ZÆØÅ][a-zæøå]+)+)["\']', text
    )
    if m:
        parts = m.group(1).split()
        return " ".join(parts[:-1]), parts[-1]

    STOP = {
        'Opprett', 'Create', 'Crea', 'Erstelle', 'Créez', 'En', 'Et', 'Ein', 'Eit',
        'Une', 'Un', 'Nuevo', 'Nueva', 'Neue', 'Ansatt', 'Employee', 'Kunde',
        'Customer', 'Med', 'Named', 'With', 'Og', 'Tilsett', 'Medarbeider',
        'Epost', 'Email', 'Telefon', 'Phone', 'And',
    }
    words = [w for w in re.findall(r'\b[A-ZÆØÅ][a-zæøåéèêëàâîïôùûüç]+\b', text)
             if w not in STOP]
    if len(words) >= 2:
        return words[0], words[1]
    if len(words) == 1:
        return words[0], "Person"
    return "Ukjent", "Person"


def extract_company_name(text: str) -> Optional[str]:
    # Quoted name
    m = re.search(r'["\u201c\u201d]([^"\u201c\u201d]{2,60})["\u201c\u201d]', text)
    if m:
        return m.group(1).strip()
    m = re.search(r"'([^']{2,60})'", text)
    if m:
        return m.group(1).strip()

    # "med navn / named / llamado / namens / appelé / called X AS"
    m = re.search(
        r'(?:med\s+navn|named?\s*|called?\s*|namens\s*|llamad[oa]\s*|appel[eé][e]?\s*|com\s+nome\s*|lié\s+au\s+client\s*|for\s+kunden?\s*|for\s+kunde\s*)\s*'
        r'([A-ZÆØÅ0-9][\w\s&.-]{1,50}?(?:\s+(?:AS|Ltd|GmbH|SAS|BV|SARL|SRL|AB))?)'
        r'(?=\s+(?:med|with|con|avec|mit|com|\()|,|\.|$)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()

    # "kunden X AS" or "cliente X AS"
    m = re.search(
        r'(?:kunden?|kunde|cliente|client|Kunden?)\s+'
        r'([A-ZÆØÅ][\w\s&.-]{1,40}(?:\s+(?:AS|Ltd|GmbH|SAS|BV|SARL|SRL|AB))?)'
        r'(?=\s|\(|,|$)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()

    m = re.search(r'\b([A-ZÆØÅ][\w\s&.-]{1,40}(?:AS|Ltd|GmbH|SAS|BV|SARL|SRL|AB))\b', text)
    if m:
        return m.group(1).strip()

    return None


def extract_address(text: str) -> Optional[dict]:
    m = re.search(r'(?:adresse|address)\s+([^,]+),\s*([A-ZÆØÅa-zæøå]+)', text, re.IGNORECASE)
    if m:
        return {
            "addressLine1": m.group(1).strip(),
            "city": m.group(2).strip(),
            "country": {"id": 1},
        }
    return None


def extract_product_name_from_order(text: str) -> Optional[str]:
    """Extract product name from order prompts like 'produkta X (nr) til pris'."""
    # Norwegian: "produkta/produktet/produkt X"
    m = re.search(
        r'(?:produkta?|produktet|vare[nr]?|tjeneste[nr]?|service)\s+'
        r'["\']?([A-ZÆØÅ][^"\',()\d][^"\',()\n]{1,50}?)["\']?\s*(?:\(|\d|til|à|for|,|$)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# OPPGAVEDETEKSJON
# ---------------------------------------------------------------------------

def detect_task(prompt: str) -> str:
    p = prompt.lower()

    # Ordre/bestilling (must check before invoice)
    if any(k in p for k in ['ordre', 'bestilling', 'bestellung', 'commande', 'pedido',
                              'encomenda', 'opprett ein ordre', 'opprett en ordre',
                              'create an order', 'create order', 'ny ordre']):
        return 'order'

    if any(k in p for k in ['faktura', 'invoice', 'rechnung', 'facture', 'factura', 'fatura']):
        return 'invoice'
    if any(k in p for k in ['reiseregning', 'travel expense', 'reiseutlegg', 'dienstreise',
                              'note de frais', 'gasto de viaje', 'despesa de viagem']):
        return 'travel_expense'
    if any(k in p for k in ['avdeling', 'department', 'abteilung', 'département',
                              'departamento', 'divisão']):
        return 'department'
    if any(k in p for k in ['leverandør', 'supplier', 'lieferant', 'fournisseur',
                              'proveedor', 'fornecedor']):
        return 'supplier'
    if any(k in p for k in ['produkt', 'product', 'produit', 'producto', 'vare',
                              'artikel', 'prodotto']):
        return 'product'
    if any(k in p for k in ['prosjekt', 'project', 'projekt', 'projet', 'proyecto',
                              'projeto']):
        return 'project'
    if any(k in p for k in ['kunde', 'customer', 'client', 'klient', 'cliente',
                              'crea un nuevo cliente', 'nouveau client']):
        return 'customer'
    if any(k in p for k in ['ansatt', 'employee', 'medarbeider', 'mitarbeiter',
                              'employé', 'empleado', 'funcionário', 'tilsett',
                              'trabalhador']):
        return 'employee'

    if extract_email(prompt):
        return 'employee'
    return 'employee'


def is_admin(prompt: str) -> bool:
    p = prompt.lower()
    return any(k in p for k in [
        'administrator', 'kontoadministrator', 'admin',
        'alle rettigheter', 'all privileges', 'all_privileges',
        'administrateur', 'administrador', 'administrativ',
        'vera kontoadministrator',
    ])


# ---------------------------------------------------------------------------
# HANDLERS
# ---------------------------------------------------------------------------

def handle_employee(prompt: str, base_url: str, auth: tuple):
    first, last = extract_name(prompt)
    email = extract_email(prompt)
    phone = extract_phone(prompt)

    body: dict = {"firstName": first, "lastName": last}
    if email:
        body["email"] = email
    if phone:
        body["phoneNumberMobile"] = phone

    print(f"  Oppretter ansatt: {first} {last}")
    result = tx("POST", "/employee", base_url, auth, body)
    emp_id = get_id(result)

    if emp_id and is_admin(prompt):
        print(f"  Setter ALL_PRIVILEGES for ansatt {emp_id}")
        tx("PUT", "/employee/entitlement/:grantEntitlementsByTemplate",
           base_url, auth,
           params={"employeeId": emp_id, "template": "ALL_PRIVILEGES"})


def handle_customer(prompt: str, base_url: str, auth: tuple):
    name = extract_company_name(prompt)
    if not name:
        first, last = extract_name(prompt)
        name = f"{first} {last}".strip()

    email = extract_email(prompt)
    org_nr = extract_org_number(prompt)
    address = extract_address(prompt)

    body: dict = {"name": name, "isCustomer": True}
    if email:
        body["email"] = email
    if org_nr:
        body["organizationNumber"] = org_nr
    if address:
        body["physicalAddress"] = address

    print(f"  Oppretter kunde: {name}")
    tx("POST", "/customer", base_url, auth, body)


def handle_supplier(prompt: str, base_url: str, auth: tuple):
    name = extract_company_name(prompt)
    if not name:
        first, last = extract_name(prompt)
        name = f"{first} {last}".strip()

    email = extract_email(prompt)
    org_nr = extract_org_number(prompt)

    body: dict = {"name": name, "isSupplier": True}
    if email:
        body["email"] = email
    if org_nr:
        body["organizationNumber"] = org_nr

    print(f"  Oppretter leverandør: {name}")
    tx("POST", "/supplier", base_url, auth, body)


def handle_product(prompt: str, base_url: str, auth: tuple):
    name = None
    m = re.search(r"['\"\u201c\u201d]([^'\"\u201c\u201d]+)['\"\u201c\u201d]", prompt)
    if m:
        name = m.group(1)
    if not name:
        m = re.search(
            r'(?:appel[eé][e]?\s+|llamad[oa]\s+|named?\s+|kalt\s+|genannt\s+|chamad[oa]\s+)'
            r'["\']?([^"\',.]+?)["\']?(?:\s+avec|\s+with|\s+med|\s+con|\s+mit|\s+com|,|$)',
            prompt, re.IGNORECASE
        )
        if m:
            name = m.group(1).strip()
    if not name:
        name = "Produkt"

    price = extract_price(prompt)
    prod_nr = extract_product_number(prompt)

    body: dict = {"name": name}
    if price is not None:
        body["priceExcludingVatCurrency"] = price
    if prod_nr:
        body["productNumber"] = prod_nr

    print(f"  Oppretter produkt: {name}, pris={price}, nr={prod_nr}")
    tx("POST", "/product", base_url, auth, body)


def handle_department(prompt: str, base_url: str, auth: tuple):
    name = None
    m = re.search(r"['\"\u201c\u201d]([^'\"\u201c\u201d]+)['\"\u201c\u201d]", prompt)
    if m:
        name = m.group(1)
    if not name:
        m = re.search(
            r'(?:namens|named?\s*|called?\s*|kalt\s*|appel[eé][e]?\s*|llamad[oa]\s*)\s*'
            r'["\']?([A-ZÆØÅ][^"\',.]+?)["\']?'
            r'(?=\s+(?:mit|with|med|con|avec|com)|\s+\w*nummer|,|$)',
            prompt, re.IGNORECASE
        )
        if m:
            name = m.group(1).strip()
    if not name:
        name = "Avdeling"

    dept_nr = extract_department_number(prompt)
    if not dept_nr:
        dept_nr = str(random.randint(10, 99))

    body: dict = {"name": name, "departmentNumber": dept_nr}
    print(f"  Oppretter avdeling: {name}, nr={dept_nr}")
    tx("POST", "/department", base_url, auth, body)


def handle_order(prompt: str, base_url: str, auth: tuple):
    """Opprett ordre: finn/opprett kunde og produkt, opprett ordre med linjer."""
    today = date.today().isoformat()

    # Finn/opprett kunde
    company = extract_company_name(prompt)
    org_nr = extract_org_number(prompt)
    email = extract_email(prompt)
    customer_id = find_or_create_customer(
        company or "Kunde", base_url, auth, org_nr=org_nr, email=email
    )
    if not customer_id:
        print("  Kunne ikke hente/opprette kunde")
        return

    # Finn produkt og pris
    price = extract_price(prompt)
    prod_name = extract_product_name_from_order(prompt)
    prod_nr = extract_product_number(prompt)

    order_lines = []
    if prod_name:
        product_id = find_or_create_product(prod_name, base_url, auth,
                                            price=price, prod_nr=prod_nr)
        if product_id:
            line: dict = {"product": {"id": product_id}, "count": 1.0}
            if price is not None:
                line["unitPriceExcludingVatCurrency"] = price
            order_lines.append(line)

    body: dict = {
        "customer": {"id": customer_id},
        "orderDate": today,
    }
    if order_lines:
        body["orderLines"] = order_lines

    print(f"  Oppretter ordre for kunde {customer_id}")
    tx("POST", "/order", base_url, auth, body)


def handle_invoice(prompt: str, base_url: str, auth: tuple):
    today = date.today().isoformat()
    due = (date.today() + timedelta(days=30)).isoformat()

    company = extract_company_name(prompt)
    org_nr = extract_org_number(prompt)
    email = extract_email(prompt)
    customer_id = find_or_create_customer(
        company or "Fakturakunde", base_url, auth, org_nr=org_nr, email=email
    )
    if not customer_id:
        print("  Kunne ikke hente/opprette kunde")
        return

    r = tx("POST", "/order", base_url, auth, {
        "customer": {"id": customer_id},
        "orderDate": today,
    })
    order_id = get_id(r)
    if not order_id:
        print("  Kunne ikke hente ordre-ID")
        return

    tx("POST", "/invoice", base_url, auth, {
        "invoiceDate": today,
        "invoiceDueDate": due,
        "customer": {"id": customer_id},
        "orders": [{"id": order_id}],
    })


def handle_travel_expense(prompt: str, base_url: str, auth: tuple):
    today = date.today().isoformat()

    r = tx("GET", "/employee", base_url, auth,
           params={"fields": "id,firstName,lastName", "count": 1})
    emp_id = None
    values = r.get("values", [])
    if values:
        emp_id = values[0]["id"]
    if not emp_id:
        print("  Ingen ansatt funnet")
        return

    body: dict = {
        "employee": {"id": emp_id},
        "travelDetails": {"departureDate": today},
    }
    m = re.search(
        r'(?:beskrivelse|description|Beschreibung)\s*[:\s]+([^,.]+)',
        prompt, re.IGNORECASE
    )
    if m:
        body["description"] = m.group(1).strip()

    tx("POST", "/travelExpense", base_url, auth, body)


def handle_project(prompt: str, base_url: str, auth: tuple):
    today = date.today().isoformat()

    # Prosjektnavn
    name = None
    m = re.search(r'["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]', prompt)
    if m:
        name = m.group(1)
    if not name:
        m = re.search(r"'([^']+)'", prompt)
        if m:
            name = m.group(1)
    if not name:
        m = re.search(
            r'(?:prosjekt|project|projekt|projet|proyecto|projeto)\s+'
            r'(?:kalt\s+|med\s+navn\s+|named?\s+|appel[eé][e]?\s+)?'
            r'([A-ZÆØÅ][^\s,\.]+(?:\s+[^\s,\.]+)*)',
            prompt, re.IGNORECASE
        )
        if m:
            name = m.group(1).strip()
    if not name:
        name = "Prosjekt"

    # Finn/opprett kunde fra prompten
    company = extract_company_name(prompt)
    org_nr = extract_org_number(prompt)
    customer_id = None
    if company:
        customer_id = find_or_create_customer(company, base_url, auth, org_nr=org_nr)
    if not customer_id:
        r = tx("GET", "/customer", base_url, auth,
               params={"fields": "id,name", "count": 1})
        values = r.get("values", [])
        if values:
            customer_id = values[0]["id"]
    if not customer_id:
        r = tx("POST", "/customer", base_url, auth,
               {"name": "Prosjektkunde AS", "isCustomer": True})
        customer_id = get_id(r)

    # Unikt prosjektnummer
    proj_nr = str(random.randint(1000, 9999))

    body: dict = {"name": name, "number": proj_nr, "startDate": today}
    if customer_id:
        body["customer"] = {"id": customer_id}

    print(f"  Oppretter prosjekt: {name}")
    tx("POST", "/project", base_url, auth, body)


# ---------------------------------------------------------------------------
# FASTAPI RUTER
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/solve")
async def solve(request_body: dict):
    try:
        prompt: str = request_body.get("prompt", "")
        creds: dict = request_body.get("tripletex_credentials", {})
        base_url: str = creds.get("base_url", "").rstrip("/")
        session_token: str = creds.get("session_token", "")
        auth = ("0", session_token)

        print(f"\n=== OPPGAVE ===\n{prompt}")
        print(f"  Base URL: {base_url}")

        task = detect_task(prompt)
        print(f"  → Detektert: {task}")

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
        elif task == "order":
            handle_order(prompt, base_url, auth)
        elif task == "invoice":
            handle_invoice(prompt, base_url, auth)
        elif task == "travel_expense":
            handle_travel_expense(prompt, base_url, auth)
        elif task == "project":
            handle_project(prompt, base_url, auth)

    except Exception as exc:
        import traceback
        print(f"FEIL: {exc}")
        traceback.print_exc()

    return JSONResponse({"status": "completed"})
