# ⬡ Kleinanzeigen Hunter — Руководство по установке

## Что это?
Локальный инструмент для поиска выгодных объявлений на Kleinanzeigen.de.
Работает **полностью бесплатно** — никаких API-ключей, никакого облака.

---

## Шаг 1 — Установить Python 3.11+

Скачать: https://www.python.org/downloads/
Проверить: `python --version`

---

## Шаг 2 — Установить Ollama (локальная AI)

```bash
# macOS / Linux:
curl -fsSL https://ollama.ai/install.sh | sh

# Windows:
# Скачать установщик с https://ollama.ai/download
```

Скачать модель (одна из двух — на выбор):

```bash
# Рекомендуется — хорошо понимает немецкий, быстрый (~2GB):
ollama pull llama3.2

# Альтернатива — отличное качество анализа (~4GB):
ollama pull mistral
```

Запустить сервер Ollama (должен работать в фоне):
```bash
ollama serve
```

---

## Шаг 3 — Установить зависимости проекта

```bash
cd kleinanzeigen-hunter
pip install -r requirements.txt
```

---

## Шаг 4 — Запустить

```bash
cd backend
uvicorn server:app --reload --port 8080
```

Открыть в браузере: **http://localhost:8080**

---

## Как пользоваться

### Поиск
1. Введи запрос в строку поиска (например: "iPhone 13", "E-Bike", "MacBook Pro")
2. Укажи кол-во страниц (2-3 обычно достаточно)
3. Нажми **↗**
4. AI автоматически проанализирует каждое объявление

### Фильтры
- **Score ≥ X** — показывать только объявления с оценкой выше X
- **Текстовый поиск** — фильтр по названию
- **Статус** — Все / Новые / Наблюдаю / Контактировал

### Работа с объявлением
- **↗ Anzeige öffnen** — открыть оригинал на Kleinanzeigen
- **🤖 Re-analysieren** — повторный AI анализ
- **Verhandlungsnachricht** — готовое сообщение для торга
- **📋 Kopieren** — скопировать сообщение в буфер
- **★ Merken** — добавить в избранное
- Статусы: Neu → Beobachte → Kontaktiert → Gekauft / Pass

### AI Score
| Score | Значение |
|-------|----------|
| 75–100 | 🔥 HEISS — очень выгодно, брать |
| 60–74  | 🟦 GUT — хороший вариант |
| 40–59  | 🟡 OKAY — стоит посмотреть |
| 0–39   | 🔴 SCHLECHT — не стоит |

---

## Структура проекта

```
kleinanzeigen-hunter/
├── backend/
│   ├── server.py      # FastAPI сервер + REST API
│   ├── scraper.py     # Парсинг Kleinanzeigen
│   ├── ai_agent.py    # Анализ через Ollama
│   └── database.py    # SQLite хранилище
├── frontend/
│   └── index.html     # Веб-интерфейс
├── data/
│   └── listings.db    # База данных (создаётся автоматически)
└── requirements.txt
```

---

## Быстрые запросы для перепродажи

```
iPhone 12, iPhone 13, iPhone 14
MacBook Pro, MacBook Air M1
Samsung Galaxy S22, S23
RTX 3080, RTX 4070
E-Bike, Rennrad, Mountainbike
PS5, Nintendo Switch
Laptop Dell, ThinkPad
Sofa, Couch
VW Golf, BMW 3er
```

---

## Советы

- Запускай поиск утром — свежие объявления добавляются ночью
- Score 75+ с ценой "VB" = идеальный кандидат для торга
- Кнопка "Verhandlungsnachricht" сразу даёт готовый текст сообщения
- Ставь статус "Kontaktiert" чтобы не запутаться

---

## Troubleshooting

**Ollama не работает:**
```bash
ollama serve   # запустить сервер
ollama list    # проверить установленные модели
```

**"Keine Anzeigen gefunden":**
- Попробуй другой запрос (немецкие слова работают лучше)
- Kleinanzeigen иногда блокирует — подожди 30 сек и повтори

**Медленный анализ:**
- Нормально, LLM работает локально
- llama3.2 быстрее mistral при схожем качестве
