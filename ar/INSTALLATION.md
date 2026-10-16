# دليل التثبيت الشامل لـ LocalPilot 🚀

يوفر هذا الدليل شرحاً تفصيلياً لجميع طرق تثبيت **LocalPilot** على نظامي التشغيل **Linux** و **macOS**، بالإضافة إلى خيارات التثبيت المتقدمة، استكشاف الأخطاء، وكيفية إلغاء التثبيت بأمان.

---

## ⚡ طرق التثبيت

### الطريقة 1: المثبت التلقائي عبر الطرفية (موصى به للمبتدئين)

هذه هي أسهل وأسرع طريقة؛ حيث يقوم السكربت بفحص نظامك، إعداد بيئة بايثون معزولة، وتثبيت LocalPilot تلقائياً دون التأثير على إعداداتك الحالية:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

**ما الذي يفعله السكربت؟**
1. يتحقق من وجود Python (الإصدار 3.11 أو أحدث).
2. ينشئ بيئة بايثون معزولة في مجلد المستخدم (`~/.local/share/localpilot/venv`).
3. يثبت أحدث إصدار من الحزمة من مستودع PyPI الرسمي.
4. يضيف اختصار الأمر `localpilot` في مسار الأوامر لديك (`~/.local/bin/localpilot`).

---

### الطريقة 2: التثبيت عبر `pipx` (موصى به لمستخدمي بايثون)

إذا كنت تفضل استخدام أدوات سطر الأوامر القياسية في بايثون:

```bash
# تثبيت pipx إذا لم يكن مثبتاً لديك:
# على Ubuntu/Debian: sudo apt install pipx && pipx ensurepath
# على macOS: brew install pipx && pipx ensurepath

pipx install localpilot
```

لترقية LocalPilot مستقبلاً عبر pipx:
```bash
pipx upgrade localpilot
```

---

### الطريقة 3: التثبيت عبر `pip` أو `uv`

إذا كنت تدير بيئاتك الافتراضية بنفسك:

```bash
# إنشاء بيئة افتراضية وتفعيلها
python3 -m venv myenv
source myenv/bin/activate

# التثبيت عبر pip
pip install -U localpilot

# أو التثبيت عبر uv السريعة
uv pip install localpilot
# أو كأداة عامة عبر uv
uv tool install localpilot
```

> [!NOTE]
> يتطلب التثبيت عبر pip وجود Python (>= 3.11) ومدير الحزم pip مثبتاً بالفعل على نظامك.

---

## 🔍 التحقق من نجاح التثبيت

بعد اكتمال التثبيت، افتح نافذة طرفية جديدة واكتب:

```bash
localpilot doctor
```

يقوم هذا الأمر بفحص عتاد حاسوبك، المعالج، الذاكرة العشوائية المتاحة، بطاقات الرسوميات، وأي برمجيات مساندة مثبتة لديك.

لبدء تشغيل لوحة التحكم الرسومية الملونة:
```bash
localpilot
# أو
localpilot tui
```

---

## ⚙️ خيارات نطاق التثبيت (Installation Scopes)

عند تثبيت النماذج ومحركات التشغيل عبر LocalPilot، يمكنك اختيار نطاق التثبيت المناسب:

| النطاق | الخيار | الوصف | المتطلبات |
| :--- | :--- | :--- | :--- |
| **المعزول (افتراضي)** | `--scope isolated` | تثبيت محركات التشغيل في مجلد خاص بـ LocalPilot فقط | لا يتطلب صلاحيات root |
| **مستخدم النظام** | `--scope user` | تثبيت الأدوات في مسار المستخدم العام (`~/.local/bin`) | لا يتطلب صلاحيات root |
| **النظام بأكمله** | `--scope system` | تثبيت عام على كامل النظام (`/usr/local/bin`) | يتطلب صلاحيات sudo |

مثال لتثبيت نموذج بنطاق معزول:
```bash
localpilot setup cand_qwen25_coder --scope isolated
```

---

## 🔄 كيفية التحديث

### تحديث تطبيق LocalPilot نفسه:
```bash
# فحص وتحديث تلقائي
localpilot self update

# أو يدوياً عبر pipx / pip:
pipx upgrade localpilot
# أو:
pip install -U localpilot
```

### فحص وتحديث أوزان النماذج المثبتة محلياً:
```bash
localpilot update <معرّف_التثبيت>
```

---

## 🗑️ إلغاء التثبيت والإزالة النظيفة

يوفر LocalPilot إمكانية إزالة شفافة بالكامل دون ترك أي ملفات مخفية:

### إزالة تطبيق LocalPilot:
```bash
# إزالة التطبيق مع الاحتفاظ بملفات النماذج التي قمت بتحميلها:
localpilot self uninstall

# أو إزالة كاملة تشمل حذف ملفات النماذج وقاعدة البيانات لتوفير المساحة:
localpilot self uninstall --purge-models
```

### إزالة نموذج معين فقط:
```bash
localpilot remove <installation-id> --delete-files
```

---

## 🛠️ استكشاف الأخطاء الشائعة

1. **الأمر `localpilot: command not found`:**
   تأكد من أن المسار `~/.local/bin` موجود ضمن متغير المسارات `PATH` في ملف تهيئة الطرفية (`~/.bashrc` أو `~/.zshrc`). أضف السطر التالي ثم أعد تشغيل الطرفية:
   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   ```

2. **خطأ في إصدار بايثون القديم:**
   يتطلب التطبيق إصدار Python 3.11 أو أحدث. تحقق من الإصدار الحالي عبر:
   ```bash
   python3 --version
   ```
