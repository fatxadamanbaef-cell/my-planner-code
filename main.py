import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from openai import AsyncOpenAI

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = "8820240792:AAFXjs_djEYwPVCwqeOyM7kguSIBV2OdPYw"
SUPABASE_URL = "https://elcmxjlqhsluzimuvdqe.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVsY214amxxaHNsdXppbXV2ZHFlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwMjMzMzYsImV4cCI6MjA5NDU5OTMzNn0.TJQ1OGX1Wlq_hQC0DN5brBp2BCcB35KNewUy6n75VV0"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ================= ПАМЯТЬ И ТАЙМЕРЫ =================
LAST_EXPENSE_PROMPT_DATE = ""
CONVERSATION_MEMORY = {}

def get_now_tashkent():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)

def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📉 Мои расходы"), KeyboardButton(text="🪙 Мои доходы")],
        [KeyboardButton(text="🎓 CRM: Ученики"), KeyboardButton(text="📊 Финансовый отчёт")],
        [KeyboardButton(text="🔔 Напоминания")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_student_inline_keyboard(student_name: str):
    """Создает интерактивные кнопки календаря посещаемости под карточкой ученика"""
    kb = [
        [
            InlineKeyboardButton(text="✅ Проведен", callback_data=f"crm:conducted:{student_name}"),
            InlineKeyboardButton(text="❌ Прогул", callback_data=f"crm:skipped:{student_name}"),
            InlineKeyboardButton(text="⏸️ Отмена", callback_data=f"crm:canceled:{student_name}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def add_to_memory(chat_id: int, role: str, content: str):
    if chat_id not in CONVERSATION_MEMORY:
        CONVERSATION_MEMORY[chat_id] = []
    CONVERSATION_MEMORY[chat_id].append({"role": role, "content": content})
    if len(CONVERSATION_MEMORY[chat_id]) > 6:
        CONVERSATION_MEMORY[chat_id].pop(0)

# ================= СИСТЕМНЫЕ ПРОМПТЫ =================
SYSTEM_PROMPT = """
Ты — Moneyfi Super Assistant, гибрид быстрого трекера личных финансов и умной CRM для преподавателя.
Разбери запрос пользователя и верни СТРОГИЙ JSON. Никаких рассуждений вне JSON структуры!

Текущая дата: """ + get_now_tashkent().strftime("%Y-%m-%d %H:%M:%S") + """

Варианты message_type:
1. "personal_finance" — личные расходы/доходы.
2. "crm_lesson" — работа с учениками (оплата, списание урока, изменение баланса, расписание).
3. "insert_reminder" — создание напоминания.
4. "analytics_request" — пользователь задает вопрос, просит отчет, хочет узнать даты, балансы, или называет одинокое имя без команды.

Варианты action:
- "expense" / "income"
- "delete_finance" — очистить расходы за день.
- "student_payment" / "lesson_status" / "set_balance" / "set_schedule"
- "reminder" 
- "delete_reminder" — удалить напоминание.

СХЕМА ОТВЕТА JSON:
{
  "is_system_action": true/false,
  "message_type": "personal_finance" | "crm_lesson" | "insert_reminder" | "analytics_request",
  "action": "expense" | "income" | "delete_finance" | "student_payment" | "lesson_status" | "set_balance" | "set_schedule" | "reminder" | "delete_reminder" | null,
  "finance_data": { "amount": number or null, "currency": "UZS", "category": string or null, "description": string or null, "date": "YYYY-MM-DD" or null },
  "crm_data": { "student_name": string or null, "lesson_state": "conducted" | "rescheduled" | "canceled" | null, "new_datetime": null, "lessons_count": number or null, "schedule_text": string or null },
  "reminder_data": { "text": string or null, "remind_at": null },
  "chat_reply": string or null
}
"""

async def parse_via_ai(text: str, chat_id: int) -> dict:
    try:
        history = CONVERSATION_MEMORY.get(chat_id, [])
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": text}]
        response = await ai_client.chat.completions.create(model="gpt-4o-mini", messages=messages, response_format={"type": "json_object"})
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Parse error: {e}")
        return {"message_type": "analytics_request", "action": None}

async def generate_smart_reply(user_text: str, db_data: dict, chat_id: int) -> str:
    system_instruction = (
        "Ты — аналитический мозг Moneyfi Super Assistant. Отвечай экспертно, вежливо и по делу.\n"
        "ПРАВИЛО: НИКАКИХ Markdown-таблиц (символы '|' и '---' КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ). Используй только вертикальные списки.\n"
        "Если пользователь спрашивает про даты окончания уроков, возьми его остаток из базы, расписание и посчитай точные календарные даты."
    )
    try:
        history = CONVERSATION_MEMORY.get(chat_id, [])
        messages = [{"role": "system", "content": system_instruction}] + history + [{"role": "user", "content": f"Сырые данные БД:\n{json.dumps(db_data, ensure_ascii=False)}\n\nЗапрос пользователя: {user_text}"}]
        response = await ai_client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        return response.choices[0].message.content
    except Exception as e:
        return f"Ошибка генерации ответа: {e}"

# ================= ОБРАБОТЧИК БАЗЫ ДАННЫХ =================
async def handle_moneyfi_action(data: dict, chat_id: int, original_text: str) -> str:
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    m_type = data.get("message_type")
    action = data.get("action")
    fdata = data.get("finance_data") or {}
    cdata = data.get("crm_data") or {}
    rdata = data.get("reminder_data") or {}
    
    st_name = cdata.get("student_name").strip().lower().capitalize() if cdata.get("student_name") else None

    async with httpx.AsyncClient() as client:
        try:
            if action == "delete_finance":
                target_date = fdata.get("date") or get_now_tashkent().strftime("%Y-%m-%d")
                await client.delete(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{chat_id}&date=eq.{target_date}", headers=headers)
                return f"🧹 *База очищена!* Финансовые записи за *{target_date}* удалены."

            elif action == "delete_reminder":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{chat_id}&status=eq.pending", headers=headers)
                reminders = res.json() if res.status_code == 200 else []
                if not reminders: return "🔔 У тебя сейчас нет активных напоминаний."
                history = CONVERSATION_MEMORY.get(chat_id, [])
                llm_messages = [{"role": "system", "content": "Верни JSON со списком ID строк для удаления. Формат: {\"ids\": [1, 2]}"}] + history + [{"role": "user", "content": f"База: {json.dumps(reminders, ensure_ascii=False)}\nКоманда: {original_text}"}]
                llm_res = await ai_client.chat.completions.create(model="gpt-4o-mini", messages=llm_messages, response_format={"type": "json_object"})
                target_ids = json.loads(llm_res.choices[0].message.content).get("ids", [])
                for rid in target_ids: await client.delete(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?id=eq.{rid}", headers=headers)
                return f"🗑️ Успешно удалено из базы напоминаний: {len(target_ids)} шт."

            elif m_type == "insert_reminder":
                payload = {"chat_id": chat_id, "text": rdata.get("text"), "remind_at": rdata.get("remind_at"), "status": "pending"}
                await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders", headers=headers, json=payload)
                return f"🔔 *Напоминание сохранено:* \"{payload['text']}\" на {payload['remind_at']}"

            elif m_type == "personal_finance":
                if fdata.get("amount") is None: return "❌ Ошибка: не указана сумма транзакции."
                payload = {"chat_id": chat_id, "action": action, "amount": int(fdata.get("amount")), "currency": "UZS", "category": fdata.get("category"), "description": fdata.get("description"), "date": get_now_tashkent().strftime("%Y-%m-%d")}
                await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_finance", headers=headers, json=payload)
                return f"💰 *Записано:* {payload['amount']} UZS -> {payload['category']}"

            elif m_type == "crm_lesson":
                if not st_name: return "⚠️ Уточните, пожалуйста, имя ученика."

                if action == "set_schedule":
                    sched_info = cdata.get("schedule_text") or original_text
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json(): await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"schedule": sched_info})
                    else: await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": 0, "schedule": sched_info})
                    return f"📅 Расписание для *{st_name}* установлено: `{sched_info}`"

                elif action == "student_payment":
                    if cdata.get("lessons_count") is None or fdata.get("amount") is None: return "❌ Укажите сумму оплаты и количество уроков пакета."
                    count, amount_val = int(cdata.get("lessons_count")), int(fdata.get("amount"))
                    await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_finance", headers=headers, json={"chat_id": chat_id, "action": "income", "amount": amount_val, "currency": "UZS", "category": "Уроки", "description": f"Оплата обучения: {st_name}", "date": get_now_tashkent().strftime("%Y-%m-%d")})
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json(): await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": st_res.json()[0]["balance_lessons"] + count})
                    else: await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": count})
                    return f"🎓 Оплата от *{st_name}* принята. Начислено +{count} уроков."

                elif action == "lesson_status":
                    l_state = cdata.get("lesson_state") or "conducted"
                    await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_crm_lessons", headers=headers, json={"chat_id": chat_id, "student_name": st_name, "lesson_state": l_state})
                    if l_state in ["conducted", "skipped"]:
                        st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                        if st_res.json():
                            new_bal = max(0, st_res.json()[0]["balance_lessons"] - 1)
                            await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                            return f"📉 *CRM:* Урок у *{st_name}* зафиксирован ({'Проведен' if l_state=='conducted' else 'Прогул'}). Списан 1 урок. Остаток: {new_bal} уроков."
                    return f"📅 Статус урока *{st_name}* изменен на Отмену."

                elif action == "set_balance":
                    count = cdata.get("lessons_count")
                    if count is None: return "❌ Укажите точную цифру для нового баланса."
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json(): await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": int(count)})
                    else: await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": int(count)})
                    return f"🔧 Баланс ученика *{st_name}* установлен на *{count}* уроков."

        except Exception as e:
            return f"❌ Ошибка Moneyfi-модуля: {e}"
    return "🤷‍♂️ Операция не распознана."

# ================= ОБРАБОТЧИК КНОПОК КАЛЕНДАРЯ ПОСЕЩАЕМОСТИ =================
@dp.callback_query(F.data.startswith("crm:"))
async def handle_crm_callback(callback: CallbackQuery):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    _, state, student_name = callback.data.split(":")
    chat_id = callback.message.chat.id
    
    states_ru = {"conducted": "Проведен ✅", "skipped": "Прогул (списание) ❌", "canceled": "Отмена (уважительная) ⏸️"}
    await callback.answer(f"Фиксирую: {states_ru[state]}")
    
    async with httpx.AsyncClient() as client:
        await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_crm_lessons", headers=headers, json={"chat_id": chat_id, "student_name": student_name, "lesson_state": state})
        
        if state in ["conducted", "skipped"]:
            st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{student_name}", headers=headers)
            if st_res.json():
                old_bal = st_res.json()[0]["balance_lessons"]
                old_sched = st_res.json()[0].get("schedule", "Не задано")
                new_bal = max(0, old_bal - 1)
                await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                
                alert = "\n⚠️ *БАЛАНС НА НУЛЕ! Нужно обновить оплату.*" if new_bal == 0 else ""
                updated_text = (
                    f"👤 **Профиль ученика**\n\n"
                    f"🏷 Имя: {student_name}\n"
                    f"📉 Остаток занятий: {new_bal} уроков {alert}\n"
                    f"📅 Расписание: {old_sched}\n\n"
                    f"⚡️ *Статус изменен:* Урок успешно отмечен как **{states_ru[state]}**!"
                )
                await callback.message.edit_text(updated_text, reply_markup=get_student_inline_keyboard(student_name), parse_mode="Markdown")
                return
        
        st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{student_name}", headers=headers)
        curr_bal = st_res.json()[0]["balance_lessons"] if st_res.json() else 0
        curr_sched = st_res.json()[0].get("schedule", "Не задано") if st_res.json() else "Не задано"
        
        updated_text = (
            f"👤 **Профиль ученика**\n\n"
            f"🏷 Имя: {student_name}\n"
            f"📉 Остаток занятий: {curr_bal} уроков\n"
            f"📅 Расписание: {curr_sched}\n\n"
            f"⚡️ *Статус изменен:* Урок отмечен как **{states_ru[state]}** (баланс сохранен)."
        )
        await callback.message.edit_text(updated_text, reply_markup=get_student_inline_keyboard(student_name), parse_mode="Markdown")

# ================= МАРШРУТИЗАЦИЯ И ИНТЕРФЕЙС =================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    CONVERSATION_MEMORY[message.chat.id] = []
    await message.answer("Привет, Farxad! 🚀 \nЯ — **Moneyfi Super Assistant**.\nИнтерактивный календарь-пульт для уроков и трекер финансов запущены.", reply_markup=get_main_keyboard())

async def process_analytics(message: Message, query_text: str):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res_f = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}", headers=headers)
        res_s = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{message.chat.id}", headers=headers)
        res_r = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{message.chat.id}&status=eq.pending", headers=headers)
        return {"finance": res_f.json() if res_f.status_code==200 else [], "students": res_s.json() if res_s.status_code==200 else [], "reminders": res_r.json() if res_r.status_code==200 else []}

@dp.message(F.text == "📉 Мои расходы")
async def btn_expenses(message: Message):
    await message.answer("🔄 Загружаю расходы...")
    db_data = await process_analytics(message, message.text)
    reply = await generate_smart_reply("Покажи список моих расходов и посчитай итог построчно.", db_data, message.chat.id)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🪙 Мои доходы")
async def btn_incomes(message: Message):
    await message.answer("🔄 Загружаю доходы...")
    db_data = await process_analytics(message, message.text)
    reply = await generate_smart_reply("Покажи список моих доходов построчно.", db_data, message.chat.id)
    await message.answer(reply, parse_mode="Markdown")

# ИНТЕРАКТИВНЫЙ ВЫВОД КАРТОЧЕК УЧЕНИКОВ С КНОПКАМИ ПОСЕЩАЕМОСТИ
@dp.message(F.text == "🎓 CRM: Ученики")
async def btn_crm(message: Message):
    await message.answer("🔄 Загружаю интерактивный календарь студентов...")
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{message.chat.id}", headers=headers)
        students = res.json() if res.status_code == 200 else []
        if not students: return await message.answer("🎓 Учеников в базе данных пока нет. Надиктуйте: 'Добавь ученика Давида, баланс 4 урока'.")
        for st in students:
            card_text = f"👤 **Профиль ученика**\n\n🏷 Имя: {st['name']}\n📉 Остаток занятий: {st['balance_lessons']} уроков\n📅 Расписание: {st.get('schedule', 'Не задано')}\n\n📝 *Отметить посещаемость текущего урока:* "
            await message.answer(card_text, reply_markup=get_student_inline_keyboard(st['name']), parse_mode="Markdown")

@dp.message(F.text == "🔔 Напоминания")
async def btn_reminders(message: Message):
    db_data = await process_analytics(message, message.text)
    reply = await generate_smart_reply("Покажи список актуальных напоминаний.", db_data, message.chat.id)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "📊 Финансовый отчёт")
async def btn_report(message: Message):
    await message.answer("🔄 Генерирую полный отчёт...")
    db_data = await process_analytics(message, message.text)
    reply = await generate_smart_reply("Сделай полный финансовый отчет: доходы, расходы, чистая прибыль.", db_data, message.chat.id)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(message: Message):
    ai_data = await parse_via_ai(message.text, message.chat.id)
    if ai_data.get("message_type") == "analytics_request" or ai_data.get("action") is None:
        db_data = await process_analytics(message, message.text)
        reply = await generate_smart_reply(message.text, db_data, message.chat.id)
        add_to_memory(message.chat.id, "user", message.text)
        add_to_memory(message.chat.id, "assistant", reply)
        return await message.answer(reply, parse_mode="Markdown")
    
    reply = await handle_moneyfi_action(ai_data, message.chat.id, message.text)
    add_to_memory(message.chat.id, "user", message.text)
    add_to_memory(message.chat.id, "assistant", reply)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.voice)
async def handle_voice(message: Message):
    local_file = f"voice_{message.message_id}.ogg"
    file = await bot.get_file(message.voice.file_id)
    await bot.download_file(file.file_path, local_file)
    try:
        with open(local_file, "rb") as f:
            transcription = await ai_client.audio.transcriptions.create(model="whisper-1", file=f)
        await message.answer(f"🗣 *Распознано:* {transcription.text}", parse_mode="Markdown")
        
        ai_data = await parse_via_ai(transcription.text, message.chat.id)
        if ai_data.get("message_type") == "analytics_request" or ai_data.get("action") is None:
            db_data = await process_analytics(message, transcription.text)
            reply = await generate_smart_reply(transcription.text, db_data, message.chat.id)
            add_to_memory(message.chat.id, "user", transcription.text)
            add_to_memory(message.chat.id, "assistant", reply)
            return await message.answer(reply, parse_mode="Markdown")

        reply = await handle_moneyfi_action(ai_data, message.chat.id, transcription.text)
        add_to_memory(message.chat.id, "user", transcription.text)
        add_to_memory(message.chat.id, "assistant", reply)
        await message.answer(reply, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка голоса: {e}")
    finally:
        if os.path.exists(local_file): os.remove(local_file)

# ================= ВЕБХУКИ И ФОНОВЫЕ ЗАДАЧИ =================
async def handle_telegram_webhook(request):
    try:
        bot_dict = json.loads(await request.text())
        update = Update.model_validate(bot_dict, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e: print(f"Webhook error: {e}")
    return web.Response(text="OK")

async def handle_keepalive_ping(request): return web.Response(text="I am awake!")

# ================= ФОНОВЫЙ ПЛАНОВИК ДЕДЛАЙНОВ =================
async def internal_reminder_scheduler():
    global LAST_EXPENSE_PROMPT_DATE
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    while True:
        try:
            now = get_now_tashkent()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour >= 21 and LAST_EXPENSE_PROMPT_DATE != today_str:
                try:
                    async with httpx.AsyncClient() as client:
                        res_f = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?select=chat_id", headers=headers)
                        if res_f.status_code == 200:
                            for cid in set(item["chat_id"] for item in res_f.json() if item.get("chat_id")):
                                await bot.send_message(cid, "🔔 *Итоги дня!*\nFarxad, не забудь надиктовать расходы за сегодня.", parse_mode="Markdown")
                            LAST_EXPENSE_PROMPT_DATE = today_str
                except Exception as e: print(f"Cron error: {e}")

            async with httpx.AsyncClient() as client:
                res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?status=eq.pending", headers=headers)
                if res.status_code == 200:
                    for r in res.json():
                        if not r.get("remind_at"): continue
                        remind_time = datetime.strptime(r["remind_at"].replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                        if remind_time <= now:
                            try:
                                await bot.send_message(r["chat_id"], f"⏰ *НАПОМИНАНИЕ:* \n\n{r['text']}", parse_mode="Markdown")
                                await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?id=eq.{r['id']}", headers={**headers, "Content-Type": "application/json"}, json={"status": "sent"})
                            except Exception as e: print(f"Reminder dispatch error: {e}")
        except Exception as e: print(f"Loop error: {e}")
        await asyncio.sleep(60)

async def on_startup_service(app):
    await bot.set_webhook(f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook")
    asyncio.create_task(internal_reminder_scheduler())

def main():
    app = web.Application()
    app.router.add_post('/webhook', handle_telegram_webhook)
    app.router.add_get('/cron', handle_keepalive_ping)
    app.on_startup.append(on_startup_service)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    main()
