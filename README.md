# Курсоград — образовательный портал

Учебный сайт на Flask + SQLite (не магазин): онлайн-курсы, запись, уроки, прогресс.

## Запуск
```bash
pip install -r requirements.txt
python app.py   # http://localhost:8080
```

## Страницы
Главная `/`, Курсы `/courses`, Курс `/course/<id>`, Урок `/lesson/<id>`,
Преподаватели `/teachers`, Регистрация `/register`, Вход `/login`,
Личный кабинет `/dashboard`, О портале `/about`, Контакты `/contacts`, 404.

## База данных
SQLite `data/app.db`: `users`, `teachers`, `courses`, `lessons`,
`enrollments`, `progress`, `feedback`, `messages`.
Пароли — хеши (werkzeug), авторизация — сессии Flask, доступ к урокам и
кабинету — декоратор `@login_required`.
