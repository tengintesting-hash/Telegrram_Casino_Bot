import os
from typing import Optional

import requests
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import execute, fetch_all, fetch_one

app = FastAPI()

templates = Jinja2Templates(directory="/app/app/templates")

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))


def ensure_user(telegram_id: int, username: Optional[str] = None):
    user = fetch_one("SELECT * FROM users WHERE telegram_id = %(telegram_id)s", {"telegram_id": telegram_id})
    if not user:
        execute(
            """
            INSERT INTO users (telegram_id, username)
            VALUES (%(telegram_id)s, %(username)s)
            """,
            {"telegram_id": telegram_id, "username": username},
        )
        user = fetch_one("SELECT * FROM users WHERE telegram_id = %(telegram_id)s", {"telegram_id": telegram_id})
    return user


def get_setting(key: str, default: str) -> str:
    setting = fetch_one("SELECT value FROM settings WHERE key = %(key)s", {"key": key})
    return setting["value"] if setting else default


def get_mandatory_channels():
    return fetch_all("SELECT * FROM mandatory_channels ORDER BY id")


def check_subscription(telegram_id: int):
    channels = get_mandatory_channels()
    missing = []
    for channel in channels:
        channel_id = channel["channel_id"]
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
            params={"chat_id": channel_id, "user_id": telegram_id},
            timeout=10,
        )
        data = response.json()
        if not data.get("ok"):
            missing.append(channel)
            continue
        status = data["result"]["status"]
        if status in {"left", "kicked"}:
            missing.append(channel)
    return missing


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/validate-subscription")
async def validate_subscription(payload: dict):
    telegram_id = int(payload.get("telegram_id", 0))
    if not telegram_id:
        raise HTTPException(status_code=400, detail="telegram_id is required")
    ensure_user(telegram_id, payload.get("username"))
    missing = check_subscription(telegram_id)
    return {"missing": missing}


@app.get("/api/tasks")
async def list_tasks(telegram_id: int):
    ensure_user(telegram_id)
    tasks = fetch_all(
        """
        SELECT t.*, ut.status, ut.enabled, ut.completed_at
        FROM tasks t
        LEFT JOIN user_tasks ut ON ut.task_id = t.id AND ut.user_id = %(telegram_id)s
        WHERE t.is_active = TRUE
          AND (ut.enabled IS NULL OR ut.enabled = TRUE)
        ORDER BY t.id
        """,
        {"telegram_id": telegram_id},
    )
    return {"tasks": tasks}


@app.post("/api/tasks/complete")
async def complete_task(payload: dict):
    telegram_id = int(payload.get("telegram_id", 0))
    task_id = int(payload.get("task_id", 0))
    if not telegram_id or not task_id:
        raise HTTPException(status_code=400, detail="telegram_id and task_id required")
    ensure_user(telegram_id)
    task = fetch_one("SELECT * FROM tasks WHERE id = %(task_id)s", {"task_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    execute(
        """
        INSERT INTO user_tasks (user_id, task_id, status, enabled, completed_at)
        VALUES (%(telegram_id)s, %(task_id)s, 'completed', TRUE, NOW())
        ON CONFLICT (user_id, task_id)
        DO UPDATE SET status = 'completed', completed_at = NOW()
        """,
        {"telegram_id": telegram_id, "task_id": task_id},
    )
    execute(
        """
        UPDATE users SET tokens = tokens + %(reward)s WHERE telegram_id = %(telegram_id)s
        """,
        {"reward": task["reward_tokens"], "telegram_id": telegram_id},
    )
    execute(
        """
        INSERT INTO token_history (user_id, change_amount, reason)
        VALUES (%(telegram_id)s, %(reward)s, %(reason)s)
        """,
        {"telegram_id": telegram_id, "reward": task["reward_tokens"], "reason": f"Task {task_id} completed"},
    )
    user = fetch_one("SELECT referred_by FROM users WHERE telegram_id = %(telegram_id)s", {"telegram_id": telegram_id})
    if user and user["referred_by"] and task["task_type"] == "deposit" and task["rarity"] == "Limited":
        execute(
            """
            UPDATE users SET tokens = tokens + 5000 WHERE telegram_id = %(referrer)s
            """,
            {"referrer": user["referred_by"]},
        )
        execute(
            """
            INSERT INTO token_history (user_id, change_amount, reason)
            VALUES (%(referrer)s, 5000, %(reason)s)
            """,
            {"referrer": user["referred_by"], "reason": f"Referral bonus for task {task_id}"},
        )
    return {"status": "ok"}


@app.post("/api/postback")
async def postback(payload: dict):
    telegram_id = int(payload.get("telegram_id", 0))
    task_id = int(payload.get("task_id", 0))
    event = payload.get("event", "")
    if event not in {"registration", "deposit"}:
        raise HTTPException(status_code=400, detail="Unsupported event")
    task = fetch_one("SELECT task_type FROM tasks WHERE id = %(task_id)s", {"task_id": task_id})
    if not task or task["task_type"] != event:
        raise HTTPException(status_code=400, detail="Task type mismatch")
    return await complete_task({"telegram_id": telegram_id, "task_id": task_id})


@app.get("/api/profile")
async def profile(telegram_id: int):
    user = ensure_user(telegram_id)
    token_rate = get_setting("token_rate", "1000=0.1")
    support_link = get_setting("support_link", "https://t.me/support")
    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_id}"
    return {
        "telegram_id": user["telegram_id"],
        "username": user["username"],
        "tokens": user["tokens"],
        "referral_link": referral_link,
        "token_rate": token_rate,
        "support_link": support_link,
    }


@app.get("/api/news")
async def list_news():
    news_items = fetch_all("SELECT * FROM news ORDER BY created_at DESC")
    return {"news": news_items}


def require_admin(telegram_id: int):
    if telegram_id != ADMIN_TELEGRAM_ID:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request, telegram_id: int):
    require_admin(telegram_id)
    stats = {
        "total_users": fetch_one("SELECT COUNT(*) AS count FROM users")["count"],
        "active_users": fetch_one("SELECT COUNT(*) AS count FROM users WHERE is_banned = FALSE")["count"],
        "completed_tasks": fetch_one("SELECT COUNT(*) AS count FROM user_tasks WHERE status = 'completed'")["count"],
        "token_circulation": fetch_one("SELECT COALESCE(SUM(tokens),0) AS sum FROM users")["sum"],
        "referrals": fetch_one("SELECT COUNT(*) AS count FROM users WHERE referred_by IS NOT NULL")["count"],
    }
    return templates.TemplateResponse(
        "admin_home.html",
        {"request": request, "stats": stats, "telegram_id": telegram_id},
    )


@app.get("/admin/tasks", response_class=HTMLResponse)
async def admin_tasks(request: Request, telegram_id: int):
    require_admin(telegram_id)
    tasks = fetch_all("SELECT * FROM tasks ORDER BY id")
    return templates.TemplateResponse(
        "admin_tasks.html",
        {"request": request, "tasks": tasks, "telegram_id": telegram_id},
    )


@app.post("/admin/tasks")
async def admin_tasks_create(
    telegram_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    task_type: str = Form("registration"),
    rarity: str = Form("Normal"),
    reward_tokens: int = Form(15000),
):
    require_admin(telegram_id)
    execute(
        """
        INSERT INTO tasks (title, description, task_type, rarity, reward_tokens, is_active)
        VALUES (%(title)s, %(description)s, %(task_type)s, %(rarity)s, %(reward_tokens)s, TRUE)
        """,
        {
            "title": title,
            "description": description,
            "task_type": task_type,
            "rarity": rarity,
            "reward_tokens": reward_tokens,
        },
    )
    return RedirectResponse(url=f"/admin/tasks?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/tasks/{task_id}/toggle")
async def admin_tasks_toggle(task_id: int, telegram_id: int = Form(...)):
    require_admin(telegram_id)
    execute(
        """
        UPDATE tasks SET is_active = NOT is_active WHERE id = %(task_id)s
        """,
        {"task_id": task_id},
    )
    return RedirectResponse(url=f"/admin/tasks?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/tasks/{task_id}/update")
async def admin_tasks_update(
    task_id: int,
    telegram_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    task_type: str = Form("registration"),
    rarity: str = Form("Normal"),
    reward_tokens: int = Form(15000),
):
    require_admin(telegram_id)
    execute(
        """
        UPDATE tasks
        SET title = %(title)s,
            description = %(description)s,
            task_type = %(task_type)s,
            rarity = %(rarity)s,
            reward_tokens = %(reward_tokens)s
        WHERE id = %(task_id)s
        """,
        {
            "title": title,
            "description": description,
            "task_type": task_type,
            "rarity": rarity,
            "reward_tokens": reward_tokens,
            "task_id": task_id,
        },
    )
    return RedirectResponse(url=f"/admin/tasks?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/tasks/{task_id}/delete")
async def admin_tasks_delete(task_id: int, telegram_id: int = Form(...)):
    require_admin(telegram_id)
    execute("DELETE FROM tasks WHERE id = %(task_id)s", {"task_id": task_id})
    return RedirectResponse(url=f"/admin/tasks?telegram_id={telegram_id}", status_code=303)


@app.get("/admin/channels", response_class=HTMLResponse)
async def admin_channels(request: Request, telegram_id: int):
    require_admin(telegram_id)
    channels = fetch_all("SELECT * FROM mandatory_channels ORDER BY id")
    return templates.TemplateResponse(
        "admin_channels.html",
        {"request": request, "channels": channels, "telegram_id": telegram_id},
    )


@app.post("/admin/channels")
async def admin_channels_add(
    telegram_id: int = Form(...),
    channel_id: int = Form(...),
    channel_title: str = Form(""),
    channel_username: str = Form(""),
):
    require_admin(telegram_id)
    execute(
        """
        INSERT INTO mandatory_channels (channel_id, channel_title, channel_username)
        VALUES (%(channel_id)s, %(channel_title)s, %(channel_username)s)
        """,
        {
            "channel_id": channel_id,
            "channel_title": channel_title,
            "channel_username": channel_username,
        },
    )
    return RedirectResponse(url=f"/admin/channels?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/channels/{channel_id}/update")
async def admin_channels_update(
    channel_id: int,
    telegram_id: int = Form(...),
    channel_title: str = Form(""),
    channel_username: str = Form(""),
):
    require_admin(telegram_id)
    execute(
        """
        UPDATE mandatory_channels
        SET channel_title = %(channel_title)s, channel_username = %(channel_username)s
        WHERE id = %(channel_id)s
        """,
        {"channel_title": channel_title, "channel_username": channel_username, "channel_id": channel_id},
    )
    return RedirectResponse(url=f"/admin/channels?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/channels/{channel_id}/delete")
async def admin_channels_delete(channel_id: int, telegram_id: int = Form(...)):
    require_admin(telegram_id)
    execute("DELETE FROM mandatory_channels WHERE id = %(channel_id)s", {"channel_id": channel_id})
    return RedirectResponse(url=f"/admin/channels?telegram_id={telegram_id}", status_code=303)


@app.get("/admin/news", response_class=HTMLResponse)
async def admin_news(request: Request, telegram_id: int):
    require_admin(telegram_id)
    news_items = fetch_all("SELECT * FROM news ORDER BY created_at DESC")
    return templates.TemplateResponse(
        "admin_news.html",
        {"request": request, "news": news_items, "telegram_id": telegram_id},
    )


@app.post("/admin/news")
async def admin_news_add(
    telegram_id: int = Form(...),
    title: str = Form(...),
    content: str = Form(""),
    media_type: str = Form(""),
    media_url: str = Form(""),
    button_text: str = Form(""),
    button_url: str = Form(""),
):
    require_admin(telegram_id)
    execute(
        """
        INSERT INTO news (title, content, media_type, media_url, button_text, button_url)
        VALUES (%(title)s, %(content)s, %(media_type)s, %(media_url)s, %(button_text)s, %(button_url)s)
        """,
        {
            "title": title,
            "content": content,
            "media_type": media_type,
            "media_url": media_url,
            "button_text": button_text,
            "button_url": button_url,
        },
    )
    return RedirectResponse(url=f"/admin/news?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/news/{news_id}/update")
async def admin_news_update(
    news_id: int,
    telegram_id: int = Form(...),
    title: str = Form(...),
    content: str = Form(""),
    media_type: str = Form(""),
    media_url: str = Form(""),
    button_text: str = Form(""),
    button_url: str = Form(""),
):
    require_admin(telegram_id)
    execute(
        """
        UPDATE news
        SET title = %(title)s,
            content = %(content)s,
            media_type = %(media_type)s,
            media_url = %(media_url)s,
            button_text = %(button_text)s,
            button_url = %(button_url)s
        WHERE id = %(news_id)s
        """,
        {
            "title": title,
            "content": content,
            "media_type": media_type,
            "media_url": media_url,
            "button_text": button_text,
            "button_url": button_url,
            "news_id": news_id,
        },
    )
    return RedirectResponse(url=f"/admin/news?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/news/{news_id}/delete")
async def admin_news_delete(news_id: int, telegram_id: int = Form(...)):
    require_admin(telegram_id)
    execute("DELETE FROM news WHERE id = %(news_id)s", {"news_id": news_id})
    return RedirectResponse(url=f"/admin/news?telegram_id={telegram_id}", status_code=303)


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(request: Request, telegram_id: int):
    require_admin(telegram_id)
    token_rate = get_setting("token_rate", "1000=0.1")
    support_link = get_setting("support_link", "https://t.me/support")
    return templates.TemplateResponse(
        "admin_settings.html",
        {"request": request, "token_rate": token_rate, "support_link": support_link, "telegram_id": telegram_id},
    )


@app.post("/admin/settings")
async def admin_settings_update(
    telegram_id: int = Form(...),
    token_rate: str = Form(...),
    support_link: str = Form(...),
):
    require_admin(telegram_id)
    execute(
        """
        INSERT INTO settings (key, value) VALUES ('token_rate', %(token_rate)s)
        ON CONFLICT (key) DO UPDATE SET value = %(token_rate)s
        """,
        {"token_rate": token_rate},
    )
    execute(
        """
        INSERT INTO settings (key, value) VALUES ('support_link', %(support_link)s)
        ON CONFLICT (key) DO UPDATE SET value = %(support_link)s
        """,
        {"support_link": support_link},
    )
    return RedirectResponse(url=f"/admin/settings?telegram_id={telegram_id}", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, telegram_id: int, query: str = ""):
    require_admin(telegram_id)
    if query:
        users = fetch_all(
            """
            SELECT * FROM users
            WHERE CAST(telegram_id AS TEXT) = %(query)s OR username ILIKE %(like)s
            ORDER BY created_at DESC
            """,
            {"query": query, "like": f"%{query}%"},
        )
    else:
        users = fetch_all("SELECT * FROM users ORDER BY created_at DESC LIMIT 50")
    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "users": users, "telegram_id": telegram_id, "query": query},
    )


@app.post("/admin/users/{telegram_id}")
async def admin_user_update(
    telegram_id: int,
    telegram_id: int = Form(...),
    tokens: int = Form(...),
    is_banned: Optional[str] = Form(None),
):
    require_admin(telegram_id)
    banned = is_banned == "on"
    execute(
        """
        UPDATE users SET tokens = %(tokens)s, is_banned = %(is_banned)s WHERE telegram_id = %(telegram_id)s
        """,
        {"tokens": tokens, "is_banned": banned, "telegram_id": telegram_id},
    )
    return RedirectResponse(url=f"/admin/users?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/users/{telegram_id}/tasks/toggle")
async def admin_user_task_toggle(
    telegram_id: int,
    telegram_id: int = Form(...),
    task_id: int = Form(...),
):
    require_admin(telegram_id)
    execute(
        """
        INSERT INTO user_tasks (user_id, task_id, status, enabled)
        VALUES (%(telegram_id)s, %(task_id)s, 'pending', FALSE)
        ON CONFLICT (user_id, task_id)
        DO UPDATE SET enabled = NOT user_tasks.enabled
        """,
        {"telegram_id": telegram_id, "task_id": task_id},
    )
    return RedirectResponse(url=f"/admin/users?telegram_id={telegram_id}", status_code=303)


@app.get("/admin/broadcasts", response_class=HTMLResponse)
async def admin_broadcasts(request: Request, telegram_id: int):
    require_admin(telegram_id)
    return templates.TemplateResponse(
        "admin_broadcasts.html",
        {"request": request, "telegram_id": telegram_id},
    )


@app.post("/admin/broadcasts")
async def admin_broadcasts_send(
    telegram_id: int = Form(...),
    message: str = Form(...),
    media_type: str = Form(""),
    media_url: str = Form(""),
    button_text: str = Form(""),
    button_url: str = Form(""),
):
    require_admin(telegram_id)
    users = fetch_all("SELECT telegram_id FROM users WHERE is_banned = FALSE")
    reply_markup = None
    if button_url:
        reply_markup = {"inline_keyboard": [[{"text": button_text or "Open", "url": button_url}]]}
    for user in users:
        if media_type == "image" and media_url:
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                params={
                    "chat_id": user["telegram_id"],
                    "photo": media_url,
                    "caption": message,
                    "reply_markup": reply_markup,
                },
                timeout=10,
            )
        elif media_type == "video" and media_url:
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                params={
                    "chat_id": user["telegram_id"],
                    "video": media_url,
                    "caption": message,
                    "reply_markup": reply_markup,
                },
                timeout=10,
            )
        else:
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                params={
                    "chat_id": user["telegram_id"],
                    "text": message,
                    "reply_markup": reply_markup,
                },
                timeout=10,
            )
    return RedirectResponse(url=f"/admin/broadcasts?telegram_id={telegram_id}", status_code=303)


@app.post("/admin/login")
async def admin_login(telegram_id: int = Form(...)):
    return RedirectResponse(url=f"/admin?telegram_id={telegram_id}", status_code=303)
