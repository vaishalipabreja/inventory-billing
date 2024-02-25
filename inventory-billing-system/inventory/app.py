# imports - standard imports
import os
import sqlite3
from pathlib import Path
from reportlab.pdfgen import canvas
import pdfkit
from datetime import date, datetime
import uuid

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
        "customer_name TEXT NOT NULL, "
        "total_amount INTEGER NOT NULL, "
        "paid_amount INTEGER NOT NULL, "
        "invoice_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(customer_name) REFERENCES customer(customer_name)) "
    )
    CUSTOMERS = (
        "customers("
        "customer_name TEXT PRIMARY KEY, "
        "agent_name TEXT NOT NULL) "
    )

    with sqlite3.connect(DATABASE_NAME) as conn:
        for table_definition in [PRODUCTS, INVOICES, CUSTOMERS]:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")


app.init_db = init_database

def generate_unique_bill_number():
    conn = sqlite3.connect(DATABASE_NAME)
    last_bill = conn.execute('SELECT * FROM invoices ORDER BY invoice_id DESC LIMIT 1').fetchone()
    print(last_bill)

    if last_bill:
        # Increment the last bill number to generate the new bill number
        last_number = int(last_bill[0].split('-')[0])
        new_number = last_number + 1
    else:
        # If there are no existing bills, start with 1
        new_number = 1

    # Use the new bill number for the current transaction
    return f"{new_number}-{datetime.utcnow().strftime('%Y')}" 

def calculate(expression):
    try:
        # Check if the expression contains only allowed characters
        allowed_chars = set('0123456789.+-* ')
        if not set(expression).issubset(allowed_chars):
            raise ValueError("Invalid characters in the expression")

        # Evaluate the expression
        result = eval(expression)
        return result
    except Exception as e:
        return f"Error: {e}"


@app.route("/invoice_history", methods=["POST","GET"])
def invoice_history():
    conn = sqlite3.connect(DATABASE_NAME)
    if request.method == "POST":
        date = request.form["date"]
        customer = request.form["customer"]
        conditions = []
        if date:
            date_object = datetime.strptime(date, '%Y-%m-%d').strftime('%Y-%m-%d')
            conditions.append(f"DATE(invoice_time) = '{date_object}'")
        elif customer:
            conditions.append(f"customer_name LIKE '%{customer}%'")
        query = "SELECT * FROM invoices"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        print(query)
        query+= " ORDER BY invoice_time DESC"
        invoices = conn.execute(query).fetchall()
    else:
        invoices = conn.execute("""SELECT invoice_id, customer_name, total_amount, 
        CASE
        WHEN total_amount - paid_amount >= 0 THEN total_amount - paid_amount
        ELSE 'Paid'
    END AS remaining_status FROM invoices ORDER BY invoice_time DESC""").fetchall()

    print(invoices)

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
            prod_name, quantity = request.form["prod_name"], request.form["prod_qty"]
            transaction_allowed = prod_name not in EMPTY_SYMBOLS and quantity not in EMPTY_SYMBOLS

            if transaction_allowed:
                conn.execute(
                    "INSERT INTO products (prod_name, prod_qty) VALUES (?, ?)",
                    (prod_name, quantity),
                )
                return redirect(VIEWS["Stock"])

        products = conn.execute("SELECT * FROM products").fetchall()
        print(products)

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
                conn.execute(
                    "INSERT INTO customers (customer_name, agent_name) VALUES (?, ?)",
                    (customer_name, agent_name),
                )
                return redirect(VIEWS["Customer"])

        customers = conn.execute("SELECT customers.customer_name, customers.agent_name, COALESCE(SUM(invoices.total_amount-invoices.paid_amount), 0) AS total_amount FROM customers LEFT JOIN invoices ON customers.customer_name = invoices.customer_name GROUP BY customers.customer_name").fetchall()
        print(customers)
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
        products = conn.execute("SELECT * FROM products where prod_qty > 0").fetchall()
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            invoice_id = generate_unique_bill_number()

            customer_name = request.form.get('customer_name').strip()
            product_name = request.form.getlist('product_name')
            weight_str = request.form.getlist('weight_str')
            unit_price = request.form.getlist('unit_price')
            less_price = request.form.getlist('less_price')
            paid_amount = request.form.get('paid_amount')

            adhat = float(request.form.get('adhat'))
            taulai = float(request.form.get('taulai'))
            majdoori = float(request.form.get('majdoori'))
            packing = float(request.form.get('packing'))
            other = float(request.form.get('other'))
            sub_total_amount = 0
            total_charges = 0
            calculated_weights=[]
            net_weights=[]
            for weight, up, lp in zip(weight_str, unit_price, less_price):
                calculated_weights.append(calculate(weight))
                net_weights.append(calculate(weight)-float(lp))
                sub_total_amount += (calculate(weight)-float(lp))*float(up)
            total_charges += adhat + taulai + majdoori + packing + other
            total_amount = total_charges + sub_total_amount
            agent=conn.execute("SELECT agent_name FROM customers where customer_name = ?",(customer_name,)).fetchall() 
            print(customer_name+"name")
            print(agent)
            agent_name = agent[0][0]
            transaction_allowed = customer_name not in EMPTY_SYMBOLS and total_amount not in EMPTY_SYMBOLS
            for weight,product in zip(calculated_weights,product_name):
                product_quantity=conn.execute("SELECT prod_qty FROM products where prod_name = ?",
                                 (product,)).fetchall() 
                print(product_quantity)
                transaction_allowed = weight<product_quantity[0][0]
                if(weight<product_quantity[0][0]):
                        conn.execute("UPDATE products SET prod_qty = ? WHERE prod_name = ?",(product_quantity[0][0]-weight, product,))
            
            if transaction_allowed:
                conn.execute(
                    "INSERT INTO invoices (invoice_id, customer_name, total_amount, paid_amount) VALUES (?, ?, ?, ?)",
                    (invoice_id, customer_name, total_amount, paid_amount),
                )
                return generate_bill(invoice_id, customer_name, agent_name, product_name, 
                                     weight_str, calculated_weights, net_weights, less_price, unit_price, sub_total_amount,
                                     adhat, taulai, majdoori, packing, other, total_charges, total_amount)
            return "Bill not generated"
    return render_template(
        "invoice.jinja",
        link=VIEWS,
        title="Invoice",
        customers=customers,
        products=products
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
            products = conn.execute(f"SELECT prod_id, prod_name FROM products where prod_name LIKE '{keyword}%'").fetchall()
            results = [{'id': product[0], 'name': product[1]} for product in products]
    return jsonify(results)


@app.route('/search/customer', methods=['POST'])
def searchCustomer():
    with sqlite3.connect(DATABASE_NAME) as conn:
        print(request.json.get('keyword'))
        keyword = request.json.get('keyword').strip()
        print(keyword)
        results = []
        if keyword:
            print(f"SELECT * FROM customers where customer_name LIKE f'{keyword}%'")
            customers = conn.execute(f"SELECT customer_name FROM customers where customer_name LIKE '{keyword}%'").fetchall()
            results = [{'name': customer[0]} for customer in customers]
    return jsonify(results)

@app.route("/edit", methods=["POST"])
def edit():
    edit_record_type = request.args.get("type")
    print(request.args.get("type"))

    with sqlite3.connect(DATABASE_NAME) as conn:
        match edit_record_type:
            case "product":
                prod_id, prod_name, prod_qty = (
                    request.form["prod_id"],
                    request.form["prod_name"],
                    request.form["prod_qty"],
                )

                if prod_name:
                    conn.execute(
                        "UPDATE products SET prod_name = ? WHERE prod_id = ?",
                        (prod_name, prod_id),
                    )
                if prod_qty:
                    product_quantity=conn.execute("SELECT prod_qty FROM products where prod_id = ?",
                                 (prod_id)).fetchall();     
                    if(int(prod_qty)>product_quantity[0][0]):
                        conn.execute("UPDATE products SET prod_qty = ? WHERE prod_id = ?",
                        (prod_qty, prod_id),
)
                return redirect(VIEWS["Stock"])

            case _:
                return redirect(VIEWS["Summary"])
            
@app.route("/record", methods=["POST"])
def record():
    customer_name, discount, amount = (
                    request.form["customer_name"],
                    request.form["discount"],
                    request.form["amount"],
                )
    record_payment(customer_name,discount)
    record_payment(customer_name,amount)
    return redirect(VIEWS["Customer"])

def record_payment(customer_name,amount):
    # Create a connection to the SQLite database
    conn = sqlite3.connect(DATABASE_NAME) 
    cursor = conn.cursor()
        # Retrieve the latest bill for the customer
    cursor.execute("""
            SELECT invoice_id, total_amount, paid_amount
            FROM invoices
            WHERE customer_name = ?
            ORDER BY invoice_time DESC
        """, (customer_name,))
    bills = cursor.fetchall()
    print(bills)
    for i, bill in enumerate(bills):
            invoice_id, total_amount, paid_amount = bill

            remaining_balance = total_amount

            # Deduct the amount from the remaining balance
            remaining_balance -= int(amount)

            # Update the invoice record with the new paid amount and remaining balance
            cursor.execute("""
                UPDATE invoices
                SET paid_amount = ?
                WHERE invoice_id = ?
            """, (paid_amount + int(amount), invoice_id))

            # Deduct the remaining amount from the next bill(s)
            amount -= max(0, -remaining_balance)
    conn.commit()
    print(f"Payment recorded for customer {customer_name}.")
    cursor.close()
    conn.close()

def generate_bill(invoice_id, customer_name, agent_name, product_name, weight_str, calculated_weights, net_weights, less_price, unit_price, sub_total_amount, 
                  adhat, taulai, majdoori, packing, other, total_charges, total_amount):
    print(total_amount)
    invoice_data = {
        'invoice_number': invoice_id,
        'date': date.today().strftime('%d-%m-%Y'),
        'customer_name': customer_name,
        'agent_name': agent_name,
        'address': customer_name.split(',',1)[1] if len(customer_name.split(',',1))>1 else "",
        'sub_total': sub_total_amount,
        'adhat': adhat,
        'taulai' : taulai,
        'majdoori': majdoori,
        'packing' : packing,
        'other' : other,
        'total_charges' : total_charges,
        'total' : total_amount
    }

    invoice_items = []
    for product, wt_s, wt_c, wt_n, lp, up in zip(product_name,weight_str, calculated_weights, net_weights, less_price, unit_price):
        invoice_item = {
            "Product" : product,
            "Quantity_Str" : "  "+wt_s,
            "Quantity" : wt_s.split('*')[0],
            "Gross_Wt" : float(wt_c),
            "Net_Wt" : wt_n,
            "Less_Wt" : lp,
            "Rate": float(up),
            "Amount" : (wt_n)*float(up)
        }
        invoice_items.append(invoice_item)

    # Render HTML template
    html_content = render_template('invoice_template.html', invoice_data=invoice_data, invoice_items=invoice_items)

    # Configure PDF options
    options = {
        'page-size': 'Letter',
        'margin-top': '0mm',
        'margin-right': '0mm',
        'margin-bottom': '0mm',
        'margin-left': '0mm',
    }

    # Convert HTML to PDF
    pdf_file = pdfkit.from_string(html_content, False, options=options)
    
    folder_path = 'pdfs'  # Specify the folder path
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    file_name= str(invoice_id)+'.pdf'
    file_path = os.path.join(folder_path, file_name)
    with open(file_path, 'wb') as file:
        file.write(pdf_file)
    
    # Create response with PDF attachment
    response = make_response(pdf_file)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename='+file_name

    return response
with app.app_context():
    app.init_db()
