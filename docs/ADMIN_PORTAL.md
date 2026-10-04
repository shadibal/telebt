# Web Admin Portal — Plan

لوحة إدارة ويب بديلة عن أوامر Telegram للتطوير والمحاكاة. الباقة: **Next.js (App Router) + Prisma (PostgreSQL) + shadcn/ui + Zod**. تعيش في `web/` ضمن نفس المستودع (monorepo)، وتمتلك قاعدة البيانات؛ يبقى بوت Python على `mock_data/*.json` حتى مرحلة لاحقة.

## القرارات

- النطاق: إدارة كاملة (تصفح/إضافة/تعديل/حذف كل الكيانات) + لوحة محاكاة بديلة عن `/dev`.
- قاعدة البيانات: PostgreSQL عبر Prisma؛ السكربت `prisma/seed.ts` يستورد `mock_data/*.json`.
- المصادقة: مستخدم أدمن واحد من متغيرات البيئة (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) مع كوكي HttpOnly موقّعة بـ `SESSION_SECRET`؛ لا NextAuth في هذه المرحلة.
- الملفات تحت `web/`؛ البوت والمستندات الحالية لا تُغيَّر.

## البنية

```
web/
├── prisma/
│   ├── schema.prisma        # User, CatalogGame, Subscription, Device, LinkedGame, Proxy,
│   │                        # ScheduledPlan, PlanOperation, PlanCounter, FinancialLedger, Referral
│   ├── seed.ts              # يستورد mock_data/*.json
│   └── migrations/
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── login/page.tsx
│   │   ├── (admin)/layout.tsx        # حارس المصادقة + هيكل الشريط الجانبي
│   │   ├── (admin)/dashboard|users|catalog|devices|proxies|plans|subscriptions|finance|referrals|simulate
│   │   └── api/login|logout/route.ts
│   ├── server/
│   │   ├── db.ts                     # Prisma singleton
│   │   ├── auth.ts                   # توقيع/تحقق كوكي الجلسة (HMAC)
│   │   └── queries/                  # استعلامات + طفرات لكل كيان
│   ├── lib/
│   │   ├── validators.ts             # Zod — منقول من core/validation.py
│   │   └── utils.ts
│   └── components/ui/                # shadcn
├── middleware.ts                     # حماية مسارات (admin)
├── docker-compose.yml                # Postgres محلي
└── .env.example                      # DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD, SESSION_SECRET
```

## الميزات

- صفحات قوائم وتفاصيل لكل كيان مع حذف يراعي التتابع (الجهاز ← الخطط، العدادات، الروابط).
- نماذج CRUD مع تحقق Zod في Server Actions.
- لوحة محاكاة `/simulate` تعادل أوامر `/dev`: حالة الاشتراك (جديد يمسح البيانات التشغيلية)، انتهاء/تقدم +32 يوماً/تجديد (شهري أو أسبوعي)، استنفاد/إعادة تعيين الحصة، تعديل الرصيد الوهمي (+سطر دفتري)، فشل أول عملية مجدولة، تعيين نتيجة فحص البروكسي.
- محرر كتالوج الألعاب (قوالب الأحداث normal/purchase، OS، platform).

## الإعداد

```bash
cd web
cp .env.example .env          # DATABASE_URL=postgresql://...
docker compose up -d db       # أو Postgres محلي
npm install
npx prisma migrate dev
npx prisma db seed            # يستورد mock_data الحالي
npm run dev
```

## التحقق

- `npx prisma validate`
- `npm run build`
- اختبارات وحدة Vitest لمخططات Zod (نفس أنماط `VALIDATION_PATTERN_*` في البوت)
- فحص يدوي: تسجيل الدخول، CRUD، لوحة المحاكاة

## ملاحظات المرحلة 1

لا مدفوعات فعلية، لا فحص شبكة للبروكسي، لا جدولة فعلية، لا تتبع attribution — المحاكاة فقط، تماشياً مع `PROJECT_SPEC.md`. البوت يبقى على ملفات JSON؛ دمج مصدر البيانات لاحقاً قرار المرحلة التالية.
