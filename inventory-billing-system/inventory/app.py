# imports - standard imports
import os
import sqlite3
from pathlib import Path

# imports - third party imports
from flask import Flask, redirect, render_template, request

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME
print(_DATABASE_PATH)
VIEWS = {
    "Summary": "/",
    "Stock": "/product",
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
        "invoice_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "customer_name TEXT NOT NULL, "
        "prod_id INTEGER NOT NULL, "
        "prod_qty INTEGER NOT NULL, "
        "wt_per_qty INTEGER NOT NULL, "
        "discount INTEGER NOT NULL, "
        "rate INTEGER NOT NULL, "
        "total_amount INTEGER NOT NULL, "
        "status TEXT NOT NULL, "
        "paid_amount INTEGER NOT NULL, "
        "invoice_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(prod_id) REFERENCES products(prod_id), "
        "FOREIGN KEY(customer_name) REFERENCES customer(customer_name)) "
    )
    CUSTOMERS = (
        "customers("
        "customer_name TEXT PRIMARY KEY, "
        "customer_address TEXT NOT NULL, "
        "broker TEXT NOT NULL) "
    )

    with sqlite3.connect(DATABASE_NAME) as conn:
        for table_definition in [PRODUCTS, INVOICES, CUSTOMERS]:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")


app.init_db = init_database


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
                    conn.execute(
                        "UPDATE products SET prod_qty = ? WHERE prod_id = ?",
                        (prod_qty, prod_id),
                    )

                return redirect(VIEWS["Stock"])

            case _:
                return redirect(VIEWS["Summary"])

with app.app_context():
    app.init_db()
