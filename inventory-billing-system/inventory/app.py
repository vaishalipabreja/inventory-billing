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
        "customer_id TEXT NOT NULL, "
        "total_amount INTEGER NOT NULL, "
        "paid_amount INTEGER NOT NULL, "
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
    PAYMENTS = (
        "payments("
        "payment_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "customer_id TEXT NOT NULL, "
        "payment_amount INTEGER NOT NULL, "
        "payment_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(customer_id) REFERENCES customers(customer_id)) "
    )

    with sqlite3.connect(DATABASE_NAME) as conn:
        for table_definition in [PRODUCTS, INVOICES, CUSTOMERS, PAYMENTS]:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")


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
    last_bill = conn.execute('SELECT * FROM invoices ORDER BY invoice_time DESC LIMIT 1').fetchone()
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
        invoices_query = "SELECT invoice_id, total_amount, paid_amount, invoice_time FROM invoices WHERE customer_id=?"
        payments_query = "SELECT payment_id, payment_amount, payment_time FROM payments WHERE customer_id=?"

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
        return render_template('customer_details.jinja', customer_details=customer_details)
    else:
        # Render the 'customer_not_found' template or handle accordingly
        return render_template('customer_not_found.html')
    

@app.route("/invoice_history", methods=["POST","GET"])
def invoice_history():
    conn = sqlite3.connect(DATABASE_NAME)
    if request.method == "POST":
        date = request.form["date"]
        customer = request.form["customer"]
        agent = request.form["agent"]
        conditions = []
        if date:
            date_object = datetime.strptime(date, '%Y-%m-%d').strftime('%Y-%m-%d')
            conditions.append(f"DATE(invoice_time) = '{date_object}'")
        if customer:
            conditions.append(f"customer_name LIKE '%{customer}%'")
        if agent:
            conditions.append(f"agent_name LIKE '%{agent}%'")
        query = """SELECT invoices.invoice_id, customers.customer_name, customers.agent_name, invoices.invoice_time, 
        ROUND(COALESCE(SUM(invoices.total_amount - invoices.paid_amount), 0) - customers.extra_payment_amount,2) AS total_amount FROM invoices LEFT JOIN customers ON customers.customer_id = invoices.customer_id"""
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query+= " GROUP BY customers.customer_name, customers.agent_name ORDER BY invoice_time DESC"
        invoices = conn.execute(query).fetchall()
    else:
        invoices = conn.execute("""SELECT invoices.invoice_id, customers.customer_name, customers.agent_name, invoices.invoice_time,
        ROUND(COALESCE(SUM(invoices.total_amount - invoices.paid_amount), 0) - customers.extra_payment_amount,2) AS total_amount FROM invoices
        LEFT JOIN customers ON customers.customer_id = invoices.customer_id
        GROUP BY customers.customer_name, customers.agent_name
        ORDER BY invoice_time DESC""").fetchall()


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
        products = conn.execute("SELECT * FROM products where prod_qty > 0").fetchall()
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            invoice_id = generate_unique_bill_number()

            customer_name = request.form.get('customer_name').split("(")[0].strip()
            agent_name = request.form.get('customer_name').split("(")[1].strip(')')
            customer_id = get_customer_id(conn, customer_name, agent_name)
            product_name = request.form.getlist('product_name')
            weight_str = request.form.getlist('weight_str')
            unit_price = request.form.getlist('unit_price')
            less_price = request.form.getlist('less_price')
            adjusted_amount = float(request.form.get('adjusted_amount'))

            packaging = float(request.form.get('packaging'))
            transport = float(request.form.get('transport'))
            mandi = float(request.form.get('mandi'))
            others = float(request.form.get('others'))
            comment = request.form.get('comment')
            sub_total_amount = 0
            total_charges = 0
            calculated_weights=[]
            net_weights=[]
            for weight, up, lp in zip(weight_str, unit_price, less_price):
                calculated_weights.append(calculate(weight))
                net_weights.append(calculate(weight)-float(lp))
                sub_total_amount += round((calculate(weight)-float(lp))*float(up), 2)
            total_charges += packaging + transport + mandi + others
            total_amount = total_charges + sub_total_amount

            # Retrieve extra payments made by the customer
            cursor = conn.cursor()
            cursor.execute("""
                SELECT customers.extra_payment_amount
                FROM customers
                WHERE customer_id = ?
            """, (customer_id,))
            extra_payment = cursor.fetchone()[0] or 0
            extra_payment += adjusted_amount
            paid_amount = 0

            # Adjust total amount due and mark extra payment in table accordingly
            if extra_payment >= total_amount:
                paid_amount = total_amount
                extra_payment = extra_payment - paid_amount
            elif extra_payment > 0:
                paid_amount = total_amount - extra_payment
                extra_payment = 0

            cursor.execute("""
            UPDATE customers
            SET extra_payment_amount = ?
            WHERE customer_id = ?
        """, (extra_payment, customer_id))

            transaction_allowed = customer_name not in EMPTY_SYMBOLS and total_amount not in EMPTY_SYMBOLS
            for weight,product in zip(net_weights,product_name):
                product_quantity=conn.execute("SELECT prod_qty FROM products where prod_name = ?",
                                 (product,)).fetchall() 
                transaction_allowed = weight<=product_quantity[0][0]
                if(weight<=product_quantity[0][0]):
                        conn.execute("UPDATE products SET prod_qty = ? WHERE prod_name = ?",(product_quantity[0][0]-weight, product,))
            if transaction_allowed:
                conn.execute(
                    "INSERT INTO invoices (invoice_id, customer_id, total_amount, paid_amount) VALUES (?, ?, ?, ?)",
                    (invoice_id, customer_id, total_amount, paid_amount),
                )
                return generate_bill(invoice_id, customer_name, agent_name, product_name, 
                                     weight_str, calculated_weights, net_weights, less_price, unit_price, sub_total_amount,
                                      packaging, transport, mandi, others, total_charges, total_amount, comment, extra_payment)
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
                    if(int(prod_qty)>0):
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
    customer_name, agent_name, discount, amount = (
                    request.form["customer_name"],
                    request.form["agent_name"],
                    request.form["discount"],
                    request.form["amount"],
                )
    payment_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DATABASE_NAME) as conn:
        customer_id = get_customer_id(conn,customer_name,agent_name)
        conn.execute("INSERT INTO payments ( customer_id, payment_amount, payment_time) VALUES ( ?, ?, ?)",
                        (customer_id, float(discount)+float(amount), payment_time))
    record_payment(customer_id, discount)
    record_payment(customer_id, amount)
    return redirect(VIEWS["Customer"])

def record_payment(customer_id, amount):
    # Create a connection to the SQLite database
    with sqlite3.connect(DATABASE_NAME) as conn:
        
        cursor = conn.cursor()
        amount = int(amount)
        # Retrieve the latest bill for the customer
        cursor.execute("""
                SELECT invoice_id, total_amount, paid_amount
                FROM invoices
                WHERE customer_id = ?
                ORDER BY invoice_time ASC
            """, (customer_id,))
        bills = cursor.fetchall()
        for i, bill in enumerate(bills):
                invoice_id, total_amount, paid_amount = bill
                # Deduct the amount from the remaining balance
                remaining_amount = total_amount - paid_amount
                if remaining_amount <= 0:
                    continue;
                if( remaining_amount >= amount):
                    paid_bill = amount
                else:
                    paid_bill = remaining_amount
                # Update the invoice record with the new paid amount and remaining balance
                cursor.execute("""
                    UPDATE invoices
                    SET paid_amount = ?
                    WHERE invoice_id = ?
                """, (paid_amount + int(paid_bill), invoice_id))

                # Deduct the remaining amount from the next bill(s)
                amount = int(amount) - paid_bill
        if amount > 0:
            cursor.execute("""
            UPDATE customers
            SET extra_payment_amount = extra_payment_amount + ?
            WHERE customer_id = ?
            """, (amount, customer_id))
        conn.commit()
    cursor.close()
    conn.close()

def generate_bill(invoice_id, customer_name, agent_name, product_name, weight_str, calculated_weights, net_weights, less_price, unit_price, sub_total_amount, 
                  packaging, transport, mandi, others, total_charges, total_amount, comment, paid_amount):
    invoice_data = {
        'invoice_number': invoice_id,
        'date': date.today().strftime('%d-%m-%Y'),
        'customer_name': customer_name,
        'agent_name': agent_name,
        'address': customer_name.split(',',1)[1] if len(customer_name.split(',',1))>1 else "",
        'sub_total': sub_total_amount,
        'packaging': packaging,
        'transport' : transport,
        'mandi': mandi,
        'other' : others,
        'total_charges' : total_charges,
        'adjusted_amount' : paid_amount,
        'total' : total_amount,
        'comment' :comment
    }

    invoice_items = []
    for product, wt_s, wt_c, wt_n, lp, up in zip(product_name,weight_str, calculated_weights, net_weights, less_price, unit_price):
        invoice_item = {
            "Product" : product,
            "Quantity_Str" : "  "+wt_s,
            "Quantity" : wt_s.split('*')[0] if '*' in wt_s else sum(1 for num in wt_s.split('+') if num.strip()),
            "Gross_Wt" : float(wt_c),
            "Net_Wt" : wt_n,
            "Less_Wt" : lp,
            "Rate": up,
            "Amount" : round((wt_n)*float(up),2)
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
