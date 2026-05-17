import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, ReplyKeyboardMarkup, KeyboardButton
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

def get_main_keyboard():
    """Создает удобную постоянную клавиатуру пульта управления"""
    kb = [
        [KeyboardButton(text="📉 Показать расходы"), KeyboardButton(text="🪙 Показать доходы")],
        [KeyboardButton(text="🎓 Баланс учеников"), KeyboardButton(text="📊 Полный отчёт")],
        [KeyboardButton(text="📅 Расписание"), KeyboardButton(text="🔔 Напоминания")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

SYSTEM_PROMPT = """
Ты — первый этап ультимативного ИИ-ассистента. Твоя задача — строго определить намерение пользователя и извлечь параметры в JSON.

Варианты намерения (intent):
- 'insert_expense' / 'insert_income' (Запись нового расхода или дохода)
- 'insert_task' / 'insert_lesson' (Добавление нового дела или урока в календарь)
- 'insert_note' (Сохранение новой мысли/заметки в блокнот)
- 'insert_reminder' (Установка напоминания на точное время)
- 'insert_student_pay' / 'insert_student_lesson' / 'set_student_balance' (Управление балансом студентов)
- 'complete_task' (Отметить задачу как выполненную)
- 'delete_finance' (Очистить или удалить расходы/доходы за день)

- 'read_finance' (Просмотр истории денег, расходов, доходов)
- 'read_tasks' / 'read_notes' / 'read_students' / 'read_reminders' (Просмотр списков данных)
- 'pure_chat' (Простые фразы, вежливость, не связанные с базой)

Формат ответа JSON:
{
  "intent": "выбранный_интент",
  "params": {
    "amount": число_или_null,
    "category": "категория_или_null",
    "description": "суть действия",
    "date": "ГГГГ-ММ-ДД",
    "remind_at": "ГГГГ-ММ-ДД ВВ:ММ:СС или null",
    "student_name": "Имя Ученика или null",
    "count": число_уроков_или_null
  },
  "chat_reply": "текст_ответа_только_для_pure_chat_или_null"
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
        return {"intent": "pure_chat", "chat_reply": "Ой, я немного запутался. Повтори, пожалуйста."}

async def generate_smart_reply(user_text: str, db_data: list) -> str:
    try:
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты — ИИ-ассистент топ-преподавателя математики из Ташкента. Проанализируй сырые данные из базы Supabase и развернуто ответь на вопрос пользователя. Считай суммы, группируй информацию, делай экспертные выводы. Используй красивую структуру, эмодзи и Markdown."},
                {"role": "user", "content": f"Вопрос: \"{user_text}\"\n\nДанные из базы:\n{json.dumps(db_data, ensure_ascii=False, indent=2)}"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ Ошибка генерации умного ответа: {e}"

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
                        await client.patch(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{item['id']}", headers={**headers, "Content-Type": "application/json"}, json={"status": "Пропущено"})
                        alert_text += f"\n\n⚠️ *ПРОПУЩЕНО:* {item['description']}"
    except Exception as e:
        print(f"Ошибка дедлайнов: {e}")
    return alert_text

async def process_intent(ai_data: dict, chat_id: int, original_text: str) -> str:
    intent = ai_data.get("intent", "pure_chat")
    params = ai_data.get("params", {})
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    
    raw_name = params.get("student_name")
    student_name = raw_name.strip().lower().capitalize() if raw_name else None

    async with httpx.AsyncClient() as client:
        try:
            if intent == "delete_finance":
                target_date = params.get("date") or get_now_tashkent().strftime("%Y-%m-%d")
                url = f"{SUPABASE_URL}/rest/v1/finance?chat_id=eq.{chat_id}&date=eq.{target_date}"
                res = await client.delete(url, headers=headers)
                return f"🧹 *База очищена!* Все финансовые записи за *{target_date}* удалены." if res.status_code in [200, 204] else "❌ Не удалось очистить."

            elif intent == "read_finance":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/finance?chat_id=eq.{chat_id}", headers=headers)
                return await generate_smart_reply(original_text, res.json() if res.status_code == 200 else [])

            elif intent == "read_tasks":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=eq.Новая", headers=headers)
                return await generate_smart_reply(original_text, res.json() if res.status_code == 200 else [])

            elif intent == "read_notes":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/notes?chat_id=eq.{chat_id}", headers=headers)
                return await generate_smart_reply(original_text, res.json() if res.status_code == 200 else [])

            elif intent == "read_students":
                url = f"{SUPABASE_URL}/rest/v1/students?chat_id=eq.{chat_id}"
                if student_name: url += f"&name=ilike.*{student_name}*"
                res = await client.get(url, headers=headers)
                return await generate_smart_reply(original_text, res.json() if res.status_code == 200 else [])

            elif intent == "read_reminders":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/reminders?chat_id=eq.{chat_id}&status=eq.pending", headers=headers)
                return await generate_smart_reply(original_text, res.json() if res.status_code == 200 else [])

            elif intent == "pure_chat":
                return ai_data.get("chat_reply", "На связи! 😊")

            elif intent in ["insert_expense", "insert_income"]:
                payload = {"chat_id": chat_id, "type": "expense" if intent == "insert_expense" else "income", "amount": params.get("amount"), "category": params.get("category", "Разное"), "description": params.get("description"), "date": params.get("date") or get_now_tashkent().strftime("%Y-%m-%d")}
                await client.post(f"{SUPABASE_URL}/rest/v1/finance", headers=headers, json=payload)
                return f"💰 Сохранено: {params.get('amount')} сум — {params.get('description')}"
            
            elif intent in ["insert_task", "insert_lesson"]:
                payload = {"chat_id": chat_id, "type": "task" if intent == "insert_task" else "lesson", "description": params.get("description"), "due_date": params.get("date") or get_now_tashkent().strftime("%Y-%m-%d"), "status": "Новая"}
                await client.post(f"{SUPABASE_URL}/rest/v1/tasks", headers=headers, json=payload)
                return f"📅 Добавлено на {payload['due_date']}: {params.get('description')}"
            
            elif intent == "insert_note":
                payload = {"chat_id": chat_id, "content": params.get("description"), "tags": params.get("category", "Общее")}
                await client.post(f"{SUPABASE_URL}/rest/v1/notes", headers=headers, json=payload)
                return f"🧠 Заметка сохранена: \"{params.get('description')}\""

            elif intent == "insert_reminder":
                payload = {"chat_id": chat_id, "text": params.get("description"), "remind_at": params.get("remind_at"), "status": "pending"}
                await client.post(f"{SUPABASE_URL}/rest/v1/reminders", headers=headers, json=payload)
                return f"🔔 Напомню в {params.get('remind_at')} про: \"{params.get('description')}\""

            elif intent == "insert_student_pay":
                count = params.get("count", 1) or 1
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{student_name}", headers=headers)
                students = st_res.json()
                if students:
                    new_bal = students[0]["balance_lessons"] + count
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                else:
                    await client.post(f"{SUPABASE_URL}/rest/v1/students", headers=headers, json={"chat_id": chat_id, "name": student_name, "balance_lessons": count})
                return f"🎓 Добавлено +{count} уроков для {student_name}."

            elif intent == "insert_student_lesson":
                count = params.get("count", 1) or 1
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{student_name}", headers=headers)
                students = st_res.json()
                if students:
                    new_bal = max(0, students[0]["balance_lessons"] - count)
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                    return f"📉 Списано {count} урок(ов) у {student_name}. Остаток: {new_bal} уроков."
                return f"❌ Ученик {student_name} не найден."

            elif intent == "set_student_balance":
                count = params.get("count", 0)
                st_res = await client.get(f"{SUPABASE_URL}/rest/v1/students?name=eq.{student_name}", headers=headers)
                students = st_res.json()
                if students:
                    await client.patch(f"{SUPABASE_URL}/rest/v1/students?id=eq.{students[0]['id']}", headers=headers, json={"balance_lessons": count})
                else:
                    await client.post(f"{SUPABASE_URL}/rest/v1/students", headers=headers, json={"chat_id": chat_id, "name": student_name, "balance_lessons": count})
                return f"🔧 Баланс {student_name} установлен на {count} уроков."

            elif intent == "complete_task":
                keyword = params.get("description", "")
                res = await client.patch(f"{SUPABASE_URL}/rest/v1/tasks?chat_id=eq.{chat_id}&status=neq.Выполнено&description=ilike.*{keyword}*", headers=headers, json={"status": "Выполнено"})
                return f"✅ Задача \"{keyword}\" выполнена!" if res.status_code in [200, 204] else f"❌ Задача не найдена."

        except Exception as e:
            return f"❌ Ошибка базы: {e}"
    return "🤷‍♂️ Не распознано."

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "🚀 Пульт управления активирован!\n\nИспользуй удобные кнопки главного меню ниже, чтобы мгновенно просматривать отчёты и балансы 👇",
        reply_markup=get_main_keyboard()
    )

# ========================================================
# УМНЫЕ ОБРАБОТЧИКИ НАЖАТИЙ НА КНОПКИ (БЕЗ ЛИШНЕГО ПАРСИНГА)
# ========================================================
@dp.message(F.text == "📉 Показать расходы")
async def btn_expenses(message: Message):
    await message.answer("🔄 Анализирую расходы...")
    ai_data = {"intent": "read_finance", "params": {"description": "expense"}}
    reply = await process_intent(ai_data, message.chat.id, message.text)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🪙 Показать доходы")
async def btn_incomes(message: Message):
    await message.answer("🔄 Анализирую доходы...")
    ai_data = {"intent": "read_finance", "params": {"description": "income"}}
    reply = await process_intent(ai_data, message.chat.id, message.text)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🎓 Баланс учеников")
async def btn_balances(message: Message):
    await message.answer("🔄 Проверяю остатки занятий...")
    ai_data = {"intent": "read_students", "params": {}}
    reply = await process_intent(ai_data, message.chat.id, "Покажи баланс всех моих учеников и напомни, когда были последние изменения")
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🔔 Напоминания")
async def btn_reminders(message: Message):
    await message.answer("🔄 Загружаю активные напоминания...")
    ai_data = {"intent": "read_reminders", "params": {}}
    reply = await process_intent(ai_data, message.chat.id, message.text)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "📅 Расписание")
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

@dp.message(F.text == "📊 Полный отчёт")
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
    await message.answer("🔄 Анализирую и сопоставляю с базой данных...")
    ai_data = await parse_via_ai(message.text)
    reply = await process_intent(ai_data, message.chat.id, message.text)
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
        reply = await process_intent(ai_data, message.chat.id, transcription.text)
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
