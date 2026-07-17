import os
import re
import random
import sqlite3
import threading
import requests
import telepot
from flask import Flask, request, jsonify

# 1. Initialize Flask Core App
app = Flask(__name__)

# 2. API Configuration (Always pull from Environment Variables in Production)
API_TOKEN = os.environ.get("API_TOKEN", "YOUR_BOT_TOKEN")
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "YOUR_PAYSTACK_SECRET")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-production-domain.com/webhook")

# Configured Administrators
ADMINS = [6830221233, 7527898347, 7565569911]
bot = telepot.Bot(API_TOKEN)

# Database Isolation & Thread Safety Configuration
DB_FILE = 'bot_data.db'
db_lock = threading.Lock()

def get_db():
    """Generates a unique, request-safe database connection for Flask threads."""
    db_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    return db_conn

# Initialize Schema Blueprint
def init_db():
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS users_details (user_id INTEGER PRIMARY KEY, email TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, issue TEXT, status TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, exam_type TEXT, 
                quantity INTEGER, amount REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, exam_type TEXT, 
                transaction_ref TEXT, status TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referred_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS points (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS user_sessions (user_id INTEGER PRIMARY KEY, email TEXT, exam_type TEXT, current_action TEXT)''')
            conn.commit()

init_db()

# Product Inventory Catalog
EXAM_PRICES = {"WASSCE": 26.5, "BECE": 26.5, "NOVDEC": 26.5}

# Configured Referral Engagement Platform Tasks
TASKS = {
    "Join @cherryboy000": "https://t.me/cherryboy000",
    "Join @tyreseosei": "https://t.me/tyreseosei"
}

# --- Core Helper Logic ---

def show_initial_menu(chat_id, user_id):
    if user_id in ADMINS:
        keyboard = [
            ["🛒 Buy Checker"], ["📊 Sales Today", "📢 Broadcast"],
            ["📬 Support", "📋 Check Ticket"], ["🔗 Invite Friends", "🏆 Leaderboard"],
            ["📋 Tasks", "💳 Pending Payments"], ["📧 Set Email"],
            ["➕ Add BECE Codes", "➕ Add WASSCE Codes", "➕ Add NOVDEC Codes"], ["📄 View Checker Codes"]
        ]
    else:
        keyboard = [
            ["🛒 Buy Checker"], ["📬 Support", "📋 Check Ticket"],
            ["🔗 Invite Friends", "🏆 Leaderboard"], ["📋 Tasks", "💳 Pending Payments"],
            ["📧 Set Email"]
        ]
    reply_markup = {"keyboard": keyboard, "resize_keyboard": True}
    bot.sendMessage(chat_id, "Welcome to MovaConsult System:", reply_markup=reply_markup)

def add_checker_codes(chat_id, exam_type):
    bot.sendMessage(chat_id, f"📥 Send the checker codes for **{exam_type}** as a text message. Ensure each code sits on a completely new line.")
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO user_sessions (user_id, exam_type, current_action) VALUES (?, ?, ?)", (chat_id, exam_type, "adding_codes"))
            conn.commit()

def save_checker_codes(chat_id, codes):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT exam_type FROM user_sessions WHERE user_id = ?", (chat_id,))
            result = cursor.fetchone()

    if not result or not result[0]:
        bot.sendMessage(chat_id, "❌ Critical Error: Session expired or invalid. Re-select administrative tools.")
        return

    exam_type = result[0]
    os.makedirs("CTechPulse", exist_ok=True)
    file_path = f"CTechPulse/{exam_type.lower()}_checkers.txt"

    try:
        cleaned_codes = [line.strip() for line in codes if line.strip()]
        if not cleaned_codes:
            return
        with open(file_path, "a") as file:
            file.write("\n".join(cleaned_codes) + "\n")
        bot.sendMessage(chat_id, f"✅ Successfully loaded {len(cleaned_codes)} vouchers into our local {exam_type} system.")
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (chat_id,))
                conn.commit()
    except Exception as e:
        bot.sendMessage(chat_id, f"❌ Failed to commit new codes: {e}")

def view_checker_codes(chat_id):
    inline_keyboard = [
        [{"text": "BECE Inventory", "callback_data": "view_BECE"}],
        [{"text": "WASSCE Inventory", "callback_data": "view_WASSCE"}],
        [{"text": "NOVDEC Inventory", "callback_data": "view_NOVDEC"}],
        [{"text": "⬅️ Return to Main Menu", "callback_data": "back_to_menu"}]
    ]
    bot.sendMessage(chat_id, "Select target batch catalog to monitor stock layers:", reply_markup={"inline_keyboard": inline_keyboard})

def display_checker_codes(chat_id, exam_type):
    file_path = f"CTechPulse/{exam_type.lower()}_checkers.txt"
    try:
        if not os.path.exists(file_path):
            bot.sendMessage(chat_id, f"❌ Stock database empty or trace missing for {exam_type}.")
            return
        with open(file_path, "r") as file:
            codes = [line.strip() for line in file.readlines() if line.strip()]
        if codes:
            formatted_string = "\n".join([f"• `{c}`" for c in codes[:30]])
            bot.sendMessage(chat_id, f"📊 **Available {exam_type} Records ({len(codes)} Left):**\n\n{formatted_string}\n\n_(Truncated to first 30 listings for rendering security)_", parse_mode="Markdown")
        else:
            bot.sendMessage(chat_id, f"❌ Out of Stock: {exam_type} pool contains 0 active records.")
    except Exception as e:
        bot.sendMessage(chat_id, f"❌ Failed data query extraction: {e}")

def greetings(chat_id):
    try:
        user_info = bot.getChat(chat_id)
        username = user_info.get('username', f"User {chat_id}")
    except:
        username = f"User {chat_id}"

    welcome_text = (
        f"🎓 **Welcome, {username}!** 😊\n\n"
        f"🚀 **MOVACONSULT Bot** - Your automated agent for direct educational verification assets.\n\n"
        f"📚 **Our Active Matrix:**\n"
        f"✅ Buy valid BECE, WASSCE, and NOVDEC checker tokens.\n"
        f"✅ Immediate programmatic delivery here.\n\n"
        f"🛠️ **Command Protocol:**\n"
        f"👫 Share your personalized link via menu to generate performance rewards.\n"
        f"🏆 Climb up our global activity Leaderboard.\n"
        f"📬 Access programmatic Support channels whenever nodes stall.\n\n"
        f"🔒 All transactions are end-to-end processing gateways."
    )
    img_path = "CTechPulse/checker.png"
    if os.path.exists(img_path):
        try: bot.sendPhoto(chat_id, photo=open(img_path, "rb"))
        except: pass
    bot.sendMessage(chat_id, welcome_text, parse_mode="Markdown")

def generate_referral_link(chat_id):
    bot_info = bot.getMe()
    bot_username = bot_info['username']
    referral_link = f"https://t.me/{bot_username}?start=ref_{chat_id}"
    bot.sendMessage(chat_id, f"🔗 **Your Direct Affiliate Node Asset:**\n`{referral_link}`\n\nDistribute this address. If incoming profiles register, you gain resource score credits.", parse_mode="Markdown")

def handle_referral(referrer_id, referred_id):
    if int(referrer_id) == int(referred_id):
        return
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM referrals WHERE referred_id = ?", (referred_id,))
            if cursor.fetchone(): return 

            cursor.execute("INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (referrer_id, referred_id))
            cursor.execute("INSERT INTO points (user_id, points) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET points = points + 1", (referrer_id,))
            conn.commit()
    try: bot.sendMessage(referrer_id, "🎉 **Affiliate Event Detected!** An incoming connection joined through your tracking node. +1 Point awarded.")
    except: pass

def show_leaderboard(chat_id):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, COALESCE(points, 0) AS score FROM points ORDER BY score DESC LIMIT 10')
            leaderboard = cursor.fetchall()
    if not leaderboard:
        bot.sendMessage(chat_id, "🏆 **Leaderboard Empty:** No metrics found.")
        return
    message = "🏆 **Global Activity Matrix Top 10:**\n\n"
    for rank, (user_id, points) in enumerate(leaderboard, start=1):
        message += f"{rank}. Profile ID: `{user_id}` — **{points} performance points**\n"
    bot.sendMessage(chat_id, message, parse_mode="Markdown")

def show_tasks_menu(chat_id):
    if not TASKS:
        bot.sendMessage(chat_id, "📋 No global tasks are registered inside the structural runtime at the moment.")
        return
    task_name, task_url = random.choice(list(TASKS.items()))
    clean_callback = task_name.replace(" ", "_").replace("@", "")
    inline_keyboard = [
        [{"text": f"🌐 Open {task_name}", "url": task_url}],
        [{"text": "✅ Verify Tasks Sequence", "callback_data": f"verify_task_{clean_callback}"}]
    ]
    bot.sendMessage(chat_id, "📋 **Ecosystem Core Objective Tasks:**\nExecute assignments through external endpoints below.", reply_markup={"inline_keyboard": inline_keyboard})

def verify_task(chat_id, task_callback_string):
    matched_channel = None
    for official_title in TASKS.keys():
        comp_string = official_title.replace(" ", "_").replace("@", "")
        if comp_string == task_callback_string:
            matched_channel = "@" + official_title.split("@")[-1] if "@" in official_title else official_title
            break
    if not matched_channel:
        bot.sendMessage(chat_id, "❌ Task parsing context dropped.")
        return
    try:
        response = bot.getChatMember(matched_channel, chat_id)
        if response['status'] in ['member', 'administrator', 'creator']:
            with db_lock:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO points (user_id, points) VALUES (?, 5) ON CONFLICT(user_id) DO UPDATE SET points = points + 5", (chat_id,))
                    conn.commit()
            bot.sendMessage(chat_id, "✅ **Verification Confirmed!** Membership records matched. +5 points loaded.")
        else:
            bot.sendMessage(chat_id, "❌ **Verification Terminated:** Access authorization signature missing.")
    except:
        bot.sendMessage(chat_id, "❌ **System Notice:** Automatic channel check failed.")

def purchase_flow(chat_id):
    inline_keyboard = [
        [{"text": "⚡ BECE Checker Token", "callback_data": "exam_BECE"}],
        [{"text": "⚡ WASSCE Checker Token", "callback_data": "exam_WASSCE"}],
        [{"text": "⚡ NOVDEC Checker Token", "callback_data": "exam_NOVDEC"}],
        [{"text": "⬅️ Cancel Transaction", "callback_data": "back_to_menu"}]
    ]
    bot.sendMessage(chat_id, "🛒 **Asset Generation Matrix:** Choose required verification context:", reply_markup={"inline_keyboard": inline_keyboard})

def extract_and_send_vouchers(chat_id, exam_type, quantity):
    file_path = f"CTechPulse/{exam_type.lower()}_checkers.txt"
    try:
        if not os.path.exists(file_path):
            bot.sendMessage(chat_id, "❌ Critical System Fault: Selected batch allocation layer missing.")
            return False
        with open(file_path, "r") as file:
            lines = [line.strip() for line in file.readlines() if line.strip()]
        if len(lines) < quantity:
            bot.sendMessage(chat_id, f"❌ Stock short circuit: Only {len(lines)} items left inside the {exam_type} system layer.")
            return False
        selected_vouchers = lines[:quantity]
        remaining_vouchers = lines[quantity:]
        with open(file_path, "w") as file:
            file.write("\n".join(remaining_vouchers) + "\n" if remaining_vouchers else "")
        bot.sendMessage(chat_id, "🔑 **Your Requested Voucher Assets Have Cleared Processing:**")
        for index, code in enumerate(selected_vouchers, start=1):
            pin, serial = code.split(" ", 1) if " " in code else (code, "N/A")
            bot.sendMessage(chat_id, f"📦 **Allocation #{index}**\n• **Serial:** `{serial}`\n• **Pin/Secret:** `{pin}`", parse_mode="Markdown")
        bot.sendMessage(chat_id, "🌐 Head over to `ghana.waecdirect.org` to process compilation slips.")
        return True
    except Exception as e:
        bot.sendMessage(chat_id, f"❌ Failure during asset extraction sequence: {e}")
        return False

def show_initiate_button(chat_id, quantity, exam_type):
    inline_keyboard = [
        [{"text": "🔐 Link directly to Paystack Gate", "callback_data": f"confirm_payment_{quantity}_{exam_type}"}],
        [{"text": "❌ Terminate Purchase Sequence", "callback_data": "cancel_payment"}]
    ]
    bot.sendMessage(chat_id, "Proceed with verification sequence below:", reply_markup={"inline_keyboard": inline_keyboard})

def show_quantity_buttons(chat_id):
    row1 = [{"text": str(i), "callback_data": f"quantity_{i}"} for i in range(1, 6)]
    row2 = [{"text": str(i), "callback_data": f"quantity_{i}"} for i in range(6, 11)]
    inline_keyboard = [row1, row2, [{"text": "⬅️ Cancel", "callback_data": "back_to_menu"}]]
    bot.sendMessage(chat_id, "Select quantity size:", reply_markup={"inline_keyboard": inline_keyboard})

def summary(chat_id, email, quantity, exam_type):
    if exam_type not in EXAM_PRICES: return
    total_cost = EXAM_PRICES[exam_type] * quantity
    invoice = (
        f"📋 **Invoicing Verification Manifest:**\n\n"
        f"• **Asset Focus:** `{exam_type}`\n"
        f"• **Volume Count:** `{quantity}` units\n"
        f"• **Routing Email:** `{email}`\n"
        f"• **Total Aggregate Charge:** **GH₵ {total_cost:.2f}**\n\n"
        f"🚀 Delivery maps directly to this console frame instantly upon network clearance."
    )
    bot.sendMessage(chat_id, invoice, parse_mode="Markdown")
    show_initiate_button(chat_id, quantity, exam_type)

def generate_payment_link(chat_id, email, quantity, exam_type):
    if exam_type not in EXAM_PRICES: return
    file_path = f"CTechPulse/{exam_type.lower()}_checkers.txt"
    if not os.path.exists(file_path):
        bot.sendMessage(chat_id, "❌ Inventory layer not initialized.")
        return
    with open(file_path, "r") as file:
        lines = [l.strip() for l in file.readlines() if l.strip()]
    if len(lines) < quantity:
        bot.sendMessage(chat_id, f"❌ Supply failure: Only {len(lines)} assets exist.")
        return

    total_amount = EXAM_PRICES[exam_type] * quantity
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}
    payment_payload = {
        "email": email, "amount": int(total_amount * 100), "currency": "GHS",
        "metadata": {"custom_fields": [{"display_name": "Exam", "variable_name": "exam", "value": exam_type}]}
    }
    try:
        res = requests.post("https://api.paystack.co/transaction/initialize", json=payment_payload, headers=headers)
        data = res.json()
        if data.get("status"):
            auth_url = data["data"]["authorization_url"]
            ref_node = data["data"]["reference"]
            with db_lock:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO transactions (user_id, exam_type, transaction_ref, status) VALUES (?, ?, ?, 'Pending')",
                                   (chat_id, f"{exam_type}:{quantity}", ref_node))
                    conn.commit()
            pay_markup = {"inline_keyboard": [[{"text": "💳 Pay Securely Now", "url": auth_url}]]}
            bot.sendMessage(chat_id, "✅ **Secure billing gateway generated.**", reply_markup=pay_markup)
            bot.sendMessage(chat_id, f"ℹ️ **Payment Node Token:**\n`{ref_node}`\n\nManual verify command:\n/verify_payment `{ref_node}`", parse_mode="Markdown")
        else:
            bot.sendMessage(chat_id, f"❌ Gateway Refused: {data.get('message')}")
    except Exception as e:
        bot.sendMessage(chat_id, f"❌ Gateway connection error: {e}")

def verify_payment(chat_id, transaction_ref):
    url = f"https://api.paystack.co/transaction/verify/{transaction_ref}"
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, exam_type FROM transactions WHERE transaction_ref = ?", (transaction_ref,))
            record = cursor.fetchone()
    if not record:
        bot.sendMessage(chat_id, "❌ System lookup fail: Invalid transaction token.")
        return
    if record[0] == "Completed":
        bot.sendMessage(chat_id, "❌ Duplicate execution blocked.")
        return
    try:
        res = requests.get(url, headers=headers)
        data = res.json()
        if data.get("status") and data["data"]["status"] == "success":
            full_meta = record[1]
            exam_type, quantity = full_meta.split(":", 1) if ":" in full_meta else (full_meta, 1)
            quantity = int(quantity)
            net_amount = data["data"]["amount"] / 100
            if extract_and_send_vouchers(chat_id, exam_type, quantity):
                with db_lock:
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE transactions SET status = 'Completed' WHERE transaction_ref = ?", (transaction_ref,))
                        cursor.execute("INSERT INTO sales (user_id, exam_type, quantity, amount) VALUES (?, ?, ?, ?)", (chat_id, exam_type, quantity, net_amount))
                        conn.commit()
                bot.sendMessage(chat_id, "✅ **Ledger finalized cleanly.**")
        else:
            bot.sendMessage(chat_id, f"❌ Unsettled funds or validation failure: {data.get('message')}")
    except Exception as e:
        bot.sendMessage(chat_id, f"❌ Fault scanned: {e}")

# Admin Matrix Modules
def show_sales_today(chat_id):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT exam_type, SUM(quantity), SUM(amount) FROM sales WHERE date(timestamp) = date('now') GROUP BY exam_type")
            records = cursor.fetchall()
    if not records:
        bot.sendMessage(chat_id, "📊 No ledger transactions posted today.")
        return
    report = "📊 **Daily Revenue Metrics Summary Matrix:**\n\n"
    grand_total = 0.0
    for exam, qty, cash in records:
        report += f"• **{exam}:** Volume: `{qty}` | Net Generated: `GH₵ {cash:.2f}`\n"
        grand_total += cash
    report += f"\n💰 Total aggregated daily revenue: **GH₵ {grand_total:.2f}**"
    bot.sendMessage(chat_id, report, parse_mode="Markdown")

def handle_admin_broadcast(chat_id, text_to_broadcast):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            all_users = [row[0] for row in cursor.fetchall()]
    if not all_users: return
    success, drop = 0, 0
    for uid in all_users:
        try:
            bot.sendMessage(uid, text_to_broadcast)
            success += 1
        except: drop += 1
    bot.sendMessage(chat_id, f"📢 **Broadcast Complete:**\n• Reached: `{success}` channels\n• Blocked: `{drop}` nodes.", parse_mode="Markdown")

def process_support_submission(chat_id, ticket_issue_text):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO tickets (user_id, issue, status) VALUES (?, ?, 'Open')", (chat_id, ticket_issue_text))
            conn.commit()
            cursor.execute("SELECT last_insert_rowid()")
            ticket_id = cursor.fetchone()[0]
    bot.sendMessage(chat_id, f"🎟️ **Ticket Logged:** Reference ID code is `#{ticket_id}`.")

def check_user_tickets(chat_id):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, issue, status FROM tickets WHERE user_id = ? ORDER BY id DESC LIMIT 5", (chat_id,))
            records = cursor.fetchall()
    if not records:
        bot.sendMessage(chat_id, "📋 You have no active support logs.")
        return
    out = "📋 **Your Recent Tickets:**\n\n"
    for tid, txt, status in records:
        out += f"• **Ticket #{tid}:** _{txt[:50]}_ | Status: `[{status}]`\n"
    bot.sendMessage(chat_id, out, parse_mode="Markdown")

def admin_view_pending_payments(chat_id):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, exam_type, transaction_ref, timestamp FROM transactions WHERE status = 'Pending' ORDER BY id DESC LIMIT 10")
            records = cursor.fetchall()
    if not records:
        bot.sendMessage(chat_id, "💳 Ledger clear: No logs sitting on unverified statuses.")
        return
    out = "💳 **Unverified Transaction Logs (Last 10):**\n\n"
    for uid, exam, ref, ts in records:
        out += f"• User: `{uid}` | `{exam}` | Ref: `{ref}`\n🕒 _{ts}_\n\n"
    bot.sendMessage(chat_id, out, parse_mode="Markdown")

def handle_email_registration(chat_id, raw_email_input):
    clean_email = raw_email_input.strip().lower()
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', clean_email):
        bot.sendMessage(chat_id, "❌ **Payload schema rejected:** Invalid email format. Try setting input configuration again:")
        return
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users_details (user_id, email) VALUES (?, ?)", (chat_id, clean_email))
            cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (chat_id,))
            conn.commit()
    bot.sendMessage(chat_id, f"✅ **Data node linkage synchronized.** Linked email is: `{clean_email}`", parse_mode="Markdown")
    show_initial_menu(chat_id, chat_id)


# --- Core Webhook Stream Parsers ---

def process_message_update(msg):
    if 'text' not in msg: return
    text = msg['text'].strip()
    user_id = msg['from']['id']
    chat_id = msg['chat']['id']

    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            cursor.execute("SELECT exam_type, current_action FROM user_sessions WHERE user_id = ?", (user_id,))
            session = cursor.fetchone()
            conn.commit()

    if session:
        exam_type, action = session[0], session[1]
        if action == "adding_codes":
            save_checker_codes(user_id, text.split("\n"))
            return
        elif action == "setting_email":
            handle_email_registration(user_id, text)
            return
        elif action == "broadcasting" and user_id in ADMINS:
            handle_admin_broadcast(user_id, text)
            with db_lock:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
                    conn.commit()
            return
        elif action == "filing_ticket":
            process_support_submission(user_id, text)
            with db_lock:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
                    conn.commit()
            return

    if text.startswith("/start"):
        if " " in text:
            param = text.split(" ", 1)[1]
            if param.startswith("ref_"): handle_referral(param.split("_")[1], user_id)
        greetings(chat_id)
        show_initial_menu(chat_id, user_id)
    elif text.startswith("/verify_payment"):
        parts = text.split(" ")
        if len(parts) >= 2: verify_payment(chat_id, parts[1].strip())
        else: bot.sendMessage(chat_id, "❌ Usage format: `/verify_payment <REFERENCE_ID>`", parse_mode="Markdown")
    elif text == "🛒 Buy Checker": purchase_flow(chat_id)
    elif text == "📧 Set Email":
        bot.sendMessage(chat_id, "📧 **Channel Configurator:** Input your email address:")
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO user_sessions (user_id, current_action) VALUES (?, 'setting_email')", (user_id,))
                conn.commit()
    elif text == "🔗 Invite Friends": generate_referral_link(chat_id)
    elif text == "🏆 Leaderboard": show_leaderboard(chat_id)
    elif text == "📋 Tasks": show_tasks_menu(chat_id)
    elif text == "📬 Support":
        bot.sendMessage(chat_id, "📬 **Issue Entry Manifest:** Explain your issue in a single text block message:")
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO user_sessions (user_id, current_action) VALUES (?, 'filing_ticket')", (user_id,))
                conn.commit()
    elif text == "📋 Check Ticket": check_user_tickets(chat_id)
    elif user_id in ADMINS:
        if text == "📊 Sales Today": show_sales_today(chat_id)
        elif text == "📢 Broadcast":
            bot.sendMessage(chat_id, "📢 **Global Transmission:** Type your broadcast message below:")
            with db_lock:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR REPLACE INTO user_sessions (user_id, current_action) VALUES (?, 'broadcasting')", (user_id,))
                    conn.commit()
        elif text == "💳 Pending Payments": admin_view_pending_payments(chat_id)
        elif text == "📄 View Checker Codes": view_checker_codes(chat_id)
        elif text in ["➕ Add BECE Codes", "➕ Add WASSCE Codes", "➕ Add NOVDEC Codes"]:
            add_checker_codes(chat_id, text.split(" ")[2])

def process_callback_update(msg):
    from_id = msg['from']['id']
    message_id = msg['message']['message_id']
    query_data = msg['data']

    if query_data.startswith("exam_"):
        target_exam = query_data.split("_")[1]
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO user_sessions (user_id, exam_type, current_action) VALUES (?, ?, 'selecting_qty')", (from_id, target_exam))
                conn.commit()
        bot.editMessageText((from_id, message_id), f"Selected Context: **{target_exam}**", parse_mode="Markdown")
        show_quantity_buttons(from_id)

    elif query_data.startswith("quantity_"):
        quantity = int(query_data.split("_")[1])
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT exam_type FROM user_sessions WHERE user_id = ?", (from_id,))
                sess = cursor.fetchone()
                cursor.execute("SELECT email FROM users_details WHERE user_id = ?", (from_id,))
                email_row = cursor.fetchone()
        if not sess or not sess[0]:
            bot.sendMessage(from_id, "❌ Flow processing error. Restart from the menu.")
            return
        if email_row and email_row[0]:
            summary(from_id, email_row[0], quantity, sess[0])
        else:
            bot.sendMessage(from_id, "❌ **Profile Error:** Please use 📧 **Set Email** first before transacting.")

    elif query_data.startswith("confirm_payment_"):
        chunks = query_data.split("_")
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT email FROM users_details WHERE user_id = ?", (from_id,))
                email_row = cursor.fetchone()
        if email_row and email_row[0]:
            generate_payment_link(from_id, email_row[0], int(chunks[2]), chunks[3])
        else:
            bot.sendMessage(from_id, "❌ Authorization failed: Profile missing email.")

    elif query_data.startswith("view_"):
        display_checker_codes(from_id, query_data.split("_")[1])
    elif query_data.startswith("verify_task_"):
        verify_task(from_id, query_data.replace("verify_task_", ""))
    elif query_data == "cancel_payment":
        bot.editMessageText((from_id, message_id), "❌ Order tracking reference cancelled.")
    elif query_data == "back_to_menu":
        bot.editMessageText((from_id, message_id), "Console pipeline closed.")
        show_initial_menu(from_id, from_id)


# --- HTTP Route Rules Matrix ---

@app.route('/webhook', methods=['POST'])
def telegram_webhook_endpoint():
    """Listens exclusively to JSON push blocks delivered via Telegram backend."""
    update = request.get_json()
    if not update:
        return jsonify({"status": "error", "message": "Empty data block rejected"}), 400

    # Divert text payload frames versus keyboard actions safely using worker routing
    if "message" in update:
        process_message_update(update["message"])
    elif "callback_query" in update:
        process_callback_update(update["callback_query"])

    return jsonify({"status": "success"}), 200

@app.route('/', methods=['GET'])
def index_heartbeat():
    """Simple connection health-check verification module."""
    return "MOVACONSULT Operational Engine Status: Clear", 200


# --- Application Bootstrap Lifecycles ---

if __name__ == '__main__':
    # Initialize the webhook tunnel directly into Telegram runtime variables on launch
    if WEBHOOK_URL and "your-production-domain" not in WEBHOOK_URL:
        try:
            bot.deleteWebhook()
            bot.setWebhook(WEBHOOK_URL)
            print(f"🚀 Live Engine Registered to Webhook Node: {WEBHOOK_URL}")
        except Exception as e:
            print(f"⚠️ Could not sync live webhook configuration routing states: {e}")
    else:
        print("⚠️ Running without active automated live Telegram Webhook binding.")

    # Launching local WSGI server context (Production setups should bind this script file to Gunicorn)
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=False)
