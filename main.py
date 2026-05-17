import os
import json
import asyncio
from datetime import datetime
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from openai import AsyncOpenAI

# Ключи и доступы
TELEGRAM_TOKEN = "8820240792:AAFXjs_djEYwPVCwqeOyM7kguSIBV2OdPYw"
OPENAI_API_KEY = "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"
SUPABASE_URL = "https://elcmxjlqhsluzimuvdqe.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVsY214amxxaHNsdXppbXV2ZHFlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwMjMzMzYsImV4cCI6MjA5NDU5OTMzNn0.TJQ1OGX1Wlq_hQC0DN5brBp2BCcB35KNewUy6n75VV0"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты — ультимативный ИИ-ассистент. Разбери запрос пользователя и верни ТОЛЬКО чистый JSON.
Типы (type): 'expense', 'income', 'task', 'lesson', 'note', 'student_pay', 'student_lesson'.
Формат ответа JSON:
{
  "type": "expense/income/task/lesson/note/student_pay/student_lesson",
  "amount": число_или_null,
  "category": "категория_или_null",
  "description": "суть действия",
  "date": "ГГГГ-ММ-ДД",
  "student_name": "Имя Ученика или null",
  "count": число_уроков_или_null
}
Текущая дата: """ + datetime.now().strftime("%Y-%m-%d") + """
"""

async def parse_via_ai(text: str) -> dict:
    try:
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"type": "unknown"}

async def check_missed_tasks(chat_id: int) -> str:
    url = f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=eq.Новая"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    alert_text = ""
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                for item in res.json():
                    due_date = datetime.strptime(item["due_date"], "%Y-%m-%d").date()
                    if due_date < datetime.now().date():
                        type_str = "Урок" if item["type"] == "lesson" else "Задача"
                        alert_text += f"\n\n⚠️ *ПРОПУЩЕНО:* {type_str}: {item['description']} (было на {item['due_date']})"
                        await client.patch(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{item['id']}", headers={**headers, "Content-Type": "application/json"}, json={"status": "Пропущено"})
    except Exception as e:
        print(f"Ошибка проверки дедлайнов: {e}")
    return alert_text

async def save_data(data: dict, chat_id: int) -> str:
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        try:
            if data["type"] in ["expense", "income"]:
                payload = {"chat_id": chat_id, "type": data["type"], "amount": data["amount"], "category": data.get("category", "Разное"), "description": data["description"], "date": data.get("date")}
                await client.post(f"{SUPABASE_URL}/rest/v1/finance", headers=headers, json=payload)
                return f"💰 [Облако] {'Расход' if data['type']=='expense' else 'Доход'} сохранен: {data['amount']} сум ({data['description']})"
            
            elif data["type"] in ["task", "lesson"]:
                payload = {"chat_id": chat_id, "type": data["type"], "description": data["description"], "due_date": data.get("date"), "status": "Новая"}
                await client.post(f"{SUPABASE_URL}/rest/v1/tasks", headers=headers, json=payload)
                return f"📅 [Облако] {data['type'].capitalize()} добавлен на {data.get('date')}: {data['description']}"
            
            elif data["type"] == "note":
                payload = {"chat_id": chat_id, "content": data["description"], "tags": data.get("category", "Общее")}
                await client.post(f"{SUPABASE_URL}/rest/v1/notes", headers=headers, json=payload)
                return f"🧠 [Второй Мозг] Заметка сохранена: \"{data['description']}\""
            
            elif data["type"] == "student_pay":
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{data['student_name']}", headers=headers)
                students = st_res.json()
                lessons_count = data.get("count", 1)
                if students:
                    new_bal = students[0]["balance_lessons"] + lessons_count
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                else:
                    await client.post(f"{SUPABASE_URL}/rest/v1/students", headers=headers, json={"chat_id": chat_id, "name": data["student_name"], "balance_lessons": lessons_count})
                return f"🎓 [Ученики] Добавлено +{lessons_count} уроков для {data['student_name']}."

            elif data["type"] == "student_lesson":
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{data['student_name']}", headers=headers)
                students = st_res.json()
                if students:
                    new_bal = max(0, students[0]["balance_lessons"] - 1)
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                    return f"📉 [Ученики] Проведен урок у {data['student_name']}. Остаток: {new_bal} уроков."
                return f"❌ Ученик {data['student_name']} не найден."
        except Exception as e:
            return f"❌ Ошибка базы: {e}"
    return "🤷‍♂️ Не распознано."

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer("🚀 Бесплатный ИИ-планировщик 24/7 запущен на чистых вебхуках!\nКоманды: `/today`, `/charts`")

@dp.message(F.text == "/today")
async def get_today(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{message.chat.id}&due_date=eq.{datetime.now().strftime('%Y-%m-%d')}", headers=headers)
        tasks = res.json()
        if not tasks: return await message.answer("☕️ На сегодня задач нет.")
        reply = "📅 *Расписание на сегодня:*\n\n"
        for t in tasks: reply += f"{'🎓' if t['type']=='lesson' else '📌'} {t['description']}\n"
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "/charts")
async def get_charts(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/finance?chat_id=eq.{message.chat.id}&type=eq.expense", headers=headers)
        expenses = res.json()
        if not expenses: return await message.answer("📊 Нет расходов.")
        cat_map, total = {}, 0
        for e in expenses:
            cat = e["category"] or "Разное"
            cat_map[cat] = cat_map.get(cat, 0) + e["amount"]
            total += e["amount"]
        reply = f"📊 *АНАЛИТИКА (Всего: {int(total)} сум)*\n\n"
        for cat, amt in sorted(cat_map.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / total) * 100
            reply += f"🔹 *{cat}*\n`{'🟩'*max(1, round(pct/10))}` {int(pct)}% ({int(amt)} сум)\n\n"
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(message: Message):
    await message.answer("🔄 Анализирую...")
    ai_data = await parse_via_ai(message.text)
    reply = await save_data(ai_data, message.chat.id)
    missed_alert = await check_missed_tasks(message.chat.id)
    await message.answer(reply + missed_alert, parse_mode="Markdown")

@dp.message(F.voice)
async def handle_voice(message: Message):
    await message.answer("📥 Скачиваю голос...")
    file = await bot.get_file(message.voice.file_id)
    local_file = "voice.ogg"
    await bot.download_file(file.file_path, local_file)
    try:
        with open(local_file, "rb") as f:
            transcription = await ai_client.audio.transcriptions.create(model="whisper-1", file=f)
        await message.answer(f"🗣 *Вы сказали:* {transcription.text}", parse_mode="Markdown")
        ai_data = await parse_via_ai(transcription.text)
        reply = await save_data(ai_data, message.chat.id)
        missed_alert = await check_missed_tasks(message.chat.id)
        await message.answer(reply + missed_alert, parse_mode="Markdown")
    finally:
        if os.path.exists(local_file): os.remove(local_file)

# ================= КАСТОМНЫЙ ХЕНДЛЕР НА ЧИСТОМ AIOHTTP =================
async def handle_telegram_webhook(request):
    """Принимаем обновления напрямую от Telegram без участия модулей aiogram"""
    try:
        bot_dict = json.loads(await request.text())
        update = Update.model_validate(bot_dict, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        print(f"Ошибка кастомного вебхука: {e}")
    return web.Response(text="OK")

async def on_startup_service(app):
    """При старте сервера принудительно связываем бота с Render"""
    webhook_url = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"
    await bot.set_webhook(webhook_url)
    print(f"🚀 Кастомный вебхук успешно привязан к: {webhook_url}")

def main():
    app = web.Application()
    app.router.add_post('/webhook', handle_telegram_webhook)
    app.on_startup.append(on_startup_service)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    main()
