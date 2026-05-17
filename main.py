import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, ReplyKeyboardMarkup, KeyboardButton
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

def add_to_memory(chat_id: int, role: str, content: str):
    if chat_id not in CONVERSATION_MEMORY:
        CONVERSATION_MEMORY[chat_id] = []
    CONVERSATION_MEMORY[chat_id].append({"role": role, "content": content})
    if len(CONVERSATION_MEMORY[chat_id]) > 6:
        CONVERSATION_MEMORY[chat_id].pop(0)

# ================= СИСТЕМНЫЕ ПРОМПТЫ =================
SYSTEM_PROMPT = """
Ты — Moneyfi Super Assistant, гибрид быстрого трекера личных финансов и умной CRM.
Твоя задача — извлечь данные из запроса пользователя и вернуть СТРОГИЙ JSON. Никаких рассуждений вне JSON!

Текущая дата: """ + get_now_tashkent().strftime("%Y-%m-%d %H:%M:%S") + """

Варианты message_type:
1. "personal_finance" — личные расходы/доходы.
2. "crm_lesson" — работа с учениками (оплата, списание урока, изменение баланса, расписание).
3. "insert_reminder" — создание напоминания.
4. "analytics_request" — пользователь задает вопрос, просит отчет, хочет узнать даты, балансы, или называет одинокое имя без команды.

Варианты action:
- "expense" / "income"
- "student_payment" / "lesson_status" / "set_balance" / "set_schedule"
- "reminder" 
- "delete_reminder" — если просит удалить, стереть или отменить напоминание (по имени или цифре).

ПРАВИЛО БЕЗОПАСНОСТИ: Если запрос не содержит явной команды на запись/удаление, а является вопросом или одиноким именем ("Давид", "Почему?", "Сколько?") -> ставь "analytics_request" и action: null.

Формат JSON:
{
  "is_system_action": true/false,
  "message_type": "personal_finance" | "crm_lesson" | "insert_reminder" | "analytics_request",
  "action": "expense" | "income" | "lesson_status" | "student_payment" | "set_balance" | "set_schedule" | "reminder" | "delete_reminder" | null,
  "finance_data": { "amount": number or null, "currency": "UZS", "category": string or null, "description": string or null },
  "crm_data": { "student_name": string or null, "lesson_state": null, "new_datetime": null, "lessons_count": number or null, "schedule_text": string or null },
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
        "ПРАВИЛО 1: НИКАКИХ Markdown-таблиц (символы '|' и '---' ЗАПРЕЩЕНЫ). Используй только вертикальные списки.\n"
        "ПРАВИЛО 2: Если пользователь спрашивает про даты окончания уроков, возьми его остаток из базы, расписание и посчитай точные даты.\n"
        "ПРАВИЛО 3: Если данных не хватает, попроси уточнения."
    )
    try:
        history = CONVERSATION_MEMORY.get(chat_id, [])
        messages = [{"role": "system", "content": system_instruction}] + history + [{"role": "user", "content": f"Сырые данные БД:\n{json.dumps(db_data, ensure_ascii=False)}\n\nЗапрос пользователя: {user_text}"}]
        response = await ai_client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        return response.choices[0].message.content
    except Exception as e:
        return f"Ошибка генерации ответа: {e}"

# ================= ОСНОВНОЙ ОБРАБОТЧИК ДЕЙСТВИЙ =================
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
            # === 1. ИНТЕЛЛЕКТУАЛЬНОЕ УДАЛЕНИЕ НАПОМИНАНИЙ ===
            if action == "delete_reminder":
                res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{chat_id}&status=eq.pending", headers=headers)
                reminders = res.json() if res.status_code == 200 else []
                if not reminders: return "🔔 У тебя сейчас нет активных напоминаний."

                history = CONVERSATION_MEMORY.get(chat_id, [])
                llm_messages = [{"role": "system", "content": "Проанализируй запрос и верни JSON со списком ID напоминаний, которые хочет удалить пользователь. Формат: {\"ids\": [1, 2]}"}] + history + [{"role": "user", "content": f"База напоминаний: {json.dumps(reminders, ensure_ascii=False)}\nКоманда: {original_text}"}]
                
                llm_res = await ai_client.chat.completions.create(model="gpt-4o-mini", messages=llm_messages, response_format={"type": "json_object"})
                target_ids = json.loads(llm_res.choices[0].message.content).get("ids", [])
                
                if not target_ids: return "🔍 Не смог найти напоминания по твоему запросу. Попробуй нажать кнопку 'Напоминания'."
                
                deleted_texts = []
                for rid in target_ids:
                    for r in reminders:
                        if r["id"] == rid: deleted_texts.append(r["text"])
                    await client.delete(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?id=eq.{rid}", headers=headers)
                
                return f"🗑️ Успешно удалено из базы:\n➔ _{', '.join(deleted_texts)}_"

            # === 2. СОЗДАНИЕ НАПОМИНАНИЙ ===
            if m_type == "insert_reminder" or action == "reminder":
                if not rdata.get("remind_at"): return "❌ Укажите точное время для напоминания."
                payload = {"chat_id": chat_id, "text": rdata.get("text"), "remind_at": rdata.get("remind_at"), "status": "pending"}
                await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders", headers=headers, json=payload)
                return f"🔔 *Напоминание сохранено!* \n📅 Время: {payload['remind_at']}\n🎯 Суть: \"{payload['text']}\""

            # === 3. ЛИЧНЫЕ ФИНАНСЫ ===
            if m_type == "personal_finance":
                if fdata.get("amount") is None: return "❌ Ошибка: не указана сумма транзакции."
                amount_val = int(fdata.get("amount"))
                payload = {"chat_id": chat_id, "action": action, "amount": amount_val, "currency": fdata.get("currency", "UZS"), "category": fdata.get("category"), "description": fdata.get("description"), "date": get_now_tashkent().strftime("%Y-%m-%d")}
                await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_finance", headers=headers, json=payload)
                return f"💰 *{ 'Расход' if action=='expense' else 'Доход' } записан:* {amount_val} UZS -> {payload['category']}"

            # === 4. CRM СИСТЕМА УЧЕНИКОВ ===
            if m_type == "crm_lesson":
                if not st_name: return "⚠️ Уточните, пожалуйста, имя ученика."

                if action == "set_schedule":
                    sched_info = cdata.get("schedule_text") or original_text
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json(): await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"schedule": sched_info})
                    else: await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": 0, "schedule": sched_info})
                    return f"📅 Расписание для *{st_name}* установлено: `{sched_info}`"

                elif action == "student_payment":
                    if cdata.get("lessons_count") is None or fdata.get("amount") is None: return "❌ Укажите сумму оплаты и количество оплаченных уроков."
                    count = int(cdata.get("lessons_count"))
                    amount_val = int(fdata.get("amount"))
                    
                    f_payload = {"chat_id": chat_id, "action": "income", "amount": amount_val, "currency": "UZS", "category": "Уроки", "description": f"Оплата обучения: {st_name}", "date": get_now_tashkent().strftime("%Y-%m-%d")}
                    await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_finance", headers=headers, json=f_payload)
                    
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json():
                        new_bal = st_res.json()[0]["balance_lessons"] + count
                        await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                    else:
                        await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": count})
                    return f"🎓 Оплата от *{st_name}* принята. Начислено +{count} уроков."

                elif action == "lesson_status":
                    l_payload = {"chat_id": chat_id, "student_name": st_name, "lesson_state": cdata.get("lesson_state"), "new_datetime": cdata.get("new_datetime")}
                    await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_crm_lessons", headers=headers, json=l_payload)
                    if l_payload["lesson_state"] == "conducted":
                        st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                        if st_res.json():
                            new_bal = max(0, st_res.json()[0]["balance_lessons"] - 1)
                            await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": new_bal})
                            return f"📉 Урок у *{st_name}* списан. Остаток: {new_bal} уроков."
                    return f"📅 Статус урока *{st_name}* обновлен."

                elif action == "set_balance":
                    count = cdata.get("lessons_count")
                    if count is None: return "❌ Ошибка: Укажите точную цифру для нового баланса уроков."
                    st_res = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?name=eq.{st_name}", headers=headers)
                    if st_res.json(): await client.patch(f"{SUPABASE_URL}/rest/v1/moneyfi_students?id=eq.{st_res.json()[0]['id']}", headers=headers, json={"balance_lessons": int(count)})
                    else: await client.post(f"{SUPABASE_URL}/rest/v1/moneyfi_students", headers=headers, json={"chat_id": chat_id, "name": st_name, "balance_lessons": int(count)})
                    return f"🔧 Баланс ученика *{st_name}* установлен на *{count}* уроков."

        except Exception as e:
            return f"❌ Системная ошибка БД: {e}"
    return "🤷‍♂️ Операция не распознана."

# ================= МАРШРУТИЗАЦИЯ СООБЩЕНИЙ =================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    CONVERSATION_MEMORY[message.chat.id] = []
    await message.answer("🚀 Moneyfi Super Assistant инициализирован. Защита БД и память включены.", reply_markup=get_main_keyboard())

async def process_analytics(message: Message, query_text: str):
    await message.answer("🔄 Собираю данные...")
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient() as client:
        res_f = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_finance?chat_id=eq.{message.chat.id}", headers=headers)
        res_s = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_students?chat_id=eq.{message.chat.id}", headers=headers)
        res_r = await client.get(f"{SUPABASE_URL}/rest/v1/moneyfi_reminders?chat_id=eq.{message.chat.id}&status=eq.pending", headers=headers)
        combined_data = {
            "finance": res_f.json() if res_f.status_code == 200 else [],
            "students": res_s.json() if res_s.status_code == 200 else [],
            "reminders": res_r.json() if res_r.status_code == 200 else []
        }
        reply = await generate_smart_reply(query_text, combined_data, message.chat.id)
        add_to_memory(message.chat.id, "user", query_text)
        add_to_memory(message.chat.id, "assistant", reply)
        await message.answer(reply, parse_mode="Markdown")

@dp.message(F.text == "📉 Мои расходы")
async def btn_expenses(message: Message): await process_analytics(message, "Покажи список моих расходов и посчитай итог.")

@dp.message(F.text == "🪙 Мои доходы")
async def btn_incomes(message: Message): await process_analytics(message, "Покажи список моих доходов.")

@dp.message(F.text == "🎓 CRM: Ученики")
async def btn_crm(message: Message): await process_analytics(message, "Выведи профили всех студентов, их расписание и остаток уроков.")

@dp.message(F.text == "🔔 Напоминания")
async def btn_reminders(message: Message): await process_analytics(message, "Покажи список актуальных напоминаний.")

@dp.message(F.text == "📊 Финансовый отчёт")
async def btn_report(message: Message): await process_analytics(message, "Сделай детальный финансовый отчет: доходы, расходы, чистая прибыль.")

@dp.message(F.text)
async def handle_text(message: Message):
    ai_data = await parse_via_ai(message.text, message.chat.id)
    if ai_data.get("message_type") == "analytics_request" or ai_data.get("action") is None:
        return await process_analytics(message, message.text)
    
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
            return await process_analytics(message, transcription.text)

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
                except Exception as e: print(f"Night cron error: {e}")

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
        except Exception as e: print(f"Scheduler loop error: {e}")
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
