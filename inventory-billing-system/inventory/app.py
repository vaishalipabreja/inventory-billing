# imports - standard imports
import os
import sqlite3
from pathlib import Path
from reportlab.pdfgen import canvas
import pdfkit
from datetime import date, datetime
import uuid
from weasyprint import HTML

# imports - third party imports
from flask import Flask, redirect, render_template, request, jsonify, render_template, make_response, send_file

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME

VIEWS = {
    "Invoice History": "/invoice_history",
    "Stock": "/product",
    "Customer": "/customer",
    "Invoice": "/invoice",
}
EMPTY_SYMBOLS = {"", " ", None}

app = Flask(__name__)

if os.environ.get("FLASK_DEBUG") == "1":
    app.config.update(TEMPLATES_AUTO_RELOAD=True)
    DATABASE_NAME = _DATABASE_PATH.resolve()
else:
    DATABASE_NAME = os.environ.get("DATABASE_NAME") or _DATABASE_PATH.resolve()

def init_database():
    PRODUCTS = (
        "products("
        "prod_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prod_name TEXT UNIQUE NOT NULL, "
        "prod_qty INTEGER NOT NULL) "
    )
    INVOICES = (
        "invoices("
        "invoice_id TEXT PRIMARY KEY, "
        "customer_id TEXT NOT NULL, "
        "total_amount REAL NOT NULL, "
        "paid_amount REAL NOT NULL, "
        "invoice_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(customer_id) REFERENCES customer(customer_id)) "
    )
    CUSTOMERS = (
        "customers("
        "customer_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "customer_name TEXT NOT NULL, "
        "agent_name TEXT NOT NULL,"
        "extra_payment_amount INTEGER NOT NULL, "
        "UNIQUE(customer_name, agent_name))"
    )

    INVOICE_ITEMS = (
        "invoice_items("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "invoice_id TEXT NOT NULL, "
        "prod_id INTEGER NOT NULL, "
        "qty INTEGER NOT NULL, "
        "FOREIGN KEY(invoice_id) REFERENCES invoices(invoice_id), "
        "FOREIGN KEY(prod_id) REFERENCES products(prod_id))"
    )

    PAYMENTS = (
        "payments("
        "payment_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "customer_id TEXT NOT NULL, "
        "payment_amount INTEGER NOT NULL, "
        "discount_amount REAL NOT NULL DEFAULT 0,"
        "payment_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(customer_id) REFERENCES customers(customer_id)) "
    )

    with sqlite3.connect(DATABASE_NAME) as conn:
        for table_definition in [PRODUCTS, INVOICES, CUSTOMERS, INVOICE_ITEMS, PAYMENTS]:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")
    run_startup_migrations()

def column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns

def run_startup_migrations():
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()

        # ---- Migration: add payment_mode to payments ----
        if not column_exists(cursor, "payments", "payment_mode"):
            cursor.execute("""
                ALTER TABLE payments
                ADD COLUMN payment_mode TEXT NOT NULL DEFAULT 'cash'
            """)

        # ---- Future migrations go here ----
        # if not column_exists(cursor, "table", "column"):
        #     cursor.execute("ALTER TABLE table ADD COLUMN column TYPE")

        conn.commit()

app.init_db = init_database

def count_numeric_inputs(addition_string):
    # Initialize a counter for numeric inputs
    numeric_count = 0

    # Iterate through each character in the string
    for char in addition_string:
        # Check if the character is numeric
        if char.isnumeric():
            numeric_count += 1

    return numeric_count

def get_customer_id(conn, customer_name, agent_name):
    cursor = conn.cursor()
    query = "SELECT customer_id FROM customers WHERE customer_name = ? AND agent_name = ?"
    cursor.execute(query, (customer_name, agent_name))
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else None

def generate_unique_bill_number():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    year = datetime.utcnow().strftime('%Y')

    cursor.execute("""
        SELECT MAX(CAST(SUBSTR(invoice_id, 1, INSTR(invoice_id, '-') - 1) AS INTEGER))
        FROM invoices
        WHERE invoice_id LIKE ?
    """, (f"%-{year}",))

    last_number = cursor.fetchone()[0]

    new_number = (last_number or 0) + 1

    return f"{new_number}-{year}"

def calculate_qty(expr: str) -> int:
    qty = 0
    parts = expr.split("+")

    for part in parts:
        if "*" in part:
            first, _ = part.split("*", 1)
            qty += int(float(first))
        else:
            qty += 1

    return qty

def calculate_row(expr, unit_price, less_weight):
    """
    Calculate quantity, weights, stock deduction, and amount for different formats:
    - normal number or arithmetic (e.g., 5*2, 1+2)
    - '65/4' → gross weight 65 for 4 bags
    - '7x9' → 7 bags, 9 weight per bag, amount = rate * qty
    """
    expr = expr.replace(" ", "").lower()  # clean input

    less_weight = float(less_weight or 0)
    unit_price = float(unit_price or 0)

    # Case 1: "65/4"
    if "/" in expr:
        gross, qty = expr.split("/")
        gross = float(gross)
        qty = int(qty)
        net_weight = gross - less_weight
        amount = round(net_weight * unit_price, 2)

        return {
            "qty": qty,
            "gross_weight": round(gross,2),
            "net_weight": round(net_weight,2),
            "stock_deduct": round(net_weight,2),
            "amount": round(amount,2)
        }

    # Case 2: "7x9"
    if "x" in expr:
        qty, per_bag = expr.split("x")
        qty = int(qty)
        per_bag = float(per_bag)
        gross = qty * per_bag
        net_weight = round(gross - less_weight,2)
        amount = round(qty * unit_price, 2)  # amount = rate * qty

        return {
            "qty": qty,
            "gross_weight": round(gross,2),
            "net_weight": round(net_weight,2),
            "stock_deduct": round(net_weight,2),
            "amount": round(amount,2)
        }

    # Case 3: normal arithmetic (1*4, 5+7.8)
    value = safe_math(expr)
    qty = calculate_qty(expr)
    net_weight = round(value - less_weight,2)
    amount = round(net_weight * unit_price, 2)

    return {
    "qty": qty,
    "gross_weight": round(value, 2),
    "net_weight": round(net_weight, 2),
    "stock_deduct": round(net_weight, 2),
    "amount": round(amount, 2)
    }

import ast, operator

OPS = {
    ast.Add: operator.add,
    ast.Mult: operator.mul,
}

def safe_math(expr):
    def _eval(node):
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.BinOp):
            return OPS[type(node.op)](_eval(node.left), _eval(node.right))
        raise ValueError("Invalid expression")

    return _eval(ast.parse(expr, mode="eval").body)

@app.route("/", methods=["GET"])
def main_page():
    return invoice_history()


@app.route('/customer_details')
def customer_details():
    conn = sqlite3.connect(DATABASE_NAME)
    customer_name = request.args.get('customer_name')
    agent_name = request.args.get('agent_name')
    customer_id = get_customer_id(conn, customer_name, agent_name)
    # Fetch customer details from the database

    if customer_id:
        # Fetch invoices and payments for the customer
        invoices_query = "SELECT invoice_id, total_amount, paid_amount, invoice_time FROM invoices WHERE customer_id=? ORDER BY invoice_time ASC"
        payments_query = "SELECT payment_id, payment_amount, discount_amount, payment_mode, payment_time FROM payments WHERE customer_id=?"

        invoices = conn.execute(invoices_query, (customer_id,)).fetchall()
        payments = conn.execute(payments_query, (customer_id,)).fetchall()

        # Pass the data to the template
        customer_details = {
            'customer_name': customer_name,
            'agent_name': agent_name,
            'invoices': invoices,
            'payments': payments,
        }

        # Render your customer details template with the provided customer details
        return render_template(
    "customer_details.jinja",
    customer_details=customer_details,
    link=VIEWS,
    title="Customer Details",
    current_date=date.today().isoformat())
    else:
        # Render the 'customer_not_found' template or handle accordingly
        return render_template(
    "customer_not_found.html",
    link=VIEWS,
    title="Customer Not Found"
)

    
@app.route("/invoice_history", methods=["POST", "GET"])
def invoice_history():
    conn = sqlite3.connect(DATABASE_NAME)

    conditions = ["invoices.total_amount != invoices.paid_amount"]
    params = []

    if request.method == "POST":
        date = request.form.get("date", "")
        customer = request.form.get("customer", "")
        agent = request.form.get("agent", "")

        if date:
            conditions.append("DATE(invoices.invoice_time) = ?")
            params.append(date)
        if customer:
            conditions.append("customers.customer_name LIKE ?")
            params.append(f"%{customer}%")
        if agent:
            conditions.append("customers.agent_name LIKE ?")
            params.append(f"%{agent}%")

    query = """
        SELECT
            invoices.invoice_id,
            invoices.invoice_time,
            customers.customer_name,
            customers.agent_name,
            invoices.total_amount,
            invoices.paid_amount
        FROM invoices
        LEFT JOIN customers
            ON customers.customer_id = invoices.customer_id
    """

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY customers.customer_name DESC"

    invoices = conn.execute(query, params).fetchall()

    return render_template(
        "invoice_history.jinja",
        link=VIEWS,
        title="Invoice History",
        invoices=invoices
    )




@app.route("/product", methods=["POST", "GET"])
def product():
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            prod_name, quantity = request.form["prod_name"].strip(), request.form["prod_qty"].strip()
            transaction_allowed = prod_name not in EMPTY_SYMBOLS and quantity not in EMPTY_SYMBOLS

            if transaction_allowed:
                conn.execute(
                    "INSERT INTO products (prod_name, prod_qty) VALUES (?, ?)",
                    (prod_name, quantity),
                )
                return redirect(VIEWS["Stock"])

        products = conn.execute("SELECT * FROM products ORDER BY prod_name ASC").fetchall()

    return render_template(
        "product.jinja",
        link=VIEWS,
        products=products,
        title="Stock",
    )

@app.route("/customer", methods=["POST", "GET"])
def customer():
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            customer_name, agent_name = request.form["customer_name"].strip(), request.form["agent_name"].strip()
            transaction_allowed = customer_name not in EMPTY_SYMBOLS and agent_name not in EMPTY_SYMBOLS

            if transaction_allowed:
                try:
                    conn.execute("INSERT INTO customers (customer_name, agent_name, extra_payment_amount) VALUES (?, ?, ?)",
                       (customer_name, agent_name, 0))
                    conn.commit()
                except Exception as e:
                    return f"Error: {e}"
                return redirect(VIEWS["Customer"])

        customers_query = """
SELECT customers.customer_name, 
       customers.agent_name, 
       ROUND(COALESCE(invoice_totals.total_amount, 0) - customers.extra_payment_amount,2)
FROM customers 
LEFT JOIN (
    SELECT customer_id, SUM(total_amount - paid_amount) AS total_amount
    FROM invoices 
    GROUP BY customer_id
) AS invoice_totals ON customers.customer_id = invoice_totals.customer_id
"""
        customers = conn.execute(customers_query).fetchall()
    return render_template(
        "customer.jinja",
        link=VIEWS,
        customers=customers,
        title="Customer",
    )

@app.route("/invoice", methods=["POST", "GET"])
def invoice():
    with sqlite3.connect(DATABASE_NAME) as conn:
        customers = conn.execute("SELECT * FROM customers").fetchall()
        products = conn.execute("SELECT * FROM products WHERE prod_qty > 0").fetchall()

    if request.method == "POST":
        with sqlite3.connect(DATABASE_NAME) as conn:
            invoice_id = generate_unique_bill_number()

            invoice_date = request.form.get("invoice_date")
            customer_raw = request.form.get("customer_name")
            transport_name = request.form.get("transportname")
            customer_name = customer_raw.split("(")[0].strip()
            agent_name = customer_raw.split("(")[1].strip(")")
            customer_id = get_customer_id(conn, customer_name, agent_name)

            product_name = request.form.getlist("product_name")
            marka = request.form.getlist("marka")
            weight_str = request.form.getlist("weight_str")
            unit_price = request.form.getlist("unit_price")
            less_price = request.form.getlist("less_price")

            adjusted_amount = float(request.form.get("adjusted_amount") or 0)

            packaging = float(request.form.get("packaging") or 0)
            transport = float(request.form.get("transport") or 0)
            mandi = float(request.form.get("mandi") or 0)
            others = float(request.form.get("others") or 0)
            comment = request.form.get("comment")

            calculated_weights = []
            net_weights = []
            stock_deductions = []

            sub_total_amount = 0
            rows = []

            # ---- CALCULATION ----
            for w, up, lp in zip(weight_str, unit_price, less_price):
                row = calculate_row(w, up, lp)
                rows.append(row)

                calculated_weights.append(row["gross_weight"])
                net_weights.append(row["net_weight"])
                stock_deductions.append(row["stock_deduct"])

                sub_total_amount += row["amount"]

            total_charges = packaging + transport + mandi + others
            total_amount = sub_total_amount + total_charges

            # ---- EXTRA PAYMENT LOGIC ----
            cursor = conn.cursor()
            cursor.execute(
                "SELECT extra_payment_amount FROM customers WHERE customer_id = ?",
                (customer_id,)
            )
            extra_payment = cursor.fetchone()[0] or 0
            extra_payment += adjusted_amount

            if extra_payment >= total_amount:
                paid_amount = total_amount
                extra_payment -= total_amount
            else:
                paid_amount = extra_payment
                extra_payment = 0

            cursor.execute(
                "UPDATE customers SET extra_payment_amount = ? WHERE customer_id = ?",
                (extra_payment, customer_id)
            )

            # ---- STOCK CHECK + UPDATE ----
            transaction_allowed = True

            for deduct_qty, product in zip(stock_deductions, product_name):
                row = conn.execute(
                    "SELECT prod_qty FROM products WHERE TRIM(prod_name) = TRIM(?)",(product,)).fetchone()
                current_qty = round(row[0], 2) if row else 0
                
                if deduct_qty > current_qty:
                    transaction_allowed = False
                    break

                new_qty = round(current_qty - deduct_qty, 2)

                conn.execute(
                    "UPDATE products SET prod_qty = ? WHERE prod_name = ?",
                    (new_qty, product)
                )

            if not transaction_allowed:
                return "Bill not generated (insufficient stock)"

            # ---- INSERT INVOICE ----
            conn.execute(
                "INSERT INTO invoices (invoice_id, customer_id, total_amount, paid_amount, invoice_time) "
                "VALUES (?, ?, ?, ?, ?)",
                (invoice_id, customer_id, total_amount, paid_amount, invoice_date)
            )

            for product, deduct_qty in zip(product_name, stock_deductions):
                conn.execute(
                    "INSERT INTO invoice_items (invoice_id, prod_id, qty) "
                    "VALUES (?, (SELECT prod_id FROM products WHERE prod_name = ?), ?)",
                    (invoice_id, product, deduct_qty)
                )

            return generate_bill(
    invoice_id,
    customer_name,
    agent_name,
    product_name,
    weight_str,
    rows,  # list of calculate_row results
    less_price,
    unit_price,
    sub_total_amount,
    packaging,
    transport,
    mandi,
    others,
    total_charges,
    total_amount,
    comment,
    extra_payment,
    invoice_date,
    transport_name,
    marka)

    return render_template(
        "invoice.jinja",
        link=VIEWS,
        title="Invoice",
        customers=customers,
        products=products,
        current_date=date.today().isoformat()
    )

@app.route("/delete")
def delete():
    delete_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        match delete_record_type:
            case "product":
                product_id = request.args.get("prod_id")
                if product_id:
                    conn.execute("DELETE FROM products WHERE prod_id = ?", product_id)
                return redirect(VIEWS["Stock"])

            case _:
                return redirect(VIEWS["Summary"])

@app.route('/search/product', methods=['POST'])
def searchProduct():
    with sqlite3.connect(DATABASE_NAME) as conn:
        keyword = request.json.get('keyword').strip()
        if keyword:
            products = conn.execute(f"SELECT prod_id, prod_name FROM products where prod_name LIKE '{keyword}%' LIMIT 20").fetchall()
            results = [{'id': product[0], 'name': product[1]} for product in products]
    return jsonify(results)


@app.route('/search/customer', methods=['POST'])
def searchCustomer():
    with sqlite3.connect(DATABASE_NAME) as conn:
        keyword = request.json.get('keyword').strip()
        results = []
        if keyword:
            customers = conn.execute(f"SELECT customer_name FROM customers where customer_name LIKE '{keyword}%'").fetchall()
            results = [{'name': customer[0]} for customer in customers]
    return jsonify(results)

@app.route("/edit", methods=["POST"])
def edit():
    edit_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        match edit_record_type:
            case "product":
                prod_id, prod_qty = (
                    request.form["prod_id"],
                    request.form["prod_qty"],
                )
                if prod_qty:
                    if(float(prod_qty)>0):
                        conn.execute("UPDATE products SET prod_qty = prod_qty + ? WHERE prod_id = ?",
                        (prod_qty, prod_id),
)
                return redirect(VIEWS["Stock"])
            case "customer":
                new_customer_name, new_agent_name = (
                    request.form["customer_name"],
                    request.form["agent_name"],
                )
                old_customer_name, old_agent_name = (
                    request.form["old_customer_name"],
                    request.form["old_agent_name"],
                )
                if old_customer_name:
                    conn.execute("UPDATE customers SET customer_name = ?, agent_name = ? WHERE customer_name = ? AND agent_name = ?",
                                  (new_customer_name, new_agent_name, old_customer_name, old_agent_name)),
                return redirect(VIEWS["Customer"])

            case _:
                return redirect(VIEWS["Summary"])

@app.route("/record", methods=["POST"])
def record():
    customer_name = request.form["customer_name"]
    agent_name = request.form["agent_name"]
    discount = float(request.form.get("discount", 0))
    amount = float(request.form.get("amount", 0))
    payment_mode = request.form["payment_mode"]
    
    # Total applied to invoices
    effective_payment = amount + discount

    # Use submitted payment date if provided, else default to now
    payment_date = request.form.get("payment_date")
    if payment_date:
        # Set time to current time (optional, can also store 00:00:00 if you prefer)
        payment_time = f"{payment_date} {datetime.now().strftime('%H:%M:%S')}"
    else:
        payment_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        customer_id = get_customer_id(conn, customer_name, agent_name)

        # 1️⃣ Save the payment record
        conn.execute(
            "INSERT INTO payments (customer_id, payment_amount, payment_mode, discount_amount, payment_time) VALUES (?, ?, ?, ?, ?)",
            (customer_id, amount, payment_mode, discount, payment_time)
        )

        # 2️⃣ Apply to invoices FIFO as paid_amount
        cursor = conn.cursor()
        cursor.execute("""
            SELECT invoice_id, total_amount, paid_amount
            FROM invoices
            WHERE customer_id = ?
            ORDER BY invoice_time ASC
        """, (customer_id,))
        bills = cursor.fetchall()

        remaining = effective_payment

        for invoice_id, total_amount, paid_amount in bills:
            if remaining == 0:
                break

            balance = total_amount - paid_amount
            if balance <= 0:
                continue

            # how much to apply
            applied = min(balance, abs(remaining))

            # update invoice paid_amount
            cursor.execute("""
                UPDATE invoices
                SET paid_amount = paid_amount + ?
                WHERE invoice_id = ?
            """, (applied, invoice_id))

            if remaining > 0:
                remaining -= applied
            else:
                remaining += applied

        # leftover becomes extra_payment_amount
        if remaining != 0:
            cursor.execute("""
                UPDATE customers
                SET extra_payment_amount = extra_payment_amount + ?
                WHERE customer_id = ?
            """, (remaining, customer_id))

        conn.commit()
    
    return redirect(f"/customer_details?customer_name={customer_name}&agent_name={agent_name}")

def generate_bill(invoice_id, customer_name, agent_name,
                  product_name, weight_str, rows,
                  less_price, unit_price,
                  sub_total_amount,
                  packaging, transport, mandi, others,
                  total_charges, total_amount,
                  comment, paid_amount, invoice_date, transport_name, marka):

    # Convert date to desired format
    invoice_date = datetime.strptime(invoice_date, "%Y-%m-%d")
    invoice_data = {
        'invoice_number': invoice_id,
        'date': invoice_date.strftime('%d-%m-%Y'),
        'transport_name': transport_name,
        'customer_name': customer_name,
        'agent_name': agent_name,
        'address': customer_name.split(',', 1)[1] if ',' in customer_name else "",
        'sub_total': sub_total_amount,
        'packaging': packaging,
        'transport': transport,
        'mandi': mandi,
        'other': others,
        'total_charges': total_charges,
        'adjusted_amount': paid_amount,
        'total': total_amount,
        'comment': comment
    }

    # Build the invoice items list
    invoice_items = []
    for product, m, wt_s, row, lp, up in zip(product_name, marka, weight_str, rows, less_price, unit_price):
        invoice_items.append({
            "Product": product + "/" + m,
            "Quantity_Str": wt_s,
            "Quantity": row.get("qty", ""),
            "Gross_Wt": row.get("gross_weight", ""),
            "Net_Wt": row.get("net_weight", ""),
            "Less_Wt": lp,
            "Rate": up,
            "Amount": row.get("amount", "")
        })

    # Render the template (no MAX_ROWS limit)
    html_content = render_template(
        'invoice_template.html',
        invoice_data=invoice_data,
        invoice_items=invoice_items
    )

    # Generate PDF
    pdf_file = HTML(string=html_content).write_pdf()

    # Save PDF to folder
    folder_path = 'pdfs'
    os.makedirs(folder_path, exist_ok=True)
    file_name = f"{invoice_id}.pdf"
    file_path = os.path.join(folder_path, file_name)
    with open(file_path, 'wb') as f:
        f.write(pdf_file)

    # Return as download
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename={file_name}'
    return response


@app.route("/invoice/delete/<invoice_id>", methods=["POST"])
def delete_invoice(invoice_id):
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Fetch the invoice to get its customer and paid amount
        invoice = cursor.execute(
            "SELECT customer_id, paid_amount FROM invoices WHERE invoice_id = ?",
            (invoice_id,)
        ).fetchone()

        if not invoice:
            return "Invoice not found", 404

        customer_id = invoice["customer_id"]
        paid_amount = invoice["paid_amount"]
        
        # Fetch all products from this invoice
        invoice_items = cursor.execute(
            "SELECT prod_id, qty FROM invoice_items WHERE invoice_id = ?",
            (invoice_id,)
        ).fetchall()

        # Add the quantities back to the stock
        for item in invoice_items:
            cursor.execute("""
                UPDATE products
                SET prod_qty = prod_qty + ?
                WHERE prod_id = ?
            """, (item["qty"], item["prod_id"]))


        # Delete the invoice
        cursor.execute(
            "DELETE FROM invoices WHERE invoice_id = ?",
            (invoice_id,)
        )

        # Adjust extra_payment_amount if needed
        if paid_amount > 0:
            cursor.execute("""
                UPDATE customers
                SET extra_payment_amount = extra_payment_amount + ?
                WHERE customer_id = ?
            """, (paid_amount, customer_id))

        conn.commit()

    return redirect(VIEWS["Invoice History"])


with app.app_context():
    app.init_db()
