import os
import json
import asyncio
from datetime import datetime, timedelta
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update
from openai import AsyncOpenAI

# Ключи Telegram и Supabase (Оставляем неизменными)
TELEGRAM_TOKEN = "8820240792:AAFXjs_djEYwPVCwqeOyM7kguSIBV2OdPYw"
SUPABASE_URL = "https://elcmxjlqhsluzimuvdqe.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVsY214amxxaHNsdXppbXV2ZHFlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwMjMzMzYsImV4cCI6MjA5NDU5OTMzNn0.TJQ1OGX1Wlq_hQC0DN5brBp2BCcB35KNewUy6n75VV0"

# Ключ OpenAI безопасно берется из настроек Render
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

def get_now_tashkent():
    """Всегда возвращает точное время в Ташкенте (UTC+5)"""
    return datetime.utcnow() + timedelta(hours=5)

SYSTEM_PROMPT = """
Ты — ультимативный ИИ-ассистент. Разбери запрос пользователя и верни ТОЛЬКО чистый JSON.

Типы (type):
- 'expense' / 'income' (расходы / доходы)
- 'task' / 'lesson' (добавление новой задачи или нового урока в календарь)
- 'note' (сохранение новой заметки, мысли или идеи в блокнот)
- 'reminder' (установка напоминания на конкретное время)
- 'student_pay' / 'student_lesson' (учёт баланса занятий учеников)
- 'get_notes' (запрос на просмотр сохраненных заметок)
- 'get_tasks' (запрос на просмотр списка дел, планов или расписания)
- 'get_balances' (если пользователь хочет УЗНАТЬ БАЛАНС УЧЕНИКОВ, спросить "сколько уроков осталось у Маши" или "покажи балансы")
- 'complete_task' (если пользователь говорит, что ВЫПОЛНИЛ, СДЕЛАЛ, ЗАВЕРШИЛ или хочет УДАЛИТЬ какую-то задачу/урок из планов)
- 'other' (вежливость, "спасибо", "привет")

Формат ответа JSON:
{
  "type": "выбранный_тип",
  "amount": число_или_null,
  "category": "категория_или_null",
  "description": "суть действия / текст заметки или напоминания / ключевые слова задачи для закрытия",
  "date": "ГГГГ-ММ-ДД",
  "remind_at": "ГГГГ-ММ-ДД ВВ:ММ:СС или null", 
  "student_name": "Имя Ученика или null",
  "count": число_уроков_или_null
}
Текущие дата и время для расчета (Ташкент): """ + get_now_tashkent().strftime("%Y-%m-%d %H:%M:%S") + """
"""

async def parse_via_ai(text: str) -> dict:
    try:
        current_prompt = SYSTEM_PROMPT.split("Текущие дата")[0] + f"Текущие дата и время для расчета (Ташкент): {get_now_tashkent().strftime('%Y-%m-%d %H:%M:%S')}\n"
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": current_prompt}, {"role": "user", "content": text}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Ошибка ИИ парсинга: {e}")
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
                    if due_date < get_now_tashkent().date():
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
            # === ФИЧА 1: ПРОСМОТР БАЛАНСА УЧЕНИКОВ ===
            if data["type"] == "get_balances":
                name_query = data.get("student_name")
                if name_query:
                    url = f"{SUPABASE_URL}/rest/v1/students?chat_id=eq.{chat_id}&name=ilike.*{name_query}*"
                else:
                    url = f"{SUPABASE_URL}/rest/v1/students?chat_id=eq.{chat_id}"
                
                res = await client.get(url, headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "🎓 *Текущий баланс уроков:* \n\n"
                    for st in res.json():
                        reply += f"👤 *{st['name']}:* {st['balance_lessons']} уроков осталось.\n"
                    return reply
                return "🎓 Учеников с таким именем или активным балансом не найдено."

            # === ФИЧА 2: ВЫПОЛНЕНИЕ / ЗАКРЫТИЕ ЗАДАЧ ИЗ КАЛЕНДАРЯ ===
            elif data["type"] == "complete_task":
                keyword = data.get("description", "")
                # Находим активную задачу, подходящую под описание
                res = await client.patch(
                    f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=neq.Выполнено&description=ilike.*{keyword}*",
                    headers=headers,
                    json={"status": "Выполнено"}
                )
                if res.status_code in [200, 204]:
                    return f"✅ [Календарь] Задача/урок, содержащая \"{keyword}\", успешно отмечена как *Выполненая*!"
                return f"❌ Не удалось найти активных задач со словом \"{keyword}\"."

            elif data["type"] == "get_notes":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/notes?chat_id=eq.{chat_id}", headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "🧠 *Твой Второй Мозг (Сохраненные заметки):*\n\n"
                    for i, note in enumerate(res.json(), 1):
                        reply += f"{i}. {note['content']}\n"
                    return reply
                return "🧠 В твоем Втором Мозге пока нет заметок."

            elif data["type"] == "get_tasks":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=eq.Новая", headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "📌 *Твои актуальные задачи и планы:*\n\n"
                    for t in res.json():
                        icon = "🎓" if t["type"] == "lesson" else "📌"
                        reply += f"{icon} [{t['due_date']}] {t['description']}\n"
                    return reply
                return "📌 У тебя нет невыполненных планов и задач!"

            elif data["type"] == "other":
                return "Рад помочь! 😊 На связи. Если нужно записать расходы, баланс или планы — просто скажи."

            elif data["type"] == "reminder":
                payload = {"chat_id": chat_id, "text": data["description"], "remind_at": data.get("remind_at"), "status": "pending"}
                await client.post(f"{SUPABASE_URL}/rest/v1/reminders", headers=headers, json=payload)
                return f"🔔 *Напоминание зафиксировано!* \n📅 Время: {data.get('remind_at')}\n🎯 Суть: \"{data['description']}\""

            elif data["type"] in ["expense", "income"]:
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
    await message.answer("🚀 Супер-Ассистент обновлен до максимальной версии! \n\nТеперь доступны все фичи контроля финансов, планов и баланса студентов.")

@dp.message(F.text == "/today")
async def get_today(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{message.chat.id}&due_date=eq.{get_now_tashkent().strftime('%Y-%m-%d')}", headers=headers)
        tasks = res.json()
        if not tasks: return await message.answer("☕️ На сегодня задач нет.")
        reply = "📅 *Расписание на сегодня:*\n\n"
        for t in tasks: reply += f"{'🎓' if t['type']=='lesson' else '📌'} {t['description']}\n"
        await message.answer(reply, parse_mode="Markdown")

# ================= ФИЧА 3: ПОЛНАЯ АНАЛИТИКА ДОХОДОВ И РАСХОДОВ =================
@dp.message(F.text == "/charts")
async def get_charts(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/finance?chat_id=eq.{message.chat.id}", headers=headers)
        records = res.json()
        if not records: return await message.answer("📊 Нет финансовых данных для построения аналитики.")
        
        total_income = 0
        total_expense = 0
        cat_map = {}
        
        for r in records:
            amount = r.get("amount") or 0
            if r["type"] == "income":
                total_income += amount
            elif r["type"] == "expense":
                total_expense += amount
                cat = r["category"] or "Разное"
                cat_map[cat] = cat_map.get(cat, 0) + amount
                
        net_profit = total_income - total_expense
        
        reply = f"📊 *ПОЛНЫЙ ФИНАНСОВЫЙ ОТЧЕТ*\n\n"
        reply += f"💰 *Всего доходов:* {int(total_income)} сум\n"
        reply += f"📉 *Всего расходов:* {int(total_expense)} сум\n"
        reply += f"🟩 *Чистая прибыль:* {int(net_profit)} сум\n\n"
        reply += "🍕 *Детализация твоих расходов:*\n"
        
        if cat_map:
            for cat, amt in sorted(cat_map.items(), key=lambda x: x[1], reverse=True):
                pct = (amt / total_expense) * 100 if total_expense > 0 else 0
                reply += f"🔹 *{cat}*\n`{'🟩'*max(1, round(pct/10))}` {int(pct)}% ({int(amt)} сум)\n\n"
        else:
            reply += "Расходы за этот период отсутствуют."
            
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
    except Exception as e:
        await message.answer(f"❌ Ошибка ИИ-обработки голоса: {e}")
    finally:
        if os.path.exists(local_file): os.remove(local_file)

async def handle_telegram_webhook(request):
    try:
        bot_dict = json.loads(await request.text())
        update = Update.model_validate(bot_dict, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        print(f"Ошибка вебхука: {e}")
    return web.Response(text="OK")

async def handle_keepalive_ping(request):
    return web.Response(text="I am awake!")

# ================= ВНУТРЕННИЙ РОБОТ НАПОМИНАНИЙ =================
async def internal_reminder_scheduler():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/reminders?status=eq.pending"
    print("🤖 Внутренний планировщик напоминаний успешно запущен!")
    while True:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    reminders = res.json()
                    now = get_now_tashkent()
                    
                    for r in reminders:
                        remind_str = r["remind_at"].replace("T", " ").split(".")[0]
                        remind_time = datetime.strptime(remind_str, "%Y-%m-%d %H:%M:%S")
                        
                        if remind_time <= now:
                            try:
                                await bot.send_message(r["chat_id"], f"⏰ *НАПОМИНАНИЕ:* \n\n{r['text']}", parse_mode="Markdown")
                                await client.patch(
                                    f"{SUPABASE_URL}/rest/v1/reminders?id=eq.{r['id']}", 
                                    headers={**headers, "Content-Type": "application/json"}, 
                                    json={"status": "sent"}
                                )
                            except Exception as msg_err:
                                print(f"Ошибка отправки сообщения: {msg_err}")
        except Exception as e:
            print(f"Ошибка проверки во внутреннем цикле: {e}")
        await asyncio.sleep(60)

async def on_startup_service(app):
    webhook_url = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"
    await bot.set_webhook(webhook_url)
    asyncio.create_task(internal_reminder_scheduler())

def main():
    app = web.Application()
    app.router.add_post('/webhook', handle_telegram_webhook)
    app.router.add_get('/cron', handle_keepalive_ping)
    app.on_startup.append(on_startup_service)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    main()
