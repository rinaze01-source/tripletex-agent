"""
NM i AI 2026 – Tripletex Agent
Gemini-basert agent med tool use. Ingen OpenAI/Anthropic nøkkel nødvendig.
"""

import json
import os
from datetime import date
from typing import Optional
import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import google.generativeai as genai

app = FastAPI()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
genai.configure(api_key=GEMINI_API_KEY)

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


# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------

SYSTEM = """Du er en ekspert Tripletex regnskaps-API-agent for NM i AI 2026.
Oppgaver kan komme på norsk bokmål, nynorsk, engelsk, spansk, tysk, portugisisk eller fransk.

## KRITISKE REGLER
1. Bruk tripletex_api-verktøyet for hvert API-kall
2. Endepunkter: /employee, /customer, /order osv – ALDRI /v2/employee
3. Kontoen er alltid TOM – ikke søk etter eksisterende data, opprett alt direkte
4. Bruk ID fra POST-respons direkte i neste kall
5. Minimer antall API-kall (effektivitetsbonus)
6. Dagens dato: """ + date.today().isoformat() + """

## NAVNEPARSING (KRITISK)
"Kari Nordmann" → firstName="Kari", lastName="Nordmann"
"Ole Martin Hansen" → firstName="Ole Martin", lastName="Hansen"
ALDRI sett hele navnet i firstName. lastName må alltid fylles ut.

## ENDEPUNKTER

### ANSATT
POST /employee: {firstName, lastName, email?, phoneNumberMobile?}
Respons: {"value": {"id": 123}}

ADMINISTRATOR: to steg:
1. POST /employee → få id
2. PUT /employee/entitlement/:grantEntitlementsByTemplate
   params: {employeeId: <id>, template: "ALL_PRIVILEGES"}

### KUNDE
POST /customer: {name, isCustomer: true, email?, organizationNumber?, physicalAddress?}

### LEVERANDØR
POST /supplier: {name, isSupplier: true, email?, organizationNumber?}

### PRODUKT
POST /product: {name, priceExcludingVatCurrency?, productNumber?}

### ORDRE
1. POST /customer: {name, isCustomer: true, organizationNumber?}  → customer_id
2. POST /product: {name, priceExcludingVatCurrency?}  → product_id (hvis produkt nevnt)
3. POST /order: {
     customer: {id: customer_id},
     orderDate: "YYYY-MM-DD",
     orderLines: [{product: {id: product_id}, count: 1.0, unitPriceExcludingVatCurrency: pris}]
   }

### FAKTURA
1. POST /customer → customer_id
2. POST /order: {customer: {id: customer_id}, orderDate: "YYYY-MM-DD"} → order_id
3. POST /invoice: {invoiceDate: "YYYY-MM-DD", invoiceDueDate: "YYYY-MM-DD", customer: {id: customer_id}, orders: [{id: order_id}]}

### AVDELING
POST /department: {name, departmentNumber: "42"}

### PROSJEKT
1. POST /customer: {name, isCustomer: true} → customer_id
2. POST /project: {name, number: "1001", startDate: "YYYY-MM-DD", customer: {id: customer_id}}

### REISEREGNING
1. GET /employee?fields=id&count=1 → employee_id
2. POST /travelExpense: {employee: {id: employee_id}, travelDetails: {departureDate: "YYYY-MM-DD"}}

### KONTAKTPERSON
POST /contact: {firstName, lastName, customer: {id: customer_id}, email?}

## FEILHÅNDTERING
- 422: Les validationMessages – forteller hvilket felt som mangler
- 404: Sjekk endepunktet (ikke /v2/employee, bare /employee)
- Korriger og prøv én gang til ved feil

Kall verktøyet for hvert steg. Les responsen og bruk IDen i neste kall.
"""

# ---------------------------------------------------------------------------
# GEMINI TOOL DEFINITION
# ---------------------------------------------------------------------------

tripletex_tool = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="tripletex_api",
            description="Utfør ett API-kall mot Tripletex v2 REST API. Bruk /employee, /customer osv – ikke /v2/employee.",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "method": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="HTTP-metode: GET, POST, PUT eller DELETE"
                    ),
                    "endpoint": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="API-endepunkt, f.eks. /employee, /customer, /order, /employee/entitlement/:grantEntitlementsByTemplate"
                    ),
                    "body": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="JSON-streng for request body ved POST/PUT. Tom streng hvis ikke nødvendig."
                    ),
                    "params": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="JSON-streng med query-parametere, f.eks. {\"employeeId\": 123, \"template\": \"ALL_PRIVILEGES\"}. Tom streng hvis ikke nødvendig."
                    ),
                },
                required=["method", "endpoint"]
            )
        )
    ]
)

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

        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM,
            tools=[tripletex_tool],
        )

        chat = model.start_chat()
        response = chat.send_message(
            f"Oppgave: {prompt}\n\nBase URL: {base_url}\n\nUtfør oppgaven nå."
        )

        max_iterations = 20
        for _ in range(max_iterations):
            # Finn function calls i responsen
            fn_calls = []
            for part in response.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    fn_calls.append(part.function_call)

            if not fn_calls:
                print("  Gemini ferdig.")
                break

            # Utfør alle tool calls
            results = []
            for fn in fn_calls:
                name = fn.name
                args = dict(fn.args)

                method = args.get("method", "GET")
                endpoint = args.get("endpoint", "")
                body_str = args.get("body", "")
                params_str = args.get("params", "")

                print(f"  → {method} {endpoint}")

                try:
                    body = json.loads(body_str) if body_str else None
                except Exception:
                    body = None
                try:
                    params = json.loads(params_str) if params_str else None
                except Exception:
                    params = None

                result = tx(method, endpoint, base_url, auth, data=body, params=params)
                results.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=name,
                            response={"result": json.dumps(result, ensure_ascii=False)},
                        )
                    )
                )

            response = chat.send_message(results)

    except Exception as exc:
        import traceback
        print(f"FEIL: {exc}")
        traceback.print_exc()

    return JSONResponse({"status": "completed"})
