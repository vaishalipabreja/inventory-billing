import sqlite3
import csv
from pathlib import Path
from datetime import datetime

DATABASE_NAME = "inventory.sqlite"
CSV_FILE = "old_balances.csv"

def get_customer_id(conn, customer_name, agent_name):
    """Return customer_id for case-insensitive match"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT customer_id 
        FROM customers 
        WHERE LOWER(customer_name) = LOWER(?) 
          AND LOWER(agent_name) = LOWER(?)
        """,
        (customer_name, agent_name),
    )
    result = cursor.fetchone()
    return result[0] if result else None

def convert_date(date_str):
    """Converts DD/MM/YY → datetime object"""
    return datetime.strptime(date_str.strip(), "%d/%m/%y")

def generate_import_invoice_id(conn, year):
    """Generate invoice id like X-1-2025, based on last counter for that year"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT invoice_id FROM invoices WHERE invoice_id LIKE ? ORDER BY invoice_id DESC LIMIT 1",
        (f"X-%-{year}",)
    )
    last = cursor.fetchone()
    if last:
        # Extract the counter from the last invoice_id
        last_counter = int(last[0].split("-")[1])
        return last_counter + 1
    return 1

def import_old_balances():
    db_path = Path(DATABASE_NAME)
    if not db_path.exists():
        print("❌ Database not found:", DATABASE_NAME)
        return

    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Track missing customers
        missing_customers = set()
        inserted = 0
        year_counters = {}  # counter per year

        with open(CSV_FILE, newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile, delimiter="\t")  # Tab-delimited format

            for row in reader:
                if len(row) != 4:
                    print(f"⚠ Skipping invalid row: {row}")
                    continue

                customer_name, agent_name, total_amount_str, invoice_date_str = row
                customer_name = customer_name.strip()
                agent_name = agent_name.strip()
                total_amount = float(total_amount_str.strip())

                dt = convert_date(invoice_date_str)
                invoice_date = dt.strftime("%Y-%m-%d")
                year = dt.strftime("%Y")

                customer_id = get_customer_id(conn, customer_name, agent_name)
                if not customer_id:
                    missing_customers.add(f"{customer_name} ({agent_name})")
                    continue

                # Prevent duplicate: check if invoice already exists for same customer, amount, date
                cursor.execute("""
                    SELECT 1 FROM invoices
                    WHERE customer_id = ?
                      AND total_amount = ?
                      AND DATE(invoice_time) = ?
                """, (customer_id, total_amount, invoice_date))
                if cursor.fetchone():
                    # Duplicate found, skip
                    continue

                # Determine year counter
                if year not in year_counters:
                    year_counters[year] = generate_import_invoice_id(conn, year)
                counter = year_counters[year]

                invoice_id = f"X-{counter}-{year}"

                # Insert invoice
                cursor.execute("""
                    INSERT INTO invoices
                    (invoice_id, customer_id, total_amount, paid_amount, invoice_time)
                    VALUES (?, ?, ?, ?, ?)
                """, (invoice_id, customer_id, total_amount, 0, invoice_date))

                inserted += 1
                year_counters[year] += 1

        conn.commit()

    # Report missing customers
    if missing_customers:
        print("⚠ Customers not found (rows skipped):")
        for c in sorted(missing_customers):
            print("  ", c)

    print(f"\n✅ Import Complete. {inserted} invoices inserted.\n")


if __name__ == "__main__":
    print("\nStarting Opening Balance Import...\n")
    import_old_balances()
