import os
import sqlite3
import requests
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

DB_PATH        = os.getenv("DB_PATH", "referral2.db")
TELE_TOKEN     = os.getenv("TELE_TOKEN")
TWITTER_BEARER = os.getenv("TWITTER_BEARER")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id      INTEGER PRIMARY KEY,
        telegram_id  INTEGER UNIQUE,
        twitter_id   INTEGER,
        ref_code     TEXT UNIQUE,
        referrer_id  INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS points (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pools (
        pool_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        task          TEXT,
        total_points  INTEGER,
        remaining     INTEGER,
        max_claims    INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS claims (
        claim_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER,
        pool_id   INTEGER,
        method    TEXT,
        verified  INTEGER
    )""")
    conn.commit()
    return conn

import random, string
def gen_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def award_points(user_id, pts):
    conn = init_db()
    cur = conn.execute("SELECT balance FROM points WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row:
        conn.execute("UPDATE points SET balance=balance+? WHERE user_id=?", (pts, user_id))
    else:
        conn.execute("INSERT INTO points(user_id,balance) VALUES(?,?)", (user_id, pts))
    conn.commit()

async def start(update, ctx):
    tg_id = update.effective_user.id
    conn = init_db()
    if conn.execute("SELECT 1 FROM users WHERE telegram_id=?", (tg_id,)).fetchone():
        return await update.message.reply_text("You’re already registered.")
    args = ctx.args[0] if ctx.args else None
    referrer = None
    if args:
        r = conn.execute("SELECT user_id FROM users WHERE ref_code=?", (args,)).fetchone()
        if r: referrer = r[0]
    code = gen_code()
    conn.execute(
        "INSERT INTO users(telegram_id,ref_code,referrer_id) VALUES(?,?,?)",
        (tg_id, code, referrer)
    )
    conn.commit()
    if referrer:
        award_points(referrer, 10)
    await update.message.reply_text(
        f"✅ Registered! Your code: {code}\n"
        "Share: t.me/YourBot?start=" + code + "\n"
        "Earn 10 pts per direct referral."
    )

async def linktwitter(update, ctx):
    handle = ctx.args[0].lstrip("@")
    resp = requests.get(
        f"https://api.twitter.com/2/users/by/username/{handle}",
        headers={"Authorization": f"Bearer {TWITTER_BEARER}"}
    )
    if resp.status_code != 200:
        return await update.message.reply_text("Twitter handle not found.")
    tw_id = resp.json()["data"]["id"]
    tg_id = update.effective_user.id
    conn = init_db()
    conn.execute("UPDATE users SET twitter_id=? WHERE telegram_id=?", (tw_id, tg_id))
    conn.commit()
    await update.message.reply_text(f"Linked Twitter @{handle}.")

async def do_join(update, ctx):
    group = ctx.args[0].lstrip("@")
    tg_id = update.effective_user.id
    try:
        member = await ctx.bot.get_chat_member(chat_id="@" + group, user_id=tg_id)
    except:
        return await update.message.reply_text("Bot not admin or group not found.")
    if member.status not in ("member", "administrator", "creator"):
        return await update.message.reply_text("You’re not a member.")
    conn = init_db()
    uid = conn.execute("SELECT user_id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()[0]
    award_points(uid, 5)
    await update.message.reply_text("✅ Verified join. +5 pts.")

async def do_follow(update, ctx):
    proj = ctx.args[0].lstrip("@")
    tg_id = update.effective_user.id
    conn = init_db()
    res = conn.execute("SELECT user_id,twitter_id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not res or not res[1]:
        return await update.message.reply_text("Link your Twitter first: /linktwitter <handle>")
    uid, tw_id = res
    chk = requests.get(
        f"https://api.twitter.com/2/users/{tw_id}/following/{proj}",
        headers={"Authorization": f"Bearer {TWITTER_BEARER}"}
    )
    if chk.status_code == 200:
        award_points(uid, 5)
        await update.message.reply_text("✅ Verified follow. +5 pts.")
    else:
        await update.message.reply_text("You’re not following them.")

async def balance(update, ctx):
    tg_id = update.effective_user.id
    conn = init_db()
    r = conn.execute("""
      SELECT p.balance FROM points p
      JOIN users u ON u.user_id=p.user_id
      WHERE u.telegram_id=?
    """, (tg_id,)).fetchone()
    pts = r[0] if r else 0
    await update.message.reply_text(f"🏅 You have {pts} points.")

async def newpool(update, ctx):
    if update.effective_user.id != ADMIN_ID:
        return
    task, pts, cap = ctx.args
    pts, cap = int(pts), int(cap)
    conn = init_db()
    conn.execute(
        "INSERT INTO pools(task,total_points,remaining,max_claims) VALUES(?,?,?,?)",
        (task, pts*cap, pts*cap, cap)
    )
    conn.commit()
    await update.message.reply_text(f"Pool created: {task}, {pts} pts × {cap} users.")

async def do_task(update, ctx):
    pool_id = int(ctx.args[0])
    tg_id = update.effective_user.id
    conn = init_db()
    pool = conn.execute("SELECT remaining,total_points FROM pools WHERE pool_id=?", (pool_id,)).fetchone()
    if not pool or pool[0] <= 0:
        return await update.message.reply_text("Pool closed or not found.")
    uid = conn.execute("SELECT user_id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()[0]
    if conn.execute("SELECT 1 FROM claims WHERE user_id=? AND pool_id=?", (uid, pool_id)).fetchone():
        return await update.message.reply_text("You already claimed.")
    pts_each = pool[1] // pool[0]
    award_points(uid, pts_each)
    conn.execute("UPDATE pools SET remaining=remaining-? WHERE pool_id=?", (pts_each, pool_id))
    conn.execute("INSERT INTO claims(user_id,pool_id,method,verified) VALUES(?,?,?,1)", (uid, pool_id, "TASK"))
    conn.commit()
    await update.message.reply_text(f"✅ Task done: +{pts_each} pts.")

if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TELE_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("linktwitter", linktwitter))
    app.add_handler(CommandHandler("do_join", do_join))
    app.add_handler(CommandHandler("do_follow", do_follow))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("newpool", newpool))
    app.add_handler(CommandHandler("do_task", do_task))
    app.run_polling()
