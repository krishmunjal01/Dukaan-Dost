# app.py — Dukaan-Dost (updated)
import os
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')   # use non-GUI backend (fixes "main thread is not in main loop")
import matplotlib.pyplot as plt
from flask import Flask, request
from dotenv import load_dotenv
import threading
from filelock import FileLock
import datetime

# Load environment
load_dotenv()
app = Flask(__name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN")

# Admin PIN
ADMIN_PIN = "1234"

# State
user_sessions = {}     # per-phone session states
admin_numbers = set()  # phone numbers in admin/owner mode
last_order_status = {} # track last known order statuses for proactive notifications

# Files + locks
PRODUCTS_CSV = "products.csv"
ORDERS_CSV = "orders.csv"
ORDERS_LOCK = "orders.csv.lock"
OFFERS_FILE = "offers.csv"

# Timing (seconds)
CHECK_UPDATES_INTERVAL = 30   # how often to check for status changes and notify
SIMULATOR_INTERVAL = 300      # 5 minutes (Processing -> Shipped -> Delivered)

# Low stock threshold (< 10 triggers alert)
LOW_STOCK_THRESHOLD = 10


# ---------- CSV helpers ----------
def load_products():
    # Ensure products CSV has name, price, stock
    if not os.path.exists(PRODUCTS_CSV):
        df = pd.DataFrame([["sugar", 40, 10], ["rice", 55, 10], ["oil", 120, 10]], columns=["name", "price", "stock"])
        df.to_csv(PRODUCTS_CSV, index=False)
    df = pd.read_csv(PRODUCTS_CSV)
    # If older file missing stock column, add it with default 10
    if "stock" not in df.columns:
        df["stock"] = 10
        df.to_csv(PRODUCTS_CSV, index=False)
    return df


def load_orders():
    """
    Load orders with FileLock, force strings to avoid .0 floats, and fill defaults.
    Ensures 'date' column exists (ISO yyyy-mm-dd).
    """
    with FileLock(ORDERS_LOCK):
        if not os.path.exists(ORDERS_CSV):
            df = pd.DataFrame(columns=["order_id", "product", "quantity", "status", "eta", "payment", "customer", "date"])
            df.to_csv(ORDERS_CSV, index=False)
        else:
            # Read everything as string to avoid numeric conversions that cause .0
            df = pd.read_csv(ORDERS_CSV, dtype=str)
            # Ensure required columns exist
            if "payment" not in df.columns:
                df["payment"] = "Cash/UPI on Delivery"
            if "customer" not in df.columns:
                df["customer"] = ""
            if "eta" not in df.columns:
                df["eta"] = "2 days"
            if "date" not in df.columns:
                df["date"] = datetime.date.today().isoformat()
            # Replace NaN (if any) with defaults
            df = df.fillna({
                "payment": "Cash/UPI on Delivery",
                "customer": "",
                "eta": "2 days",
                "quantity": "0",
                "product": "",
                "date": datetime.date.today().isoformat()
            })
            # Persist if we modified structure
            df.to_csv(ORDERS_CSV, index=False)
    return df


def save_orders(df):
    with FileLock(ORDERS_LOCK):
        df.to_csv(ORDERS_CSV, index=False)


def save_products(df):
    # simple wrapper to persist products
    df.to_csv(PRODUCTS_CSV, index=False)


def add_order(product, qty, customer):
    """
    Add an order to orders.csv and return new order id.
    customer should be the WhatsApp number string (e.g. '9198....')
    Also reduces stock permanently (until restocked). If insufficient stock, returns None.
    """
    # Load and check stock first
    products_df = load_products()
    # Strict matching: product name must match exactly as present in CSV
    if product not in products_df["name"].values:
        # product doesn't exist
        return None

    # Ensure qty int
    try:
        qty_int = int(qty)
    except Exception:
        return None

    current_stock = int(products_df.loc[products_df["name"] == product, "stock"].values[0])
    if qty_int > current_stock:
        # insufficient stock
        return None

    # reduce stock
    products_df.loc[products_df["name"] == product, "stock"] = current_stock - qty_int
    save_products(products_df)

    # low stock alert
    new_stock = current_stock - qty_int
    if new_stock < LOW_STOCK_THRESHOLD:
        for admin_num in admin_numbers:
            try:
                send_whatsapp_message(admin_num,
                                      f"⚠️ {product.capitalize()} only {new_stock} left, reorder soon!")
            except Exception as e:
                print("Failed sending low stock alert:", e)

    # Now create order record
    df = load_orders()
    if df.empty:
        new_id = 10001
    else:
        try:
            max_id = int(df["order_id"].astype(int).max())
            new_id = max_id + 1
        except Exception:
            new_id = df.shape[0] + 10001
    new_row = {
        "order_id": str(new_id),
        "product": str(product),
        "quantity": str(qty_int),
        "status": "Processing",
        "eta": "2 days",
        "payment": "Cash/UPI on Delivery",
        "customer": str(customer),
        "date": datetime.date.today().isoformat()
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_orders(df)
    return str(new_id)


# ---------- Messaging helpers ----------
def send_whatsapp_message(to, message):
    to = str(to)
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    try:
        r = requests.post(url, headers=headers, json=payload)
        print("Text response:", r.status_code, r.text)
        return r.json()
    except Exception as e:
        print("send_whatsapp_message error:", e)
        return {}


def upload_media(file_path):
    """
    Upload file to /media and return media_id.
    """
    abs_path = os.path.abspath(file_path)
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    # Force image MIME type so API accepts it
    files = {"file": (os.path.basename(abs_path), open(abs_path, "rb"), "image/png")}
    data = {"messaging_product": "whatsapp"}
    r = requests.post(url, headers=headers, files=files, data=data)
    print("Upload response:", r.status_code, r.text)
    # Return id if present
    try:
        return r.json().get("id")
    except Exception:
        return None


def send_image_by_media_id(to, media_id, caption=""):
    """
    Send image using media_id obtained from /media.
    """
    to = str(to)
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, "caption": caption}
    }
    r = requests.post(url, headers=headers, json=payload)
    print("Send image response:", r.status_code, r.text)
    return r.json()


def send_image(to, file_path, caption=""):
    """
    Convenience: upload then send. If upload fails, notify user.
    """
    media_id = upload_media(file_path)
    if not media_id:
        send_whatsapp_message(to, "⚠️ Failed to upload image.")
        return None
    return send_image_by_media_id(to, media_id, caption)


# ---------- Chart helpers ----------
def generate_sales_chart():
    """
    Generate a sales chart (quantity sold per product) from orders.csv.
    Returns path to saved chart or None.
    """
    try:
        df = load_orders()
        if df.empty:
            return None
        # Ensure quantity numeric
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
        sales = df.groupby("product", as_index=False)["quantity"].sum()
        if sales.empty:
            return None
        plt.figure(figsize=(6, 4))
        plt.bar(sales["product"], sales["quantity"])
        plt.xlabel("Product")
        plt.ylabel("Quantity Sold")
        plt.title("Sales Insights (All-time)")
        plt.tight_layout()
        chart_path = "sales.png"
        plt.savefig(chart_path)
        plt.close()
        return chart_path
    except Exception as e:
        print("generate_sales_chart error:", e)
        return None


# ---------- Reports ----------
def generate_pnl_summary():
    """
    P&L only for today's orders.
    We assume cost = 70% of price (as placeholder) unless you provide exact cost column.
    """
    try:
        df = load_orders()
        if df.empty:
            return "📊 No sales yet."
        today = datetime.date.today().isoformat()
        today_orders = df[df["date"] == today]
        if today_orders.empty:
            return "📊 No sales today."
        products = load_products()
        # merge on product name
        merged = today_orders.merge(products, left_on="product", right_on="name", how="left")
        merged["quantity"] = pd.to_numeric(merged["quantity"], errors="coerce").fillna(0)
        merged["price"] = pd.to_numeric(merged["price"], errors="coerce").fillna(0)
        # assume cost is 70% of price
        merged["revenue"] = merged["quantity"] * merged["price"]
        merged["cost"] = merged["quantity"] * (merged["price"] * 0.7)
        merged["profit"] = merged["revenue"] - merged["cost"]
        total_rev = merged["revenue"].sum()
        total_cost = merged["cost"].sum()
        total_profit = merged["profit"].sum()
        return (f"💰 Today's Summary:\n"
                f"Revenue: ₹{total_rev:.2f}\n"
                f"Cost: ₹{total_cost:.2f}\n"
                f"Profit: ₹{total_profit:.2f}")
    except Exception as e:
        print("P&L error:", e)
        return "⚠️ Error generating P&L."


def generate_demand_insights_text():
    """
    All-time demand insights (text).
    """
    try:
        orders = load_orders()
        if orders.empty:
            return "📊 No demand data yet."
        orders["quantity"] = pd.to_numeric(orders["quantity"], errors="coerce").fillna(0)
        demand = orders.groupby("product")["quantity"].sum().sort_values(ascending=False)
        if demand.empty:
            return "📊 No demand data yet."
        top = demand.index[0]
        bottom = demand.index[-1]
        return f"🔥 In-demand: {top}\n❄️ Not in demand: {bottom}"
    except Exception as e:
        print("Demand error:", e)
        return "⚠️ Error generating demand insights."


# ---------- Offers helpers ----------
def get_offers_text():
    if os.path.exists(OFFERS_FILE):
        with open(OFFERS_FILE, "r") as f:
            offers = [o for o in (line.strip() for line in f.readlines()) if o]
        if offers:
            return "🎉 Today's Offers:\n- " + "\n- ".join(offers)
    # fallback default offers (if file missing or empty)
    return ("🎉 Today's Offers:\n- 10% off on Rice\n- Buy 1 Get 1 Free on Sugar\n- Flat ₹20 off on Oil")


# ---------- Proactive updates ----------
def check_order_updates():
    """
    Check orders.csv for status changes and notify respective customers.
    Runs periodically in its own Timer thread.
    """
    global last_order_status
    try:
        df = load_orders()
        for _, row in df.iterrows():
            oid = str(row["order_id"])
            current_status = str(row.get("status", "")).strip()
            if oid not in last_order_status:
                last_order_status[oid] = current_status
                continue
            if last_order_status[oid] != current_status:
                last_order_status[oid] = current_status
                customer = str(row.get("customer", "")).strip()
                # don't attempt to send if customer missing
                if customer:
                    send_whatsapp_message(
                        customer,
                        f"📦 Order Update!\n"
                        f"Your Order #{row['order_id']} ({row['product']} x{row['quantity']}) is now {row['status']} 🚚\n"
                        f"📅 Estimated Delivery Time: {row.get('eta', '2 days')}\n"
                        f"💰 Payment Mode: {row.get('payment', 'Cash/UPI on Delivery')}"
                    )
    except Exception as e:
        print("Error checking updates:", e)
    # schedule next run
    threading.Timer(CHECK_UPDATES_INTERVAL, check_order_updates).start()


# ---------- Simulator ----------
def simulate_order_flow():
    """
    Simulate order progression:
      - Find first Processing -> set to Shipped (eta updated)
      - Else, find first Shipped -> set to Delivered
    Runs every SIMULATOR_INTERVAL seconds (5 minutes as requested).
    """
    try:
        df = load_orders()
        if df.empty:
            pass
        else:
            updated = False
            for idx, row in df.iterrows():
                status = str(row.get("status", "")).strip()
                if status.lower() == "processing":
                    df.at[idx, "status"] = "Shipped"
                    df.at[idx, "eta"] = "Tomorrow"
                    save_orders(df)
                    print(f"Simulated: Order {row['order_id']} -> Shipped")
                    updated = True
                    break
                elif status.lower() == "shipped":
                    df.at[idx, "status"] = "Delivered"
                    df.at[idx, "eta"] = "Delivered Today"
                    save_orders(df)
                    print(f"Simulated: Order {row['order_id']} -> Delivered")
                    updated = True
                    break
            if not updated:
                # nothing to simulate this round
                pass
    except Exception as e:
        print("Error simulating orders:", e)

    # schedule next run (5 minutes)
    threading.Timer(SIMULATOR_INTERVAL, simulate_order_flow).start()


# ---------- Simple Menus ----------
def customer_menu_text():
    return ("👋 Welcome to Dukaan-Dost!\nPlease choose an option:\n\n"
            "1️⃣ View Products\n"
            "2️⃣ Check Order Status\n"
            "3️⃣ Place New Order\n"
            "4️⃣ Offers & Discounts\n"
            "5️⃣ Talk to Support")


def admin_menu_text():
    return ("👨‍💼 Owner Menu:\n"
            "1️⃣ View Products\n"
            "2️⃣ View Orders\n"
            "3️⃣ Sales Insights\n"
            "4️⃣ Profit & Loss Summary (today)\n"
            "5️⃣ Stock Levels\n"
            "6️⃣ Demand Insights (all-time)\n"
            "Type commands (strict lowercase):\n"
            "- restock product,qty   (example: restock rice,50)\n"
            "- add offer <text>      (example: add offer 5% off on Oil)\n"
            "- remove offer <text>   (example: remove offer 5% off on Oil)\n"
            "- offers                (view current offers)\n"
            "Type 'exit' to leave admin mode.")


# ---------- Admin command handler ----------
def handle_admin_command_text(from_number, text):
    """Return True if handled an admin-specific command (besides 1/2/3)."""
    # strict restock: format must be exactly 'restock product,qty' (lowercase), with comma
    if text.startswith("restock "):
        args = text[len("restock "):].strip()
        if "," not in args:
            send_whatsapp_message(from_number, "⚠️ Wrong format. Use: restock product,qty  (example: restock rice,50)")
            return True
        item, qty_str = [p.strip() for p in args.split(",", 1)]
        try:
            qty = int(qty_str)
        except Exception:
            send_whatsapp_message(from_number, "⚠️ Quantity must be an integer.")
            return True
        products = load_products()
        # strict name match
        if item in products["name"].values:
            products.loc[products["name"] == item, "stock"] = products.loc[products["name"] == item, "stock"].astype(int) + qty
            save_products(products)
            new_stock = int(products.loc[products["name"] == item, "stock"].values[0])
            send_whatsapp_message(from_number, f"✅ Restocked {item} by {qty}. New stock: {new_stock}")
        else:
            send_whatsapp_message(from_number, f"⚠️ Product '{item}' not found.")
        return True

    if text.startswith("add offer"):
        offer = text.replace("add offer", "", 1).strip()
        if not offer:
            send_whatsapp_message(from_number, "⚠️ Usage: add offer <offer text>")
            return True
        # append to offers file
        try:
            with open(OFFERS_FILE, "a") as f:
                f.write(offer + "\n")
            send_whatsapp_message(from_number, f"✅ Offer '{offer}' added.")
        except Exception as e:
            send_whatsapp_message(from_number, f"⚠️ Could not add offer: {e}")
        return True

    if text.startswith("remove offer"):
        offer = text.replace("remove offer", "", 1).strip()
        if not offer:
            send_whatsapp_message(from_number, "⚠️ Usage: remove offer <offer text>")
            return True
        if not os.path.exists(OFFERS_FILE):
            send_whatsapp_message(from_number, "⚠️ No offers file exists.")
            return True
        with open(OFFERS_FILE, "r") as f:
            offers = [line.strip() for line in f.readlines() if line.strip()]
        if offer in offers:
            offers.remove(offer)
            with open(OFFERS_FILE, "w") as f:
                for o in offers:
                    f.write(o + "\n")
            send_whatsapp_message(from_number, f"✅ Offer '{offer}' removed.")
        else:
            send_whatsapp_message(from_number, f"⚠️ Offer '{offer}' not found.")
        return True

    if text == "offers":
        send_whatsapp_message(from_number, get_offers_text())
        return True

    return False


# ---------- Routes ----------
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def incoming():
    data = request.get_json()
    print("Webhook:", data)

    if not (data and "entry" in data):
        return "ok", 200

    # Basic parsing of single-message events
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for message in messages:
                from_number = message.get("from")
                session_key = from_number
                # keep incoming normalized to lowercase as earlier code did (strict lower commands required)
                text = message.get("text", {}).get("body", "").strip().lower()
                print(f"[{from_number}] {text} (session={user_sessions.get(session_key)})")

                # ---------- Admin login ----------
                if text.startswith("admin"):
                    parts = text.split()
                    if len(parts) >= 2 and parts[1] == ADMIN_PIN:
                        admin_numbers.add(from_number)
                        send_whatsapp_message(from_number, "✅ Admin mode activated.\n\n" + admin_menu_text())
                    else:
                        send_whatsapp_message(from_number, "❌ Wrong admin PIN.")
                    continue

                if text == "exit" and from_number in admin_numbers:
                    admin_numbers.discard(from_number)
                    send_whatsapp_message(from_number, "👋 Exited admin mode.")
                    continue

                # ---------- Admin actions ----------
                if from_number in admin_numbers:
                    # first, check our admin textual commands (restock, add/remove offer, offers)
                    handled = handle_admin_command_text(from_number, text)
                    if handled:
                        continue

                    # keep your original admin numeric options (1/2/3) intact
                    if text == "1":
                        df = load_products()
                        products = "\n".join([f"- {r['name'].capitalize()} ₹{r['price']} (stock: {r['stock']})" for _, r in df.iterrows()])
                        send_whatsapp_message(from_number, f"📦 Products:\n{products}")
                        continue
                    if text == "2":
                        df = load_orders()
                        if df.empty:
                            send_whatsapp_message(from_number, "📭 No orders yet.")
                        else:
                            lines = [f"#{r['order_id']}: {r['product']} x{r['quantity']} - {r['status']}" for _, r in df.iterrows()]
                            send_whatsapp_message(from_number, "📝 Orders:\n" + "\n".join(lines))
                        continue
                    if text == "3":
                        chart = generate_sales_chart()
                        if chart:
                            send_image(from_number, chart, "📊 Sales Insights")
                        else:
                            send_whatsapp_message(from_number, "⚠️ No sales data yet.")
                        continue

                    # New admin menu actions: 4 -> P&L (today), 5 -> Stock Levels, 6 -> Demand insights (all time)
                    if text == "4":
                        send_whatsapp_message(from_number, generate_pnl_summary())
                        continue
                    if text == "5":
                        products = load_products()
                        stock_list = "\n".join([f"- {r['name'].capitalize()}: {int(r['stock'])} left" for _, r in products.iterrows()])
                        send_whatsapp_message(from_number, f"📦 Stock Levels:\n{stock_list}")
                        continue
                    if text == "6":
                        # send text demand insights and also try to send chart
                        send_whatsapp_message(from_number, generate_demand_insights_text())
                        chart = generate_sales_chart()
                        if chart:
                            send_image(from_number, chart, "📊 All-time Demand Chart")
                        continue

                    send_whatsapp_message(from_number, "⚠️ Invalid admin option. Use 1/2/3/4/5/6, 'offers', or admin commands, or 'exit'.")
                    continue

                # ---------- Customer flow ----------
                # 1) Awaiting order ID input (after selecting option 2)
                if user_sessions.get(session_key) == "awaiting_order_id":
                    user_sessions.pop(session_key, None)
                    order_id = text
                    df = load_orders()
                    # strict order id check: digits only
                    if not order_id.isdigit():
                        send_whatsapp_message(from_number, "⚠️ Order ID should contain digits only.")
                        continue
                    if order_id in df["order_id"].astype(str).values:
                        row = df[df["order_id"].astype(str) == order_id].iloc[0]
                        send_whatsapp_message(from_number,
                            f"✅ Order #{row['order_id']} ({row['product']} x{row['quantity']}) is {row['status']} 🚚\n"
                            f"📅 Estimated Delivery Time: {row.get('eta','2 days')}\n"
                            f"💰 Payment Mode: {row.get('payment','Cash/UPI on Delivery')}"
                        )
                    else:
                        send_whatsapp_message(from_number, "⚠️ Order not found.")
                    continue

                # 2) Awaiting new order (after selecting option 3)
                if user_sessions.get(session_key) == "awaiting_new_order":
                    user_sessions.pop(session_key, None)
                    if "," in text:
                        try:
                            product, qty = [t.strip() for t in text.split(",", 1)]
                            # strict: product must match exactly in products list (case-sensitive in CSV). We keep text lowercased as earlier.
                            new_id = add_order(product, int(qty), from_number)
                            if not new_id:
                                send_whatsapp_message(from_number, "⚠️ Could not place order. Product may not exist, format wrong, or insufficient stock.")
                            else:
                                send_whatsapp_message(from_number,
                                    f"📝 Order placed: {qty} {product}\n"
                                    f"Your Order ID: {new_id}\n"
                                    f"📅 Estimated Delivery Time: 2 days\n"
                                    f"💰 Payment Mode: Cash/UPI on Delivery")
                                send_whatsapp_message(from_number,
                                    "🙏 Thank you for shopping with Dukaan-Dost!\nWe’ll notify you when your order is out for delivery. 🚚")
                        except Exception as e:
                            print("Error saving order:", e)
                            send_whatsapp_message(from_number, "⚠️ Could not save order. Try again.")
                    else:
                        send_whatsapp_message(from_number, "⚠️ Wrong format. Send: product,quantity")
                    continue

                # 3) Menu / commands
                if text in ["hi", "hello", "hey"]:
                    user_sessions.pop(session_key, None)
                    send_whatsapp_message(from_number, customer_menu_text())
                    continue

                if text == "1":
                    df = load_products()
                    products = "\n".join([f"- {r['name'].capitalize()} ₹{r['price']}" for _, r in df.iterrows()])
                    send_whatsapp_message(from_number, f"🛒 Products:\n{products}")
                    continue

                if text == "2":
                    user_sessions[session_key] = "awaiting_order_id"
                    send_whatsapp_message(from_number, "📦 Please enter your Order ID:")
                    continue

                if text == "3":
                    user_sessions[session_key] = "awaiting_new_order"
                    send_whatsapp_message(from_number, "📝 Type order as: product,quantity\nExample: rice,2")
                    continue

                if text == "4":
                    # show dynamic offers from OFFERS_FILE if present
                    send_whatsapp_message(from_number, get_offers_text())
                    continue

                if text == "5":
                    send_whatsapp_message(from_number, "📞 Talk to Support: +91-9996033812")
                    continue

                if text.isdigit():
                    send_whatsapp_message(from_number, "⚠️ Invalid option. Reply with 1-5 or type 'hi' for menu.")
                    continue

                send_whatsapp_message(from_number, "⚠️ Invalid choice. Reply with 1-5 or 'hi' for menu.")

    return "ok", 200


if __name__ == "__main__":
    # start background jobs
    check_order_updates()
    simulate_order_flow()
    app.run(port=5000, debug=True)
