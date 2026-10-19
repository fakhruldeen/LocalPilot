# دليل تثبيت وإلغاء تثبيت LocalPilot

يوضح هذا الدليل خيارات التثبيت المختلفة، ونطاقات العزل، وإجراءات الإلغاء الآمنة على نظامي لينكس وماك (وكذلك نظام ويندوز عبر WSL2).

---

## الطريقة 1: المثبت التلقائي (الخيار الموصى به)

للمستخدمين الذين يفضلون تثبيتاً سهلاً ومستقلاً دون ضبط بيئات بايثون يدوياً:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

### ما الذي يقوم به المثبت؟
1. **فحص التوافق:** يتأكد من أن نظام التشغيل هو لينكس أو ماك، ويتحقق من توفر بايثون 3.11+.
2. **إنشاء مجلد تطبيق مستقل ومعزول:**
   * على **لينكس:** `~/.local/share/localpilot`
   * على **ماك:** `~/Library/Application Support/LocalPilot`
3. **تثبيت حزم التطبيق:** ينشئ بيئة بايثون افتراضية مخصصة (`app-env`) داخل ذلك المجلد، ويثبت الحزم المطلوبة دون تعديل بيئة بايثون الرئيسية في نظامك.
4. **تجهيز أمر التشغيل:** ينشئ ملف تشغيل في مسار التطبيق ورابطاً سريعاً في `~/.local/bin/localpilot`.
5. **لا يحتاج لصلاحيات المسؤول:** يعمل المثبت بالكامل في نطاق المستخدم العادي ولا يطلب كلمة مرور `sudo` أو root.

### خيارات التثبيت المتقدمة
يمكنك تمرير معاملات مخصصة لأمر التثبيت عبر `bash -s --`:

```bash
# فحص جاهزية النظام دون تنزيل أي ملفات
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --check

# وضع التثبيت المباشر دون مطالبات تأكيد
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- -y

# تحديد نطاق التثبيت: معزول (isolated - الافتراضي)، أو للمستخدم (user)، أو للنظام كاملاً (system)
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --scope user
```

---

## الطريقة 2: التثبيت عبر `pipx` (لمطوري بايثون)

تتيح أداة [`pipx`](https://pypa.github.io/pipx/) تثبيت برامج الطرفية في بيئات معزولة مع جعل أوامرها متاحة في مسار نظامك:

```bash
pipx install localpilot
```

وللترقية مستقبلاً:
```bash
pipx upgrade localpilot
```

---

## الطريقة 3: التثبيت عبر `pip` أو `uv`

باستخدام `pip`:
```bash
pip install localpilot
```

باستخدام `uv`:
```bash
uv tool install localpilot
```

---

## التحقق بعد التثبيت

تأكد من اكتمال التثبيت واستعداد حاسوبك بتشغيل أمر الفحص:

```bash
localpilot doctor
```

يعرض هذا الأمر مواصفات جهازك وميزانية الذاكرة الآمنة المتاحة للنماذج.

---

## إلغاء التثبيت والحذف الآمن

صُمم LocalPilot بحيث يمكن إزالته بنظافة تامة دون ترك أي خدمات مخفية في الخلفية.

### الخيار (أ): إزالة التطبيق مع الاحتفاظ بنماذجك المحملة (الخيار الموصى به)
إذا كنت ترغب في إزالة برنامج LocalPilot مع الحفاظ على ملفات النماذج المحملة على القرص حتى لا تضطر لتنزيلها مجدداً لاحقاً:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --uninstall
```

* **ما الذي يتم حذفه؟** ملف أمر التشغيل والبيئة الافتراضية المعزولة (`app-env`).
* **ما الذي يتم الإبقاء عليه؟** ملفات أوزان النماذج في مجلد `models/` تظل محفوظة بأمان على قرصك.

إذا كنت قد ثبته عبر `pipx` أو `pip`:
```bash
pipx uninstall localpilot
# أو
pip uninstall localpilot
```

---

### الخيار (ب): الحذف الكامل مع إزالة كافة النماذج المحملة

> [!WARNING]
> يؤدي هذا الخيار إلى حذف كافة ملفات النماذج المحملة نهائياً من قرصك الصلب. هذا الإجراء دائم ولا يمكن التراجع عنه، وسيتطلب منك إعادة تنزيل النماذج في حال رغبت في استخدامها مستقبلاً.

لإزالة التطبيق وحذف كافة ملفات النماذج التابعة له بالكامل:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash -s -- --uninstall --purge-models
```
