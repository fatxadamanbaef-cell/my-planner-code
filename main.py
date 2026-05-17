import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update
from openai import AsyncOpenAI

# Ключи Telegram и Supabase
TELEGRAM_TOKEN = "8820240792:AAFXjs_djEYwPVCwqeOyM7kguSIBV2OdPYw"
SUPABASE_URL = "https://elcmxjlqhsluzimuvdqe.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVsY214amxxaHNsdXppbXV2ZHFlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwMjMzMzYsImV4cCI6MjA5NDU5OTMzNn0.TJQ1OGX1Wlq_hQC0DN5brBp2BCcB35KNewUy6n75VV0"

# Ключ OpenAI безопасно берется из настроек Render
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

LAST_EXPENSE_PROMPT_DATE = ""

def get_now_tashkent():
    """Возвращает точное время в Ташкенте (UTC+5)"""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)

SYSTEM_PROMPT = """
Ты — ультимативный ИИ-ассистент. Разбери запрос пользователя и верни ТОЛЬКО чистый JSON.

Типы (type):
- 'expense' / 'income' (СТРОГО добавление НОВОГО расхода или дохода. Например: "купил кофе", "заработал 50000")
- 'task' / 'lesson' (добавление новой задачи или урока в календарь)
- 'note' (сохранение новой заметки/мысли в блокнот)
- 'reminder' (установка напоминания)
- 'student_pay' / 'student_lesson' / 'student_set_balance' (учет баланса студентов)
- 'get_notes' / 'get_tasks' / 'get_reminders' / 'get_balances' (просмотр списков данных)
- 'get_finance' (если пользователь хочет ПОСМОТРЕТЬ ИСТОРИЮ своих денег, пишет "покажи расходы", "куда потратил деньги из Еда", "покажи доходы". Если в запросе звучит конкретная категория, запиши её название в поле 'category'. Если спрашивает строго про расходы, запиши 'expense' в поле 'description', если про доходы — 'income' в поле 'description')
- 'complete_task' (завершение задачи из календаря)
- 'other' (вежливость, простые фразы)

Формат ответа JSON:
{
  "type": "выбранный_тип",
  "amount": число_или_null,
  "category": "категория_или_null",
  "description": "суть действия / тип фильтра для финансов",
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
            raw_name = data.get("student_name")
            student_name = raw_name.strip().lower().capitalize() if raw_name else None

            # === НОВАЯ ЛОГИКА: ПОИСК И ДЕТАЛИЗАЦИЯ РАСХОДОВ/ДОХОДОВ ===
            if data["type"] == "get_finance":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/finance?chat_id=eq.{chat_id}", headers=headers)
                if res.status_code == 200 and res.json():
                    records = res.json()
                    filter_cat = data.get("category")
                    filter_type = data.get("description") # 'expense' или 'income'
                    
                    reply = "💰 *История твоих финансовых записей:*\n\n"
                    found = False
                    for r in sorted(records, key=lambda x: x.get('date', ''), reverse=True):
                        if filter_cat and filter_cat.lower() not in (r.get("category") or "").lower(): continue
                        if filter_type and r.get("type") != filter_type: continue
                        
                        found = True
                        icon = "📉 Расход:" if r["type"] == "expense" else "🪙 Доход:"
                        reply += f"{icon} *{int(r['amount'])} сум* — {r['description']} [Категория: {r['category']}] ({r.get('date', '---')})\n"
                    
                    return reply if found else "🔍 Записей по твоему финансовому запросу не найдено."
                return "💰 У тебя пока нет сохраненных финансовых записей."

            elif data["type"] == "get_balances":
                url = f"{SUPABASE_URL}/rest/v1/students?chat_id=eq.{chat_id}&name=ilike.*{student_name}*" if student_name else f"{SUPABASE_URL}/rest/v1/students?chat_id=eq.{chat_id}"
                res = await client.get(url, headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "🎓 *Текущий баланс уроков:* \n\n"
                    for st in res.json(): reply += f"👤 *{st['name']}:* {st['balance_lessons']} уроков осталось.\n"
                    return reply
                return "🎓 Учеников с активным балансом не найдено."

            elif data["type"] == "get_reminders":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/reminders?chat_id=eq.{chat_id}&status=eq.pending", headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "🔔 *Твои активные напоминания:*\n\n"
                    for r in res.json():
                        if r.get("remind_at"):
                            t_str = r["remind_at"].replace("T", " ").split(".")[0]
                            reply += f"🔹 [{t_str}] {r['text']}\n"
                    return reply
                return "🔔 У тебя нет активных напоминаний."

            elif data["type"] == "student_set_balance":
                lessons_count = data.get("count", 0)
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{student_name}", headers=headers)
                students = st_res.json()
                if students:
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": lessons_count})
                else:
                    await client.post(f"{SUPABASE_URL}/rest/v1/students", headers=headers, json={"chat_id": chat_id, "name": student_name, "balance_lessons": lessons_count})
                return f"🔧 [Ученики] Баланс пользователя *{student_name}* успешно изменен на *{lessons_count}* уроков."

            elif data["type"] == "student_pay":
                lessons_count = data.get("count", 1) or 1
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{student_name}", headers=headers)
                students = st_res.json()
                if students:
                    new_bal = students[0]["balance_lessons"] + lessons_count
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                else:
                    await client.post(f"{SUPABASE_URL}/rest/v1/students", headers=headers, json={"chat_id": chat_id, "name": student_name, "balance_lessons": lessons_count})
                return f"🎓 [Ученики] Добавлено +{lessons_count} уроков для {student_name}."

            elif data["type"] == "student_lesson":
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{student_name}", headers=headers)
                students = st_res.json()
                lessons_count = data.get("count", 1) or 1
                if students:
                    new_bal = max(0, students[0]["balance_lessons"] - lessons_count)
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                    return f"📉 [Ученики] Списано {lessons_count} урок(ов) у {student_name}. Остаток: {new_bal} уроков."
                return f"❌ Ученик {student_name} не найден."

            elif data["type"] == "complete_task":
                keyword = data.get("description", "")
                res = await client.patch(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=neq.Выполнено&description=ilike.*{keyword}*", headers=headers, json={"status": "Выполнено"})
                return f"✅ [Календарь] Задача, содержащая \"{keyword}\", успешно выполнена!" if res.status_code in [200, 204] else f"❌ Задача \"{keyword}\" не найдена."

            elif data["type"] == "get_notes":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/notes?chat_id=eq.{chat_id}", headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "🧠 *Сохраненные заметки:*\n\n"
                    for i, note in enumerate(res.json(), 1): reply += f"{i}. {note['content']}\n"
                    return reply
                return "🧠 В блокноте пусто."

            elif data["type"] == "get_tasks":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=eq.Новая", headers=headers)
                if res.status_code == 200 and res.json():
                    reply = "📌 *Актуальные задачи:*\n\n"
                    for t in res.json(): reply += f"{'🎓' if t['type']=='lesson' else '📌'} [{t['due_date']}] {t['description']}\n"
                    return reply
                return "📌 Нет активных задач!"

            elif data["type"] == "other":
                return "Я услышал тебя. Мысль зафиксирована, никаких лишних записей в базу делать не буду! 👍"

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
        except Exception as e:
            return f"❌ Ошибка базы: {e}"
    return "🤷‍♂️ Не распознано."

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer("🚀 Система полностью обновлена! Теперь ИИ идеально различает запись финансов и поиск по ним.")

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

@dp.message(F.text == "/charts")
async def get_charts(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/finance?chat_id=eq.{message.chat.id}", headers=headers)
        records = res.json()
        if not records: return await message.answer("📊 Нет финансовых данных.")
        total_income, total_expense, cat_map = 0, 0, {}
        for r in records:
            amount = r.get("amount") or 0
            if r["type"] == "income": total_income += amount
            elif r["type"] == "expense":
                total_expense += amount
                cat = r["category"] or "Разное"
                cat_map[cat] = cat_map.get(cat, 0) + amount
        net_profit = total_income - total_expense
        reply = f"📊 *ПОЛНЫЙ ФИНАНСОВЫЙ ОТЧЕТ*\n\n💰 *Всего доходов:* {int(total_income)} сум\n📉 *Всего расходов:* {int(total_expense)} сум\n🟩 *Чистая прибыль:* {int(net_profit)} сум\n\n🍕 *Детализация расходов:*\n"
        for cat, amt in sorted(cat_map.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / total_expense) * 100 if total_expense > 0 else 0
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

async def internal_reminder_scheduler():
    global LAST_EXPENSE_PROMPT_DATE
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    print("🤖 Внутренний планировщик напоминаний успешно запущен!")
    while True:
        try:
            now = get_now_tashkent()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour >= 21 and LAST_EXPENSE_PROMPT_DATE != today_str:
                try:
                    async with httpx.AsyncClient() as client:
                        res_f = await client.get(f"{SUPABASE_URL}/rest/v1/finance?select=chat_id", headers=headers)
                        if res_f.status_code == 200:
                            chat_ids = set(item["chat_id"] for item in res_f.json() if item.get("chat_id"))
                            for cid in chat_ids:
                                await bot.send_message(
                                    cid,
                                    "🔔 *Время подвести итоги дня!*\n\nFarxad, привет! Не забудь записать сегодняшние расходы, чтобы аналитика в `/charts` была точной. Просто надиктуй голосом или напиши текстом сюда 👇",
                                    parse_mode="Markdown"
                                )
                            LAST_EXPENSE_PROMPT_DATE = today_str
                except Exception as cron_err:
                    print(f"Ошибка авто-напоминания расходов: {cron_err}")

            async with httpx.AsyncClient() as client:
                res = await client.get(f"{SUPABASE_URL}/rest/v1/reminders?status=eq.pending", headers=headers)
                if res.status_code == 200:
                    reminders = res.json()
                    for r in reminders:
                        if not r.get("remind_at"): continue
                        remind_str = r["remind_at"].replace("T", " ").split(".")[0]
                        remind_time = datetime.strptime(remind_str, "%Y-%m-%d %H:%M:%S")
                        if remind_time <= now:
                            try:
                                await bot.send_message(r["chat_id"], f"⏰ *НАПОМИНАНИЕ:* \n\n{r['text']}", parse_mode="Markdown")
                                await client.patch(f"{SUPABASE_URL}/rest/v1/reminders?id=eq.{r['id']}", headers={**headers, "Content-Type": "application/json"}, json={"status": "sent"})
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
