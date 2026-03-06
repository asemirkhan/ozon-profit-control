# Landing v1 (RU)

Премиальный одностраничный лендинг для сервиса аналитики прибыли маркетплейсов.

## Локальный запуск

```bash
cd web
npm i
npm run dev
```

Откройте `http://localhost:3000`.

## Переменные окружения

Создайте `.env.local` в папке `web/`:

```env
NEXT_PUBLIC_TG_LINK=https://t.me/your_link
NEXT_PUBLIC_LEAD_FORM=
NEXT_PUBLIC_BRAND_NAME=Profit Control
```

Правило CTA:
- если `NEXT_PUBLIC_LEAD_FORM` заполнен -> кнопки ведут на форму
- если `NEXT_PUBLIC_LEAD_FORM` пуст -> кнопки ведут в Telegram (`NEXT_PUBLIC_TG_LINK`)

## Деплой на Vercel

1. Импортируйте репозиторий в Vercel.
2. В настройках проекта выставьте:
- **Root Directory** = `web`
3. Добавьте env-переменные:
- `NEXT_PUBLIC_TG_LINK`
- `NEXT_PUBLIC_LEAD_FORM`
- `NEXT_PUBLIC_BRAND_NAME`
4. Нажмите Deploy.

## Технологии

- Next.js (App Router) + TypeScript
- TailwindCSS
- shadcn/ui-style components
- Framer Motion
- Recharts
- next/image
