#!/usr/bin/env python3
"""K-pop gig alerts for Telegram.

Two modes:
  python kpop_bot.py           weekly digest: new shows since last run + shows in the next N days
  python kpop_bot.py --reply   answer commands people sent the bot (/latest, /sg, /all, /help)

No third-party packages needed. Settings come from environment variables:
  TELEGRAM_BOT_TOKEN   token from @BotFather (required)
  TELEGRAM_CHAT_ID     where the weekly digest goes; run `python kpop_bot.py --find-chat-id` after messaging the bot
  ARTISTS              optional, comma-separated list to only alert on (e.g. "BTS,IU,Stray Kids")
  SG_ONLY              optional, "1" to only include Singapore shows
  DAYS_AHEAD           optional, default 30
  EVENTS_FILE          optional, default events.json next to this script
  STATE_FILE           optional, default seen.json next to this script
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ARTISTS = {a.strip().lower() for a in os.environ.get("ARTISTS", "").split(",") if a.strip()}
SG_ONLY = os.environ.get("SG_ONLY", "") == "1"
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "30"))
EVENTS_FILE = Path(os.environ.get("EVENTS_FILE", HERE / "events.json"))
STATE_FILE = Path(os.environ.get("STATE_FILE", HERE / "seen.json"))
PAGE = "https://claude.ai/artifact/1uS7qvXBYw4TtM8MAHjTH2"
STATUS = {"on_sale": "on sale", "announced": "announced", "tba": "date TBC", "unverified": "unverified"}
COMMANDS = ("/latest", "/sg", "/all", "/help", "/start")
HELP = ("Hi! I track K-pop concerts in Singapore and overseas.\n\n"
        "/latest - shows in the next 30 days\n"
        "/sg - all upcoming Singapore shows\n"
        "/all - every upcoming show\n"
        "/help - this list\n\n"
        "I check for messages every few minutes, so replies can take a little while.")


def api(method, **params):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        return json.load(r)


def key(e):
    return f"{e['artist']}|{e['date']}|{e['city']}"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt(e):
    d = date.fromisoformat(e["date"]).strftime("%a %d %b %Y")
    place = e["city"] if e["country"] == "Singapore" else f"{e['city']}, {e['country']}"
    flag = "🇸🇬 " if e["country"] == "Singapore" else ""
    line = f"{flag}<b>{esc(e['artist'])}</b> · {d}\n{esc(e['venue'])}, {esc(place)} ({STATUS.get(e['status'], e['status'])})"
    if e.get("note"):
        line += f"\n<i>{esc(e['note'])}</i>"
    if e.get("tickets"):
        line += f'\n<a href="{e["tickets"]}">Tickets</a>'
    return line


def load_events(today):
    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))["events"]
    events = [e for e in events if (e.get("endDate") or e["date"]) >= today.isoformat()]
    if ARTISTS:
        events = [e for e in events if e["artist"].lower() in ARTISTS]
    if SG_ONLY:
        events = [e for e in events if e["country"] == "Singapore"]
    return sorted(events, key=lambda e: e["date"])


def upcoming(events, today):
    cut = (today + timedelta(days=DAYS_AHEAD)).isoformat()
    return [e for e in events if e["date"] <= cut]


def send(chat_id, text):
    """Send text, split on blank lines to stay under Telegram's 4096-char cap."""
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > 4000:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    chunks.append(cur)
    for c in chunks:
        api("sendMessage", chat_id=chat_id, text=c, parse_mode="HTML", disable_web_page_preview="true")
    return len(chunks)


def find_chat_id():
    res = api("getUpdates")
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or u["message"]["chat"].get("title")
             for u in res.get("result", []) if "message" in u}
    if not chats:
        print("No messages found. Open your bot in Telegram, press Start or send it any message, then run this again.")
    for cid, name in chats.items():
        print(f"chat id {cid}  ({name})")


def answer(cmd, today):
    if cmd in ("/help", "/start"):
        return HELP
    events = load_events(today)
    if cmd == "/sg":
        pick, title = [e for e in events if e["country"] == "Singapore"], "🇸🇬 <b>Upcoming Singapore shows</b>"
    elif cmd == "/all":
        pick, title = events, "🎤 <b>All upcoming shows</b>"
    else:
        pick, title = upcoming(events, today), f"📅 <b>Coming up in the next {DAYS_AHEAD} days</b>"
    body = "\n\n".join(fmt(e) for e in pick) if pick else "Nothing listed right now."
    return f"{title}\n\n{body}\n\n<a href=\"{PAGE}\">Open the gig guide</a>"


def reply_mode():
    """Answer messages sent to the bot since the last check, then mark them handled."""
    today = date.today()
    ups = api("getUpdates", timeout=0).get("result", [])
    if not ups:
        print("No new messages.")
        return
    handled = 0
    for u in ups:
        m = u.get("message")
        if not m or "text" not in m:
            continue
        text = m["text"].strip()
        in_group = m["chat"].get("type") in ("group", "supergroup")
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if in_group:
            # In groups only answer the bot's own commands, so it doesn't reply to every chat message.
            if cmd not in COMMANDS:
                continue
        elif cmd not in COMMANDS:
            # Private chat: plain text gets the latest list, unknown commands get the help text.
            cmd = "/help" if cmd else "/latest"
        try:
            send(m["chat"]["id"], answer(cmd, today))
            handled += 1
        except Exception as err:  # one bad chat (e.g. bot removed from a group) shouldn't block the rest
            print(f"Could not reply in chat {m['chat']['id']}: {err}")
    api("getUpdates", offset=ups[-1]["update_id"] + 1, timeout=0)  # tell Telegram these are done
    print(f"Answered {handled} message(s).")


def weekly():
    global CHAT_ID
    chat_file = STATE_FILE.with_name("chat_id.txt")
    if not CHAT_ID and chat_file.exists():
        CHAT_ID = chat_file.read_text().strip()
    if not CHAT_ID:
        # No chat id set: use whoever pressed Start on the bot most recently (private chats only).
        ups = [u["message"]["chat"] for u in api("getUpdates").get("result", [])
               if "message" in u and u["message"]["chat"].get("type") == "private"]
        if not ups:
            me = api("getMe").get("result", {})
            sys.exit(f"No messages yet. Open https://t.me/{me.get('username')} in Telegram, press Start, then run again.")
        CHAT_ID = str(ups[-1]["id"])
        chat_file.write_text(CHAT_ID)
        print(f"Using chat id {CHAT_ID} (saved to chat_id.txt).")

    today = date.today()
    events = load_events(today)
    seen = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    first_run = not STATE_FILE.exists()
    new = [] if first_run else [e for e in events if key(e) not in seen]
    soon = upcoming(events, today)

    parts = []
    if first_run:
        parts.append(f"👋 K-pop gig alerts are on. You're tracking {len(events)} upcoming shows.")
    if new:
        parts.append("🆕 <b>New shows</b>\n\n" + "\n\n".join(fmt(e) for e in new))
    if soon:
        parts.append(f"📅 <b>Coming up in the next {DAYS_AHEAD} days</b>\n\n" + "\n\n".join(fmt(e) for e in soon))
    if not parts:
        parts.append("No new or upcoming shows this week.")
    parts.append(f'<a href="{PAGE}">Open the gig guide</a>')

    n = send(CHAT_ID, "\n\n".join(parts))
    STATE_FILE.write_text(json.dumps(sorted(seen | {key(e) for e in events})))
    print(f"Sent {n} message(s): {len(new)} new, {len(soon)} coming up.")


def main():
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN first.")
    if "--find-chat-id" in sys.argv:
        return find_chat_id()
    if "--reply" in sys.argv:
        return reply_mode()
    weekly()


if __name__ == "__main__":
    main()
