FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости из requirements.txt и gunicorn (продакшн-сервер для Flask)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Копируем сам код сайта
COPY . .

EXPOSE 8080

# Запускаем Flask через gunicorn (app:app означает файл app.py и объект app внутри него)
CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:8080", "app:app"]
