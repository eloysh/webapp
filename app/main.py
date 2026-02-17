from __future__ import annotations

import os
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from .config import settings
from .storage import Storage
from .telegram_api import TelegramAPI
from .apifree_client import ApiFreeClient
from .bot_logic import handle_update

app = FastAPI(title="Creator Kristina Bot (ApiFree)")

storage = Storage(settings.DB_PATH)
tg = TelegramAPI(settings.BOT_TOKEN)
apifree = ApiFreeClient(settings.APIFREE_BASE_URL, settings.APIFREE_API_KEY)

@app.on_event("startup")
async def startup():
    os.makedirs(os.path.dirname(settings.DB_PATH) or ".", exist_ok=True)
    await storage.init()

    # set webhook
    webhook_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/telegram/webhook/{settings.WEBHOOK_SECRET}"
    try:
        await tg.set_webhook(webhook_url)
        print(f"[startup] setWebhook -> {webhook_url}")
    except Exception as e:
        print(f"[startup] setWebhook failed: {e}")

@app.get("/health")
async def health():
    return {"ok": True}



async def _deliver_image_to_tg(tg_id: int, request_id: str):
    """Poll provider and send final image to Telegram chat."""
    try:
        await tg.send_message(tg_id, f"🧠 Задача принята. ID: <code>{request_id}</code>\nЖду результат…")
    except Exception:
        pass
    for _ in range(120):
        try:
            res = await apifree.image_result(request_id)
            data = res
            url = data.get('url') or data.get('output_url') or (data.get('result') or {}).get('url') or (data.get('result') or {}).get('output_url')
            if not url:
                imgs = (data.get('images') or (data.get('result') or {}).get('images') or [])
                if imgs:
                    url = imgs[0]
            status = (data.get('status') or data.get('state') or data.get('phase') or '').lower()
            if url:
                await tg.send_photo(tg_id, url, caption='✅ Готово!')
                return
            if 'fail' in status or 'error' in status:
                await tg.send_message(tg_id, f"❌ Ошибка генерации: <pre>{str(data)[:3500]}</pre>")
                return
        except Exception as e:
            # keep polling; transient provider issues happen
            last = str(e)
        import asyncio
        await asyncio.sleep(2)
    try:
        await tg.send_message(tg_id, '⌛ Не дождалась результата (timeout). Попробуй ещё раз.')
    except Exception:
        pass


async def _deliver_video_to_tg(tg_id: int, request_id: str):
    """Poll provider and send final video to Telegram chat."""
    try:
        await tg.send_message(tg_id, f"🎬 Задача принята. ID: <code>{request_id}</code>\nЖду результат…")
    except Exception:
        pass
    for _ in range(180):
        try:
            res = await apifree.video_result(request_id)
            data = res
            url = data.get('url') or data.get('output_url') or (data.get('result') or {}).get('url') or (data.get('result') or {}).get('output_url')
            if not url:
                vids = (data.get('videos') or (data.get('result') or {}).get('videos') or [])
                if vids:
                    url = vids[0]
            status = (data.get('status') or data.get('state') or data.get('phase') or '').lower()
            if url:
                await tg.send_video(tg_id, url, caption='✅ Готово!')
                return
            if 'fail' in status or 'error' in status:
                await tg.send_message(tg_id, f"❌ Ошибка генерации: <pre>{str(data)[:3500]}</pre>")
                return
        except Exception:
            pass
        import asyncio
        await asyncio.sleep(2)
    try:
        await tg.send_message(tg_id, '⌛ Не дождалась результата (timeout). Попробуй ещё раз.')
    except Exception:
        pass
@app.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != settings.WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")
    update = await request.json()
    # optional: inject bot username if you set BOT_USERNAME env, otherwise ignore
    update["bot_username"] = os.getenv("BOT_USERNAME", "")
    try:
        await handle_update(storage, tg, apifree, update)
    except Exception as e:
        print("handle_update error:", e)
    return {"ok": True}

# -------- Mini App API --------
@app.get("/api/me")
async def api_me(tg_id: int):
    u = await storage.get_user(tg_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return {"tg_id": u.tg_id, "credits_free": u.credits_free, "credits_pro": u.credits_pro}

@app.post("/api/chat")
async def api_chat(payload: dict):
    tg_id = int(payload.get("tg_id", 0))
    text = (payload.get("text") or "").strip()
    if not tg_id or not text:
        raise HTTPException(status_code=400, detail="tg_id and text required")
    # Admins bypass credit checks (useful while payments/referrals are being wired)
    if tg_id not in settings.admin_ids():
        ok = await storage.consume_credit(tg_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "no_credits"}, status_code=402)

    try:
        answer = await apifree.chat(settings.APIFREE_CHAT_MODEL, [{"role": "user", "content": text}])
        return {"ok": True, "answer": answer}
    except Exception as e:
        # Make provider errors readable in the UI
        return JSONResponse({"ok": False, "error": "provider_error", "detail": str(e)}, status_code=502)

@app.post("/api/image/submit")
async def api_image_submit(payload: dict, background_tasks: BackgroundTasks):
    tg_id = int(payload.get("tg_id", 0))
    prompt = (payload.get("prompt") or "").strip()
    if not tg_id or not prompt:
        raise HTTPException(status_code=400, detail="tg_id and prompt required")
    if tg_id not in settings.admin_ids():
        ok = await storage.consume_credit(tg_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "no_credits"}, status_code=402)

    # Pass-through payload to provider with a small normalization layer.
    provider_payload = dict(payload)
    provider_payload.pop("tg_id", None)
    # If you want to force model from env, keep it here; otherwise provider can infer.
    provider_payload.setdefault("model", settings.APIFREE_IMAGE_MODEL)

    try:
        res = await apifree.image_submit(provider_payload)
        resp_data = (res or {}).get("resp_data") or {}
        request_id = (
            res.get("request_id")
            or resp_data.get("request_id")
            or res.get("id")
            or res.get("task_id")
            or (res.get("result") or {}).get("id")
        )
        if request_id and payload.get("deliver_to_tg", True):
            background_tasks.add_task(_deliver_image_to_tg, tg_id, str(request_id))
        return {"ok": True, "request_id": request_id, "apifree": res}
    except Exception as e:
        return JSONResponse({"ok": False, "error": "provider_error", "detail": str(e)}, status_code=502)


@app.get("/api/image/result/{request_id}")
async def api_image_result(request_id: str):
    try:
        res = await apifree.image_result(request_id)
        return {"ok": True, "apifree": res}
    except Exception as e:
        return JSONResponse({"ok": False, "error": "provider_error", "detail": str(e)}, status_code=502)


@app.post("/api/video/submit")
async def api_video_submit(payload: dict, background_tasks: BackgroundTasks):
    tg_id = int(payload.get("tg_id", 0))
    prompt = (payload.get("prompt") or "").strip()
    if not tg_id or not prompt:
        raise HTTPException(status_code=400, detail="tg_id and prompt required")
    if tg_id not in settings.admin_ids():
        ok = await storage.consume_credit(tg_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "no_credits"}, status_code=402)

    provider_payload = dict(payload)
    provider_payload.pop("tg_id", None)
    provider_payload.setdefault("model", settings.APIFREE_VIDEO_MODEL)

    try:
        res = await apifree.video_submit(provider_payload)
        resp_data = (res or {}).get("resp_data") or {}
        request_id = (
            res.get("request_id")
            or resp_data.get("request_id")
            or res.get("id")
            or res.get("task_id")
            or (res.get("result") or {}).get("id")
        )
        if request_id and payload.get("deliver_to_tg", True):
            background_tasks.add_task(_deliver_video_to_tg, tg_id, str(request_id))
        return {"ok": True, "request_id": request_id, "apifree": res}
    except Exception as e:
        return JSONResponse({"ok": False, "error": "provider_error", "detail": str(e)}, status_code=502)


@app.get("/api/video/result/{request_id}")
async def api_video_result(request_id: str):
    try:
        res = await apifree.video_result(request_id)
        return {"ok": True, "apifree": res}
    except Exception as e:
        return JSONResponse({"ok": False, "error": "provider_error", "detail": str(e)}, status_code=502)

# -------- static miniapp --------
WEBAPP_DIR =WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "..", "webapp")
app.mount("/webapp", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")

@app.get("/")
async def root():
    return HTMLResponse("""<html><body>
    <h3>Creator Kristina Bot</h3>
    <ul>
      <li><a href='/webapp/'>Open Mini App</a></li>
      <li><a href='/health'>Health</a></li>
    </ul>
    </body></html>""")
