"""
UMarket Telegram Bot — Vercel Serverless Entry Point
=====================================================
Architecture: Webhook (Telegram POSTs to this function on each update)
Database:     Neon PostgreSQL (free tier) via DATABASE_URL
Payment:      Paystack
"""

import os
import sys
import json
import requests
from datetime import datetime
from flask import Flask, request, jsonify

# ── Allow sibling imports when running locally ─────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from db import get_connection, init_db

# ── App & Config ───────────────────────────────────────────────────────────
app = Flask(__name__)

BOT_TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PAYSTACK_SECRET    = os.environ.get("PAYSTACK_SECRET_KEY", "")
WEBHOOK_SECRET     = os.environ.get("WEBHOOK_SECRET", "")
BOT_USERNAME       = os.environ.get("BOT_USERNAME", "umarket_bot")

# Comma-separated admin IDs, e.g. "6830221233,9876543210"
_admin_env = os.environ.get("ADMIN_IDS", "6830221233")
ADMINS = [int(x.strip()) for x in _admin_env.split(",") if x.strip().isdigit()]

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

EXAM_PRICES = {
    "WASSCE": 26.5,
    "BECE":   26.5,
    "NOVDEC": 26.5,
}

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def db_exec(query, params=(), fetch="none"):
    """Execute a query and optionally return results."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        print(f"[DB ERROR] {exc} | Query: {query[:80]}")
        raise
    finally:
        cur.close()
        conn.close()


def ensure_user(user_id: int, username: str = None):
    db_exec(
        "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, username),
    )


def get_session(user_id: int) -> dict:
    row = db_exec("SELECT * FROM user_sessions WHERE user_id = %s", (user_id,), fetch="one")
    return dict(row) if row else {}


def set_state(user_id: int, state: str, state_data: str = None):
    db_exec(
        """
        INSERT INTO user_sessions (user_id, state, state_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, state_data = EXCLUDED.state_data
        """,
        (user_id, state, state_data),
    )


def clear_state(user_id: int):
    db_exec(
        "UPDATE user_sessions SET state = NULL, state_data = NULL WHERE user_id = %s",
        (user_id,),
    )


def set_session_exam(user_id: int, exam_type: str):
    db_exec(
        """
        INSERT INTO user_sessions (user_id, exam_type)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET exam_type = EXCLUDED.exam_type
        """,
        (user_id, exam_type),
    )


def get_user_email(user_id: int):
    row = db_exec("SELECT email FROM user_details WHERE user_id = %s", (user_id,), fetch="one")
    return row["email"] if row and row["email"] else None


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _tg(method: str, payload: dict) -> dict:
    try:
        r = requests.post(f"{BASE_URL}/{method}", json=payload, timeout=10)
        return r.json()
    except Exception as exc:
        print(f"[TG ERROR] {method}: {exc}")
        return {}


def send_message(chat_id, text: str, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _tg("sendMessage", payload)


def edit_message(chat_id, message_id, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _tg("editMessageText", payload)


def answer_cb(callback_query_id, text=""):
    _tg("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def reply_kb(rows: list, resize=True) -> dict:
    return {"keyboard": rows, "resize_keyboard": resize}


def inline_kb(rows: list) -> dict:
    return {"inline_keyboard": rows}


def ibtn(text: str, callback_data: str = None, url: str = None) -> dict:
    b = {"text": text}
    if callback_data:
        b["callback_data"] = callback_data
    if url:
        b["url"] = url
    return b


def main_keyboard(user_id: int) -> dict:
    base = [
        [{"text": "🛒 Buy Checker"}],
        [{"text": "📬 Support"}, {"text": "📋 Check Ticket"}],
        [{"text": "🔗 Invite Friends"}, {"text": "🏆 Leaderboard"}],
        [{"text": "📋 Tasks"}, {"text": "💳 Pending Payments"}],
        [{"text": "📧 Set Email"}],
    ]
    if user_id in ADMINS:
        base += [
            [{"text": "📊 Sales Today"}, {"text": "📢 Broadcast"}],
            [{"text": "➕ Add BECE Codes"}, {"text": "➕ Add WASSCE Codes"}, {"text": "➕ Add NOVDEC Codes"}],
            [{"text": "📄 View Checker Codes"}],
        ]
    return reply_kb(base)


def show_main_menu(chat_id, user_id):
    send_message(chat_id, "🏠 *Main Menu* — Choose an option below:", reply_markup=main_keyboard(user_id))


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND / BUTTON HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def handle_start(chat_id, user_id, username, args=""):
    ensure_user(user_id, username)

    # ── Referral tracking ──────────────────────────────────────────────────
    if args.startswith("ref_"):
        try:
            referrer_id = int(args.split("_")[1])
            if referrer_id != user_id:
                existing = db_exec(
                    "SELECT id FROM referrals WHERE referred_id = %s", (user_id,), fetch="one"
                )
                if not existing:
                    db_exec(
                        "INSERT INTO referrals (referrer_id, referred_id) VALUES (%s, %s)",
                        (referrer_id, user_id),
                    )
                    db_exec(
                        """
                        INSERT INTO points (user_id, points) VALUES (%s, 1)
                        ON CONFLICT (user_id) DO UPDATE SET points = points.points + 1
                        """,
                        (referrer_id,),
                    )
                    send_message(referrer_id, "🎉 Someone joined using your referral link! You earned *1 point*.")
        except Exception as exc:
            print(f"[Referral error] {exc}")

    display = f"@{username}" if username else f"User {user_id}"
    send_message(
        chat_id,
        (
            f"👋 Welcome to *UMarket Bot*, {display}!\n\n"
            f"🛒 Your trusted marketplace on Telegram.\n\n"
            f"Use the menu below to get started. 👇"
        ),
        reply_markup=main_keyboard(user_id),
    )


# ── Email ──────────────────────────────────────────────────────────────────

def handle_set_email(chat_id, user_id):
    set_state(user_id, "awaiting_email")
    send_message(chat_id, "📧 Please enter your *email address*:")


# ── Support ────────────────────────────────────────────────────────────────

def handle_support(chat_id, user_id):
    set_state(user_id, "awaiting_support")
    send_message(chat_id, "📬 Please describe your issue and we'll get back to you as soon as possible:")


def handle_check_ticket(chat_id, user_id):
    tickets = db_exec(
        "SELECT id, issue, status, created_at FROM support_tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 5",
        (user_id,),
        fetch="all",
    )
    if not tickets:
        send_message(chat_id, "📋 You have no support tickets yet.")
        return
    lines = ["📋 *Your Recent Tickets:*\n"]
    for t in tickets:
        emoji = "✅" if t["status"] == "Closed" else "🟡"
        preview = (t["issue"] or "")[:60] + ("…" if len(t["issue"] or "") > 60 else "")
        lines.append(f"{emoji} *Ticket #{t['id']}* — _{t['status']}_\n`{preview}`\n")
    send_message(chat_id, "\n".join(lines))


# ── Referrals & Leaderboard ────────────────────────────────────────────────

def handle_invite(chat_id, user_id):
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    pts_row = db_exec("SELECT points FROM points WHERE user_id = %s", (user_id,), fetch="one")
    pts = pts_row["points"] if pts_row else 0
    send_message(
        chat_id,
        (
            f"🔗 *Your Referral Link:*\n`{link}`\n\n"
            f"Share this link — you earn *1 point* for every new user who joins.\n\n"
            f"🏅 Your current points: *{pts}*"
        ),
    )


def handle_leaderboard(chat_id):
    rows = db_exec(
        """
        SELECT u.user_id, u.username, COALESCE(p.points, 0) AS pts
        FROM users u LEFT JOIN points p ON u.user_id = p.user_id
        ORDER BY pts DESC LIMIT 10
        """,
        fetch="all",
    )
    if not rows:
        send_message(chat_id, "🏆 The leaderboard is empty. Be the first!")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *Leaderboard — Top Users*\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i + 1}."
        name = f"@{row['username']}" if row["username"] else f"User {row['user_id']}"
        lines.append(f"{medal} {name} — *{row['pts']} pts*")
    send_message(chat_id, "\n".join(lines))


# ── Tasks ──────────────────────────────────────────────────────────────────

def handle_tasks(chat_id):
    # Customise the channel/task below with your real UMarket channel
    keyboard = inline_kb([
        [ibtn("📢 Follow UMarket Channel", url=f"https://t.me/{BOT_USERNAME}")],
        [ibtn("✅ Verify Task", callback_data="verify_task_umarket")],
    ])
    send_message(chat_id, "📋 *Tasks*\nComplete the task below to earn *5 points*!", reply_markup=keyboard)


# ── Admin: Pending payments ────────────────────────────────────────────────

def handle_pending_payments(chat_id, user_id):
    if user_id not in ADMINS:
        send_message(chat_id, "❌ Unauthorized.")
        return
    rows = db_exec(
        """
        SELECT user_id, exam_type, quantity, amount, transaction_ref, created_at
        FROM transactions WHERE status = 'Pending'
        ORDER BY created_at DESC LIMIT 20
        """,
        fetch="all",
    )
    if not rows:
        send_message(chat_id, "💳 No pending payments at the moment. ✅")
        return
    lines = ["💳 *Pending Payments:*\n"]
    for r in rows:
        lines.append(
            f"👤 User `{r['user_id']}` | {r['exam_type']} ×{r['quantity']} | GH₵{r['amount']:.2f}\n"
            f"Ref: `{r['transaction_ref']}`\n"
        )
    send_message(chat_id, "\n".join(lines))


# ── Admin: Sales today ─────────────────────────────────────────────────────

def handle_sales_today(chat_id, user_id):
    if user_id not in ADMINS:
        send_message(chat_id, "❌ Unauthorized.")
        return
    rows = db_exec(
        """
        SELECT exam_type, SUM(quantity) AS qty, SUM(amount) AS total
        FROM sales WHERE DATE(sold_at) = CURRENT_DATE
        GROUP BY exam_type
        """,
        fetch="all",
    )
    if not rows:
        send_message(chat_id, "📊 No sales recorded today yet.")
        return
    lines = [f"📊 *Sales Report — {datetime.utcnow().strftime('%d %b %Y')}*\n"]
    grand = 0.0
    for r in rows:
        lines.append(f"📚 {r['exam_type']}: {r['qty']} units — GH₵{r['total']:.2f}")
        grand += float(r["total"])
    lines.append(f"\n💰 *Grand Total: GH₵{grand:.2f}*")
    send_message(chat_id, "\n".join(lines))


# ── Admin: Broadcast ───────────────────────────────────────────────────────

def handle_broadcast_start(chat_id, user_id):
    if user_id not in ADMINS:
        send_message(chat_id, "❌ Unauthorized.")
        return
    set_state(user_id, "awaiting_broadcast")
    send_message(chat_id, "📢 Send the message you want to broadcast to *all users*:")


# ── Admin: Add checker codes ───────────────────────────────────────────────

def handle_add_codes_start(chat_id, user_id, exam_type: str):
    if user_id not in ADMINS:
        send_message(chat_id, "❌ Unauthorized.")
        return
    set_state(user_id, f"awaiting_codes_{exam_type}")
    send_message(
        chat_id,
        (
            f"➕ Send *{exam_type}* checker codes.\n\n"
            f"Format — one code per line:\n`PIN SERIALNUMBER`\n\n"
            f"Example:\n`ABC123 SN-00001`\n`XYZ456 SN-00002`\n\n"
            f"Paste all codes in a *single message*."
        ),
    )


# ── Admin: View available codes ────────────────────────────────────────────

def handle_view_codes(chat_id, user_id):
    if user_id not in ADMINS:
        send_message(chat_id, "❌ Unauthorized.")
        return
    keyboard = inline_kb([
        [ibtn("BECE",   callback_data="viewcodes_BECE")],
        [ibtn("WASSCE", callback_data="viewcodes_WASSCE")],
        [ibtn("NOVDEC", callback_data="viewcodes_NOVDEC")],
        [ibtn("⬅️ Back", callback_data="back_to_menu")],
    ])
    send_message(chat_id, "📄 Select exam type to view available codes:", reply_markup=keyboard)


# ── Purchase flow ──────────────────────────────────────────────────────────

def purchase_flow(chat_id):
    keyboard = inline_kb([
        [ibtn("BECE",   callback_data="exam_BECE")],
        [ibtn("WASSCE", callback_data="exam_WASSCE")],
        [ibtn("NOVDEC", callback_data="exam_NOVDEC")],
        [ibtn("⬅️ Back", callback_data="back_to_menu")],
    ])
    send_message(chat_id, "🛒 *Select exam type:*", reply_markup=keyboard)


def show_quantity_buttons(chat_id):
    keyboard = inline_kb([
        [ibtn(str(i), callback_data=f"quantity_{i}") for i in range(1, 6)],
        [ibtn(str(i), callback_data=f"quantity_{i}") for i in range(6, 11)],
        [ibtn("⬅️ Back", callback_data="back_to_menu")],
    ])
    send_message(chat_id, "🔢 *How many checkers do you want?*", reply_markup=keyboard)


def show_payment_summary(chat_id, user_id, exam_type: str, quantity: int, email: str):
    price = EXAM_PRICES.get(exam_type, 0)
    total = price * quantity
    keyboard = inline_kb([
        [ibtn("✅ Confirm & Pay", callback_data=f"confirm_payment_{exam_type}_{quantity}")],
        [ibtn("❌ Cancel",        callback_data="cancel_payment")],
    ])
    send_message(
        chat_id,
        (
            f"🔔 *Transaction Summary*\n\n"
            f"📄 Checker Type: *{exam_type}*\n"
            f"📘 Quantity: *{quantity}*\n"
            f"💵 Price per Checker: *GH₵{price:.2f}*\n"
            f"🏷️ Total: *GH₵{total:.2f}*\n"
            f"📧 Email: `{email}`\n\n"
            f"_Codes will be delivered here in the bot._\n\n"
            f"Tap below to pay securely via Paystack 🔐"
        ),
        reply_markup=keyboard,
    )


def generate_payment_link(chat_id, user_id, exam_type: str, quantity: int):
    # ── Validate email ─────────────────────────────────────────────────────
    email = get_user_email(user_id)
    if not email:
        send_message(chat_id, "❌ Please set your email first using 📧 *Set Email*.")
        return

    # ── Check stock ────────────────────────────────────────────────────────
    stock = db_exec(
        "SELECT COUNT(*) AS cnt FROM checker_codes WHERE exam_type = %s AND is_used = FALSE",
        (exam_type,),
        fetch="one",
    )
    if not stock or stock["cnt"] < quantity:
        send_message(chat_id, f"❌ Not enough *{exam_type}* codes in stock. Please try later or contact support.")
        return

    total = EXAM_PRICES[exam_type] * quantity
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET}", "Content-Type": "application/json"}
    payload = {
        "email": email,
        "amount": int(total * 100),  # Paystack uses smallest currency unit
        "currency": "GHS",
        "metadata": {
            "custom_fields": [
                {"display_name": "Exam Type", "variable_name": "exam_type", "value": exam_type},
                {"display_name": "Quantity",  "variable_name": "quantity",  "value": str(quantity)},
                {"display_name": "User ID",   "variable_name": "user_id",   "value": str(user_id)},
            ]
        },
    }
    try:
        resp = requests.post(
            "https://api.paystack.co/transaction/initialize",
            json=payload,
            headers=headers,
            timeout=15,
        )
        data = resp.json()
        if data.get("status"):
            pay_url = data["data"]["authorization_url"]
            ref     = data["data"]["reference"]

            db_exec(
                """
                INSERT INTO transactions (user_id, exam_type, quantity, transaction_ref, status, amount)
                VALUES (%s, %s, %s, %s, 'Pending', %s)
                """,
                (user_id, exam_type, quantity, ref, total),
            )

            keyboard = inline_kb([[ibtn("💳 Open Payment Page", url=pay_url)]])
            send_message(
                chat_id,
                (
                    f"✅ *Payment link ready!*\n\n"
                    f"💾 *Save your reference:*\n`{ref}`\n\n"
                    f"After paying, verify with:\n`/verify_payment {ref}`"
                ),
                reply_markup=keyboard,
            )
        else:
            send_message(chat_id, f"❌ Could not generate payment link: {data.get('message', 'Unknown error')}")
    except Exception as exc:
        print(f"[Paystack error] {exc}")
        send_message(chat_id, "❌ Error generating payment link. Please try again later.")


def verify_payment(chat_id, user_id, ref: str):
    existing = db_exec(
        "SELECT status, exam_type, quantity FROM transactions WHERE transaction_ref = %s AND user_id = %s",
        (ref, user_id),
        fetch="one",
    )
    if not existing:
        send_message(chat_id, "❌ Transaction not found. Make sure you entered the correct reference.")
        return
    if existing["status"] == "Completed":
        send_message(chat_id, "❌ This transaction has already been processed.")
        return

    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET}"}
    try:
        resp = requests.get(
            f"https://api.paystack.co/transaction/verify/{ref}",
            headers=headers,
            timeout=15,
        )
        data = resp.json()
        if data.get("status") and data["data"]["status"] == "success":
            exam_type = existing["exam_type"]
            quantity  = existing["quantity"]
            amount    = data["data"]["amount"] / 100

            # ── Claim codes ────────────────────────────────────────────────
            codes = db_exec(
                """
                SELECT id, pin, serial_number FROM checker_codes
                WHERE exam_type = %s AND is_used = FALSE
                LIMIT %s
                """,
                (exam_type, quantity),
                fetch="all",
            )
            if not codes or len(codes) < quantity:
                send_message(chat_id, "✅ Payment confirmed! Codes are being prepared — please contact support.")
                return

            code_ids = [c["id"] for c in codes]
            # Use ANY with a list for PostgreSQL
            db_exec("UPDATE checker_codes SET is_used = TRUE WHERE id = ANY(%s::int[])", (code_ids,))
            db_exec("UPDATE transactions SET status = 'Completed' WHERE transaction_ref = %s", (ref,))
            db_exec(
                "INSERT INTO sales (user_id, exam_type, quantity, amount) VALUES (%s, %s, %s, %s)",
                (user_id, exam_type, quantity, amount),
            )

            send_message(chat_id, f"✅ *Payment confirmed!* Here are your *{exam_type}* checker codes:")
            for i, code in enumerate(codes, 1):
                send_message(
                    chat_id,
                    f"🔑 *Code #{i}:*\nSerial Number: `{code['serial_number']}`\nPin: `{code['pin']}`",
                )
            send_message(chat_id, "🌐 Visit *ghana.waecdirect.org* to check your results.")
        else:
            send_message(chat_id, "❌ Payment not confirmed yet. Complete the payment and try again.")
    except Exception as exc:
        print(f"[Verify payment error] {exc}")
        send_message(chat_id, "❌ Error verifying payment. Please try again later.")


# ══════════════════════════════════════════════════════════════════════════════
# STATE MACHINE — handles multi-step conversations
# ══════════════════════════════════════════════════════════════════════════════

def handle_state_input(chat_id, user_id, text: str, session: dict):
    state = session.get("state", "")

    # ── Set email ──────────────────────────────────────────────────────────
    if state == "awaiting_email":
        email = text.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            send_message(chat_id, "❌ That doesn't look like a valid email. Please try again (e.g. `you@gmail.com`):")
            return
        db_exec(
            "INSERT INTO user_details (user_id, email) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email",
            (user_id, email),
        )
        db_exec(
            "INSERT INTO user_sessions (user_id, email) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email",
            (user_id, email),
        )
        clear_state(user_id)
        send_message(chat_id, f"✅ Email saved: `{email}`")
        show_main_menu(chat_id, user_id)

    # ── Support ticket ─────────────────────────────────────────────────────
    elif state == "awaiting_support":
        db_exec(
            "INSERT INTO support_tickets (user_id, issue, status) VALUES (%s, %s, 'Open')",
            (user_id, text),
        )
        ticket = db_exec(
            "SELECT id FROM support_tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
            fetch="one",
        )
        ticket_id = ticket["id"] if ticket else "?"
        clear_state(user_id)
        send_message(chat_id, f"✅ *Ticket #{ticket_id}* submitted! We'll get back to you shortly.")
        # Notify all admins
        for admin_id in ADMINS:
            send_message(admin_id, f"🎫 *New Support Ticket #{ticket_id}*\nFrom: `{user_id}`\n\n{text}")

    # ── Broadcast ──────────────────────────────────────────────────────────
    elif state == "awaiting_broadcast":
        if user_id not in ADMINS:
            clear_state(user_id)
            return
        users = db_exec("SELECT user_id FROM users", fetch="all") or []
        sent, failed = 0, 0
        for u in users:
            try:
                send_message(u["user_id"], f"📢 *Announcement from UMarket*\n\n{text}")
                sent += 1
            except Exception:
                failed += 1
        clear_state(user_id)
        send_message(chat_id, f"✅ Broadcast sent to *{sent}* users. ({failed} failed)")

    # ── Add checker codes ──────────────────────────────────────────────────
    elif state and state.startswith("awaiting_codes_"):
        if user_id not in ADMINS:
            clear_state(user_id)
            return
        exam_type = state.replace("awaiting_codes_", "")
        lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
        added, skipped = 0, 0
        for line in lines:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                pin, serial = parts[0].strip(), parts[1].strip()
                try:
                    db_exec(
                        "INSERT INTO checker_codes (exam_type, pin, serial_number) VALUES (%s, %s, %s)",
                        (exam_type, pin, serial),
                    )
                    added += 1
                except Exception:
                    skipped += 1
            else:
                skipped += 1
        clear_state(user_id)
        msg = f"✅ Added *{added}* {exam_type} code(s)."
        if skipped:
            msg += f"\n⚠️ *{skipped}* line(s) skipped (wrong format — must be `PIN SERIAL`)."
        send_message(chat_id, msg)

    else:
        # Unknown state — reset
        clear_state(user_id)
        show_main_menu(chat_id, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def handle_callback(update: dict):
    cb         = update["callback_query"]
    query_id   = cb["id"]
    user_id    = cb["from"]["id"]
    chat_id    = cb["from"]["id"]
    username   = cb["from"].get("username", "")
    data       = cb["data"]
    message_id = cb["message"]["message_id"]

    answer_cb(query_id)
    ensure_user(user_id, username)

    # ── Exam selection ─────────────────────────────────────────────────────
    if data.startswith("exam_"):
        exam_type = data.split("_")[1]
        set_session_exam(user_id, exam_type)
        edit_message(chat_id, message_id, f"✅ You selected *{exam_type}*.")
        show_quantity_buttons(chat_id)

    # ── Quantity selection ─────────────────────────────────────────────────
    elif data.startswith("quantity_"):
        quantity = int(data.split("_")[1])
        session  = get_session(user_id)
        exam_type = session.get("exam_type")
        if not exam_type:
            send_message(chat_id, "❌ Session expired. Please start again: 🛒 Buy Checker")
            return
        email = get_user_email(user_id)
        if not email:
            send_message(chat_id, "❌ Please set your email first via 📧 *Set Email*.")
            return
        # Store quantity in state_data temporarily
        db_exec(
            "UPDATE user_sessions SET state_data = %s WHERE user_id = %s",
            (str(quantity), user_id),
        )
        show_payment_summary(chat_id, user_id, exam_type, quantity, email)

    # ── Confirm payment ────────────────────────────────────────────────────
    elif data.startswith("confirm_payment_"):
        parts = data.split("_")
        # confirm_payment_EXAMTYPE_QUANTITY
        exam_type = parts[2]
        quantity  = int(parts[3])
        generate_payment_link(chat_id, user_id, exam_type, quantity)

    # ── View codes (admin) ─────────────────────────────────────────────────
    elif data.startswith("viewcodes_"):
        exam_type = data.split("_")[1]
        row = db_exec(
            "SELECT COUNT(*) AS cnt FROM checker_codes WHERE exam_type = %s AND is_used = FALSE",
            (exam_type,),
            fetch="one",
        )
        cnt = row["cnt"] if row else 0
        send_message(chat_id, f"📄 *{exam_type}* — *{cnt}* code(s) available.")

    # ── Task verification ──────────────────────────────────────────────────
    elif data.startswith("verify_task_"):
        channel = data.replace("verify_task_", "")
        try:
            r = requests.get(
                f"{BASE_URL}/getChatMember",
                params={"chat_id": f"@{channel}", "user_id": user_id},
                timeout=10,
            ).json()
            status = (r.get("result") or {}).get("status", "")
            if status in ("member", "administrator", "creator"):
                db_exec(
                    """
                    INSERT INTO points (user_id, points) VALUES (%s, 5)
                    ON CONFLICT (user_id) DO UPDATE SET points = points.points + 5
                    """,
                    (user_id,),
                )
                send_message(chat_id, "✅ Task verified! You earned *5 points*. 🎉")
            else:
                send_message(chat_id, "❌ You haven't joined the channel yet. Please join and try again.")
        except Exception as exc:
            print(f"[Task verify error] {exc}")
            send_message(chat_id, "❌ Could not verify membership. Try again later.")

    # ── Cancel payment ─────────────────────────────────────────────────────
    elif data == "cancel_payment":
        edit_message(chat_id, message_id, "❌ Payment cancelled. Use 🛒 *Buy Checker* to start again.")

    # ── Back to menu ───────────────────────────────────────────────────────
    elif data == "back_to_menu":
        edit_message(chat_id, message_id, "🏠 Returning to main menu…")
        show_main_menu(chat_id, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE ROUTER
# ══════════════════════════════════════════════════════════════════════════════

# Maps button text → handler function
MESSAGE_ROUTES = {
    "🛒 Buy Checker":       lambda cid, uid, _: purchase_flow(cid),
    "📧 Set Email":         lambda cid, uid, _: handle_set_email(cid, uid),
    "📬 Support":           lambda cid, uid, _: handle_support(cid, uid),
    "📋 Check Ticket":      lambda cid, uid, _: handle_check_ticket(cid, uid),
    "🔗 Invite Friends":    lambda cid, uid, _: handle_invite(cid, uid),
    "🏆 Leaderboard":       lambda cid, uid, _: handle_leaderboard(cid),
    "📋 Tasks":             lambda cid, uid, _: handle_tasks(cid),
    "💳 Pending Payments":  lambda cid, uid, _: handle_pending_payments(cid, uid),
    "📊 Sales Today":       lambda cid, uid, _: handle_sales_today(cid, uid),
    "📢 Broadcast":         lambda cid, uid, _: handle_broadcast_start(cid, uid),
    "➕ Add BECE Codes":    lambda cid, uid, _: handle_add_codes_start(cid, uid, "BECE"),
    "➕ Add WASSCE Codes":  lambda cid, uid, _: handle_add_codes_start(cid, uid, "WASSCE"),
    "➕ Add NOVDEC Codes":  lambda cid, uid, _: handle_add_codes_start(cid, uid, "NOVDEC"),
    "📄 View Checker Codes": lambda cid, uid, _: handle_view_codes(cid, uid),
}


def handle_message(update: dict):
    msg      = update.get("message", {})
    chat_id  = msg["chat"]["id"]
    user_id  = msg["from"]["id"]
    username = msg["from"].get("username", "")
    text     = msg.get("text", "")

    ensure_user(user_id, username)
    session = get_session(user_id)
    state   = session.get("state") or ""

    # ── Active state → delegate to state machine ───────────────────────────
    if state:
        handle_state_input(chat_id, user_id, text, session)
        return

    # ── Commands ───────────────────────────────────────────────────────────
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        args  = parts[1] if len(parts) > 1 else ""
        handle_start(chat_id, user_id, username or f"User{user_id}", args)
        return

    if text.startswith("/verify_payment"):
        parts = text.split(" ")
        if len(parts) > 1:
            verify_payment(chat_id, user_id, parts[1].strip())
        else:
            send_message(chat_id, "❌ Usage: `/verify_payment YOUR_REFERENCE`")
        return

    if text.startswith("/help"):
        send_message(
            chat_id,
            (
                "📖 *Help Guide*\n\n"
                "🛒 *Buy Checker* — Purchase BECE/WASSCE/NOVDEC checker codes\n"
                "📧 *Set Email* — Save your email for payment receipts\n"
                "🔗 *Invite Friends* — Get your referral link\n"
                "🏆 *Leaderboard* — See top referrers\n"
                "📬 *Support* — Open a support ticket\n"
                "📋 *Check Ticket* — View your ticket status\n\n"
                "After paying, verify with:\n`/verify_payment REFERENCE`"
            ),
        )
        return

    # ── Menu buttons ───────────────────────────────────────────────────────
    if text in MESSAGE_ROUTES:
        MESSAGE_ROUTES[text](chat_id, user_id, text)
        return

    # ── Fallback ───────────────────────────────────────────────────────────
    send_message(
        chat_id,
        "🤔 I didn't understand that. Please use the menu below.",
        reply_markup=main_keyboard(user_id),
    )


# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "🚀 UMarket Bot is running!"})


@app.route("/", methods=["POST"])
def webhook():
    # ── Optional webhook secret verification ───────────────────────────────
    if WEBHOOK_SECRET:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 403

    update = request.get_json(silent=True)
    if not update:
        return jsonify({"ok": True})

    try:
        if "callback_query" in update:
            handle_callback(update)
        elif "message" in update:
            handle_message(update)
    except Exception as exc:
        print(f"[Webhook error] {exc}")

    # Telegram expects a 200 response within 10 s
    return jsonify({"ok": True})


@app.route("/setup", methods=["GET"])
def setup():
    """
    Call this endpoint ONCE after first deployment to create DB tables.
    e.g. https://your-bot.vercel.app/setup
    """
    try:
        init_db()
        return jsonify({"ok": True, "message": "✅ Database tables created successfully!"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/set-webhook", methods=["GET"])
def set_webhook():
    """
    Call this endpoint ONCE after first deployment to register the webhook with Telegram.
    e.g. https://your-bot.vercel.app/set-webhook
    """
    host = request.host_url.rstrip("/")
    webhook_url = f"{host}/"

    payload = {"url": webhook_url}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    r = requests.post(f"{BASE_URL}/setWebhook", json=payload, timeout=10)
    return jsonify(r.json())


# ── Local dev entry point ──────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
