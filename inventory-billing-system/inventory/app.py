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
    "Summary": "/",
    "Stock": "/product",
    "Customer": "/customer",
    "Invoice": "/invoice"
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
    # Generate a unique identifier using uuid
    unique_id = str(uuid.uuid4().hex)[:8]

    # Get the current timestamp in a formatted string
    timestamp = datetime.now().strftime('%d-%m-%Y')

    # Combine the unique id and timestamp to create a unique bill number
    bill_number = f'{timestamp}_{unique_id}'
    return bill_number

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
def summary():
    with sqlite3.connect(DATABASE_NAME) as conn:
        invoices = conn.execute("SELECT * FROM invoices").fetchall()
        products = conn.execute("SELECT * FROM products").fetchall()
        q_data = conn.execute(
            "SELECT prod_name, prod_qty FROM products"
        ).fetchall()

    return render_template(
        "index.jinja",
        link=VIEWS,
        title="Summary",
        invoices=invoices,
        products=products,
        summary=q_data,
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
            customer_name, agent_name = request.form["customer_name"], request.form["agent_name"]
            transaction_allowed = customer_name not in EMPTY_SYMBOLS and agent_name not in EMPTY_SYMBOLS

            if transaction_allowed:
                conn.execute(
                    "INSERT INTO customers (customer_name, agent_name) VALUES (?, ?)",
                    (customer_name, agent_name),
                )
                return redirect(VIEWS["Customer"])

        customers = conn.execute("SELECT * FROM customers").fetchall()

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

            customer_name = request.form.get('customer_name')
            product_name = request.form.getlist('product_name')
            weight_str = request.form.getlist('weight_str')
            unit_price = request.form.getlist('unit_price')
            less_price = request.form.getlist('less_price')
            paid_amount = request.form.get('paid_amount')
            total_amount = 0
            calculated_weights=[]
            net_weights=[]
            for weight, up, lp in zip(weight_str, unit_price, less_price):
                calculated_weights.append(calculate(weight))
                net_weights.append(calculate(weight)-float(lp))
                total_amount += (calculate(weight)-float(lp))*float(up)

            agent=conn.execute("SELECT agent_name FROM customers where customer_name = ?",
                                 (customer_name,)).fetchall() 
            agent_name = agent[0][0]
            transaction_allowed = customer_name not in EMPTY_SYMBOLS and total_amount not in EMPTY_SYMBOLS
            for weight,product in zip(calculated_weights,product_name):
                product_quantity=conn.execute("SELECT prod_qty FROM products where prod_name = ?",
                                 (product,)).fetchall() 
                transaction_allowed = weight<product_quantity[0][0]
                if(weight<product_quantity[0][0]):
                        conn.execute("UPDATE products SET prod_qty = ? WHERE prod_name = ?",(product_quantity[0][0]-weight, product,))
            
            if transaction_allowed:
                conn.execute(
                    "INSERT INTO invoices (invoice_id, customer_name, total_amount, paid_amount) VALUES (?, ?, ?, ?)",
                    (invoice_id, customer_name, total_amount, paid_amount),
                )
                return generate_bill(invoice_id, customer_name, agent_name, product_name, weight_str, calculated_weights, net_weights, less_price, unit_price, total_amount)
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

def generate_bill(invoice_id, customer_name, agent_name, product_name, weight_str, calculated_weights, net_weights, less_price, unit_price, total_amount):
    invoice_data = {
        'invoice_number': invoice_id,
        'date': date.today().strftime('%d-%m-%Y'),
        'customer_name': customer_name,
        'agent_name': agent_name,
        'address': customer_name.split(',',1)[0] if len(customer_name.split(',',1))>1 else "",
        'total': total_amount,
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
