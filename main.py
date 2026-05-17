import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, ReplyKeyboardMarkup, KeyboardButton
from openai import AsyncOpenAI

# Конфигурация и Ключи (Остаются неизменными)
TELEGRAM_TOKEN = "8820240792:AAFXjs_djEYwPVCwqeOyM7kguSIBV2OdPYw"
SUPABASE_URL = "https://elcmxjlqhsluzimuvdqe.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVsY214amxxaHNsdXppbXV2ZHFlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwMjMzMzYsImV4cCI6MjA5NDU5OTMzNn0.TJQ1OGX1Wlq_hQC0DN5brBp2BCcB35KNewUy6n75VV0"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

LAST_EXPENSE_PROMPT_DATE = ""
CONVERSATION_MEMORY = {}

def get_now_tashkent():
    """Возвращает точное время в Ташкенте (UTC+5)"""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)

def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📉 Мои расходы"), KeyboardButton(text="🪙 Мои доходы")],
        [KeyboardButton(text="🎓 CRM: Ученики"), KeyboardButton(text="📊 Финансовый отчёт")],
        [KeyboardButton(text="🔔 Напоминания")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def add_to_memory(chat_id: int, role: str, content: str):
    if chat_id not in CONVERSATION_MEMORY:
        CONVERSATION_MEMORY[chat_id] = []
    CONVERSATION_MEMORY[chat_id].append({"role": role, "content": content})
    if len(CONVERSATION_MEMORY[chat_id]) > 6:
        CONVERSATION_MEMORY[chat_id].pop(0)

# ОБНОВЛЕННЫЙ ПРОМПТ: ДОБАВЛЕНА ИНСТРУКЦИЯ ДЛЯ УДАЛЕНИЯ НАПОМИНАНИЙ
SYSTEM_PROMPT = """
Ты — Moneyfi Super Assistant, гибрид быстрого трекера личных финансов и умной CRM для преподавателя математики.
Разбери запрос и верни СТРОГИЙ JSON. Никакого лишнего текста вне JSON структуры!

Текущая дата: 18 мая 2026 года (Понедельник).
Текущее время (Ташкент): """ + get_now_tashkent().strftime("%H:%M:%S") + """

Варианты message_type:
1. "personal_finance" — личные расходы или доходы.
2. "crm_lesson" — действия с учениками (оплата обучения, статус урока, изменение баланса).
3. "insert_reminder" — пользователь просит НАПОМНИТЬ о чем-то в конкретное время.
4. "analytics_request" — пользователь хочет посмотреть списки, отчеты, задает вопросы.

Варианты action:
- "expense" / "income" — для финансов.
- "student_payment" / "lesson_status" / "set_balance" / "set_schedule" — для CRM.
- "reminder" — для записи нового напоминания.
- "delete_reminder" — СТРОГО если пользователь просит УДАЛИТЬ, СТЕРЕТЬ, УБРАТЬ или ОТМЕНИТЬ существующее напоминание (например: "удали напоминание 1 про Сахиба", "удали напоминание про долг Шерзоду").

ПРАВИЛО ДЛЯ DELETE_REMINDER: Если пользователь указывает номер напоминания (например, "удали напоминание 1"), внимательно посмотри в историю сообщений (CONVERSATION_MEMORY), определи, какое именно напоминание шло под этим номером в последнем выданном списке, и запиши его ключевое слово или имя ученика в поле `reminder_data.text`. Если указано имя ("удали про Сахиба"), запиши это имя в `reminder_data.text`.

СХЕМА ОТВЕТА JSON:
{
  "is_system_action": true/false,
  "message_type": "personal_finance" | "crm_lesson" | "insert_reminder" | "analytics_request",
  "action": "expense" | "income" | "lesson_status" | "student_payment" | "set_balance" | "set_schedule" | "reminder" | "delete_reminder" | null,
  "finance_data": { "amount": number or null, "currency": "UZS", "category": string or null, "description": string or null },
  "crm_data": { "student_name": string or null, "lesson_state": "conducted" | "rescheduled" | "canceled" | "scheduled" | null, "new_datetime": string or null, "lessons_count": number or null, "schedule_text": string or null },
  "reminder_data": {
    "text": string or null, // Ключевое слово или текст для поиска и удаления / записи
    "remind_at": "YYYY-MM-DD HH:MM:SS" or null
  },
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
        print(f"Ошибка ИИ: {e}")
        return {"message_type": "analytics_request", "is_system_action": False, "chat_reply": "Ошибка разбора фразы."}

async def generate_smart_reply(user_text: str, db_data: list, chat_id: int) -> str:
    system_instruction = (
        "Ты — аналитический модуль Moneyfi Super Assistant. Перед тобой сырые данные из базы Supabase.\n"
        "Сгруппируй информацию ВЕРТИКАЛЬНЫМ СПИСКОМ БЕЗ ТАБЛИЦ (символы '|' и '---' КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ)! "
        "Используй эмодзи и Markdown. Форматируй строки лаконично, чтобы они легко читались на экране телефона.\n"
        "Если пользователь спрашивает, почему что-то не удалилось или не произошло, внимательно изучи структуру данных и дай честный ответ."
    )
    try:
        history = CONVERSATION_MEMORY.get(chat_id, [])
        messages = [{"role": "system", "content": system_instruction}] + history + [{"role": "user", "content": f"Данные из базы:\n{json.dumps(db_data, ensure_ascii=False)}\n\nВопрос: {user_text}"}]
        response = await ai_client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        return response.choices[0].message.content
    except Exception as e:
        return f"Ошибка синтеза ответа: {e}"

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
            # === ОПЕРАЦИЯ ФИЗИЧЕСКОГО УДАЛЕНИЯ НАПОМИНАНИЯ ИЗ БАЗЫ ===
            if action == "delete_reminder":
                keyword = rdata.get("text") or original_text
                # Корректируем частые нестыковки имён Шерзод/Шерзот
                if "шерзот" in keyword.lower(): keyword = "Шерзод"
                elif "шерзод" in keyword.lower(): keyword = "Шерзод"
                elif "сахиб" in keyword.lower(): keyword = "Сахиб"
                
                res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{chat_id}&status=eq.pending", headers=headers)
                if res.status_code == 200 and res.json():
                    reminders = res.json()
                    target_id = None
                    matched_text = ""
                    for r in reminders:
                        if keyword.lower() in r["text"].lower() or (st_name and st_name.lower() in r["text"].lower()):
                            target_id = r["id"]
                            matched_text = r["text"]
                            break
                    if target_id:
                        await client.delete(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?id=eq.{target_id}", headers=headers)
                        return f"🗑️ *CRM:* Напоминание «_{matched_text}_» успешно удалено из базы данных Supabase!"
                    else:
                        return f"🔍 Активное напоминание с ключевым словом *{keyword}* не найдено в списке ожидающих."
                return "🔔 У тебя сейчас нет активных напоминаний."

            if m_type == "insert_reminder":
                payload = {"chat_id": chat_id, "text": rdata.get("text"), "remind_at": rdata.get("remind_at"), "status": "pending"}
                await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders", headers=headers, json=payload)
                return f"🔔 *Напоминание зафиксировано!* \n📅 Время: {payload['remind_at']}\n🎯 Суть: \"{payload['text']}\""

            if m_type == "personal_finance":
                amount_val = fdata.get("amount")
                if not amount_val: return "❌ Операция отклонена: не указана сумма."
                payload = {"chat_id": chat_id, "action": action, "amount": amount_val, "currency": fdata.get("currency", "UZS"), "category": fdata.get("category"), "description": fdata.get("description"), "date": get_now_tashkent().strftime("%Y-%m-%d")}
                await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_finance", headers=headers, json=payload)
                return f"💰 *Записано:* {int(amount_val)} UZS -> {payload['category']}"

            if m_type == "crm_lesson":
                if not st_name: return "⚠️ Уточните, пожалуйста, имя ученика?"

                if action == "set_schedule":
                    sched_info = cdata.get("schedule_text") or original_text
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json():
                        await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"schedule": sched_info})
                    else:
                        await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": 0, "schedule": sched_info})
                    return f"📅 *CRM:* Расписание ученика *{st_name}* сохранено: `{sched_info}`"

                elif action == "student_payment":
                    amount_val = fdata.get("amount")
                    count = cdata.get("lessons_count")
                    if not count: return "❌ Укажите количество уроков пакета."
                    f_payload = {"chat_id": chat_id, "action": "income", "amount": amount_val, "currency": "UZS", "category": "Уроки", "description": f"Оплата обучения: {st_name}", "date": get_now_tashkent().strftime("%Y-%m-%d")}
                    await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_finance", headers=headers, json=f_payload)
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json():
                        new_bal = st_res.json()[0]["balance_lessons"] + count
                        await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                    else:
                        await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": count})
                    return f"🎓 *CRM:* Оплата от *{st_name}* принята. Начислено +{count} уроков."

                elif action == "lesson_status":
                    l_payload = {"chat_id": chat_id, "student_name": st_name, "lesson_state": cdata.get("lesson_state"), "new_datetime": cdata.get("new_datetime")}
                    await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_crm_lessons", headers=headers, json=l_payload)
                    if l_payload["lesson_state"] == "conducted":
                        st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                        if st_res.json():
                            new_bal = max(0, st_res.json()[0]["balance_lessons"] - 1)
                            await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                            return f"📉 *CRM:* Урок у *{st_name}* списан. Остаток: {new_bal} уроков."
                    return f"📅 *CRM:* Статус урока *{st_name}* изменен."

                elif action == "set_balance":
                    count = cdata.get("lessons_count")
                    if count is None:
                        st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{chat_id}&name=eq.{st_name}", headers=headers)
                        return await generate_smart_reply(original_text, {"students": st_res.json() if st_res.status_code==200 else []}, chat_id)
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json():
                        await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": count})
                    else:
                        await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": count})
                    return f"🔧 *CRM:* Баланс ученика *{st_name}* изменен на число *{count}* уроков."

        except Exception as e:
            return f"❌ Ошибка Moneyfi-модуля: {e}"
    return "🤷‍♂️ Не распознано."

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    CONVERSATION_MEMORY[message.chat.id] = []
    await message.answer("Привет, Farxad! 🚀 \nЯ — **Moneyfi Super Assistant**. Логика удаления напоминаний полностью исправлена.", reply_markup=get_main_keyboard())

@dp.message(F.text == "📉 Мои расходы")
async def btn_expenses(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}&action=eq.expense", headers=headers)
        reply = await generate_smart_reply(message.text, res.json() if res.status_code == 200 else [], message.chat.id)
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🪙 Мои доходы")
async def btn_incomes(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}&action=eq.income", headers=headers)
        reply = await generate_smart_reply(message.text, res.json() if res.status_code == 200 else [], message.chat.id)
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🎓 CRM: Ученики")
async def btn_crm(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{message.chat.id}", headers=headers)
        reply = await generate_smart_reply(message.text, res.json() if res.status_code == 200 else [], message.chat.id)
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "🔔 Напоминания")
async def btn_reminders(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{message.chat.id}&status=eq.pending", headers=headers)
        reply = await generate_smart_reply(message.text, res.json() if res.status_code == 200 else [], message.chat.id)
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "📊 Финансовый отчёт")
async def btn_report(message: Message):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}", headers=headers)
        reply = await generate_smart_reply("Сделай детальный анализ доходов и чистой прибыли", res.json() if res.status_code == 200 else [], message.chat.id)
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(message: Message):
    ai_data = await parse_via_ai(message.text, message.chat.id)
    
    # ЕСЛИ ЭТО ЗАПРОС ИСТОРИИ ИЛИ АНАЛИТИКИ
    if ai_data.get("message_type") == "analytics_request" or ai_data.get("action") is None:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}", headers=headers)
            res_stud = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{message.chat.id}", headers=headers)
            res_rem = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{message.chat.id}&status=eq.pending", headers=headers)
            combined_data = {
                "finance": res.json() if res.status_code==200 else [],
                "students": res_stud.json() if res_stud.status_code==200 else [],
                "reminders": res_rem.json() if res_rem.status_code==200 else []
            }
            reply = await generate_smart_reply(message.text, combined_data, message.chat.id)
            add_to_memory(message.chat.id, "user", message.text)
            add_to_memory(message.chat.id, "assistant", reply)
            return await message.answer(reply, parse_mode="Markdown")
            
    reply = await handle_moneyfi_action(ai_data, message.chat.id, message.text)
    add_to_memory(message.chat.id, "user", message.text)
    add_to_memory(message.chat.id, "assistant", reply)
    await message.answer(reply, parse_mode="Markdown")

@dp.message(F.voice)
async def handle_voice(message: Message):
    file = await bot.get_file(message.voice.file_id)
    local_file = "voice.ogg"
    await bot.download_file(file.file_path, local_file)
    try:
        with open(local_file, "rb") as f:
            transcription = await ai_client.audio.transcriptions.create(model="whisper-1", file=f)
        await message.answer(f"🗣 *Вы сказали:* {transcription.text}", parse_mode="Markdown")
        
        ai_data = await parse_via_ai(transcription.text, message.chat.id)
        
        if ai_data.get("message_type") == "analytics_request" or ai_data.get("action") is None:
            headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}", headers=headers)
                res_stud = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{message.chat.id}", headers=headers)
                res_rem = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{message.chat.id}&status=eq.pending", headers=headers)
                combined_data = {
                    "finance": res.json() if res.status_code==200 else [],
                    "students": res_stud.json() if res_stud.status_code==200 else [],
                    "reminders": res_rem.json() if res_rem.status_code==200 else []
                }
                reply = await generate_smart_reply(transcription.text, combined_data, message.chat.id)
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
    while True:
        try:
            now = get_now_tashkent()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour >= 21 and LAST_EXPENSE_PROMPT_DATE != today_str:
                try:
                    async with httpx.AsyncClient() as client:
                        res_f = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?select=chat_id", headers=headers)
                        if res_f.status_code == 200:
                            chat_ids = set(item["chat_id"] for item in res_f.json() if item.get("chat_id"))
                            for cid in chat_ids:
                                await bot.send_message(cid, "🔔 *Время подвести итоги дня!*\n\nFarxad, привет! Не забудь надиктовать сегодняшние расходы, чтобы финансовый отчёт оставался точным. 👇", parse_mode="Markdown")
                            LAST_EXPENSE_PROMPT_DATE = today_str
                except Exception as cron_err:
                    print(f"Ошибка вечернего уведомления: {cron_err}")

            async with httpx.AsyncClient() as client:
                res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?status=eq.pending", headers=headers)
                if res.status_code == 200:
                    reminders = res.json()
                    for r in reminders:
                        if not r.get("remind_at"): continue
                        remind_str = r["remind_at"].replace("T", " ")[:19]
                        remind_time = datetime.strptime(remind_str, "%Y-%m-%d %H:%M:%S")
                        if remind_time <= now:
                            try:
                                await bot.send_message(r["chat_id"], f"⏰ *НАПОМИНАНИЕ:* \n\n{r['text']}", parse_mode="Markdown")
                                await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?id=eq.{r['id']}", headers={**headers, "Content-Type": "application/json"}, json={"status": "sent"})
                            except Exception as msg_err:
                                print(f"Ошибка отправки напоминания: {msg_err}")
        except Exception as e:
            print(f"Ошибка в цикле таймера: {e}")
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
