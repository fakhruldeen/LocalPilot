<div align="center">

<img src="docs/img/banner-readme.jpeg" alt="LocalPilot Banner" width="100%" />

# LocalPilot 🦙
### الذكاء الاصطناعي المحلي.. بكل بساطة وأمان

[English](README.md) • **[العربية](README.ar.md)**

[![PyPI Version](https://img.shields.io/pypi/v/localpilot.svg?style=flat-square&color=blue)](https://pypi.org/project/localpilot/)
[![Python Versions](https://img.shields.io/pypi/pyversions/localpilot.svg?style=flat-square&color=3776AB)](https://pypi.org/project/localpilot/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23424540.svg)](https://doi.org/10.5281/zenodo.23424540)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey.svg?style=flat-square)](https://fakhr.me/LocalPilot/)
[![Documentation](https://img.shields.io/badge/Docs-GitHub%20Pages-success.svg?style=flat-square)](https://fakhr.me/LocalPilot/)

*اكتشف نماذج الذكاء الاصطناعي المتوافقة مع عتاد جهازك، ثبتها بأمان تام، وابدأ المحادثة بخصوصية مطلقة.*

[الموقع الإلكتروني](https://fakhr.me/LocalPilot/) • [دليل المبتدئين](https://fakhr.me/LocalPilot/guide.html) • [دليل النماذج](https://fakhr.me/LocalPilot/models.html) • [الأسئلة الشائعة](https://fakhr.me/LocalPilot/faq.html) • [المعمارية التقنية](https://fakhr.me/LocalPilot/architecture.html) • [التوثيق العربي](docs/ar/README.md) • [PyPI](https://pypi.org/project/localpilot/) • [DOI](https://doi.org/10.5281/zenodo.23424540)

</div>

---

## 🌟 لماذا LocalPilot؟

لا ينبغي أن يتطلب تشغيل الذكاء الاصطناعي محلياً على حاسوبك دراية بهندسة النظم، أو ضبط خيارات التجميع، أو حسابات الذاكرة وحجم طبقات الرسوميات (CUDA/Metal)، أو التعامل مع مشاكل البيئات البرمجية المعقدة.

**LocalPilot** هو تطبيق مكتوب بلغة بايثون وواعٍ بعتاد حاسوبك؛ يرشدك من أول خطوة لاكتشاف النماذج وحتى بدء المحادثة. يفحص مواصفات معالجك وذاكرتك، ويقترح النماذج المتوافقة، ويثبتها بإعدادات افتراضية آمنة، ويتيح لك إدارة الموارد عبر لوحة تحكم رسومية مريحة أو سطر أوامر سهل وبديهي.

---

## ✨ أهم المميزات

- 🧠 **فحص وتوصية ذكية مبنية على العتاد:** يفحص المعالج (CPU)، الذاكرة العشوائية (RAM)، بطاقات الرسوميات المخصصة والذاكرة الموحدة في أجهزة Apple Silicon، ويستبعد أي نموذج قد يرهق حاسوبك.
- 🧭 **معمارية ثلاثية المحاور (3-Axis Architecture):** يفصل طبقات التشغيل بوضوح:
  - **محركات الاستدلال (Backends):** `llama.cpp` (صيغ GGUF مع دعم Metal وCUDA) و`vLLM` للأنظمة المتقدمة.
  - **خدمات الإدارة (Execution Services):** تكامل هادئ مع `Ollama` أو `LM Studio` أو مشغل الحاويات في `Docker`.
  - **أساليب التنفيذ (Deployment):** عمليات عمال مباشرة ومعزولة، أو واجهات API محلية، أو حاويات.
- 🛡️ **حماية تامة للخدمات والبيانات الخارجية:**
  - احترام العمليات القائمة: تفريغ النماذج من الذاكرة دون إيقاف أو قتل خوادم الخلفية المشتركة.
  - حماية التخزين ومنع حذف أي ملفات لم يقم LocalPilot بإنشائها.
- 📦 **خيارات تثبيت مرنة:** تثبيت معزول على مستوى التطبيق (الافتراضي، لا يتطلب صلاحيات root)، أو على مستوى المستخدم (`~/.local/bin`)، أو على مستوى النظام.
- 💻 **واجهة مزدوجة للمستخدمين:**
  - **لوحة تحكم تفاعلية ملونة (TUI):** شاشة رسومية كاملة داخل الطرفية مبنية على مكتبة [Textual](https://textual.textualize.io/).
  - **سطر أوامر نصي سريع (CLI):** أوامر منظمة وسهلة مبنية على [Typer](https://typer.tiangolo.com/) و[Rich](https://rich.readthedocs.io/).
- 🔒 **خصوصية حقيقية 100%:** يعمل دون اتصال بالإنترنت، ولا يرسل أي بيانات تحليلية أو سجلات تتبع إطلاقاً.

---

## 🚀 التثبيت والبدء السريع

اختر الطريقة الأنسب لحاسوبك:

### 1. المثبت التلقائي (موصى به للمستخدمين غير التقنيين)
دون الحاجة لإنشاء بيئات بايثون افتراضية يدوياً:
```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

### 2. التثبيت عبر `pipx` (معزول وسهل الإدارة)
```bash
pipx install localpilot
```

### 3. التثبيت عبر `pip` أو `uv` (للمطورين)
```bash
pip install localpilot
# أو عبر uv
uv tool install localpilot
```

> **تنبيه:** التثبيت عبر `pip` أو `pipx` يتطلب وجود **Python (الإصدار 3.10 أو أحدث)** و`pip` مثبتين مسبقاً على نظامك.

---

## 🎮 دليل الاستخدام

### فتح لوحة التحكم الرسومية (TUI)
ببساطة، اكتب في الطرفية:
```bash
localpilot
```
أو للوصول المباشر:
```bash
localpilot tui
```

يمكنك التنقل في اللوحة باستخدام اختصارات لوحة المفاتيح:
- <kbd>d</kbd> اكتشاف النماذج المتوافقة (Discover)
- <kbd>i</kbd> عرض النماذج المثبتة (Installed)
- <kbd>r</kbd> مراقبة النماذج النشطة في الذاكرة (Running)
- <kbd>s</kbd> استعراض استهلاك المساحة التخزينية (Storage)
- <kbd>a</kbd> سجل النشاطات والقرارات الأخيرة (Activity)
- <kbd>q</kbd> إغلاق التطبيق (Exit)

---

### أهم أوامر الطرفية (CLI Commands)

#### 1. فحص توافق عتاد الحاسوب:
```bash
localpilot doctor
```

#### 2. استعراض النماذج المتوافقة:
```bash
# عرض النماذج المتوافقة مع سعة الذاكرة
localpilot discover

# تصفية النماذج المتخصصة في المحادثة
localpilot discover --task chat

# البحث باسم النموذج
localpilot discover -q "llama"
```

#### 3. إعداد وتثبيت نموذج جديد مع الفحص الحقيقي:
```bash
localpilot setup llama3.2:3b
```

#### 4. بدء المحادثة الخاصة والمباشرة:
```bash
localpilot chat llama3.2:3b
```

#### 5. استعراض النماذج النشطة والمثبتة:
```bash
# النماذج قيد التشغيل حالياً
localpilot running

# كافة النماذج المحملة على القرص
localpilot installed
```

#### 6. مراجعة المساحة التخزينية وحذف النماذج:
```bash
# عرض تفاصيل استهلاك القرص
localpilot storage

# حذف نموذج بأمان وتحرير مساحته
localpilot remove llama3.2:3b --delete-files
```

#### 7. تحديث التطبيق أو إزالته النظيفة:
```bash
# فحص وتحديث LocalPilot من PyPI
localpilot self update

# إزالة نظيفة وشاملة للتطبيق بنقرة واحدة
localpilot self uninstall --purge-models
```

---

## 📐 المعمارية البرمجية

```text
Application Services (CLI / TUI / Setup / Chat)
                    │
                    ▼
          ┌───────────────────┐
          │   ExecutionPlan   │
          └─────────┬─────────┘
                    │
   ┌────────────────┼────────────────┐
   ▼                ▼                ▼
Backend          Service         Deployment
llama.cpp        Ollama          direct_worker
vLLM             LM Studio       local_api_service
                 Docker Runner   container
```

لمزيد من التفاصيل حول التوافق ومصفوفة التشغيل المدعومة، راجع **[مصفوفة الدعم الكاملة (Support Matrix)](docs/SUPPORT_MATRIX.md)**.

---

## 👤 المطور والمسؤول عن المشروع

**محمد فؤاد فخر الدين (Mohamed Fouad Fakhruldeen)**  
*مبتكر ومطور LocalPilot. خبير في حوكمة مكاتب إدارة المشاريع (PMO)، ونظم الذكاء الاصطناعي، وبيئات العمل الرشيقة.*

- 🌐 **الموقع الشخصي:** [fakhr.me](https://fakhr.me/)
- 💼 **لينكد إن:** [linkedin.com/in/fakhruldeen](https://www.linkedin.com/in/fakhruldeen)
- 🐙 **جيت هاب:** [@fakhruldeen](https://github.com/fakhruldeen)
- 📬 **البريد الإلكتروني:** fakhruldeen@gmail.com

---

## ⚠️ إخلاء المسؤولية

> [!WARNING]
> **للاستخدام الشخصي والتجريبي فقط:** مشروع LocalPilot مخصص للمهتمين والطلاب والمستكشفين غير المهنيين في بيئة الاستخدام الشخصي. يعد تشغيل نماذج الذكاء الاصطناعي على الحواسيب الشخصية تقنية حديثة وسريعة التطور وتتطلب موارد حاسوبية ومعرفة تقنية مستمرة لضبطها بشكل موثوق.
>
> هذا المشروع مقدم "كما هو" (AS-IS) بدون أي ضمانات من أي نوع. لا يتحمل المطور أو المساهمون أي مسؤولية قانونية أو أضرار ناتجة عن فقدان البيانات، أو بطء الأجهزة، أو انقطاع الخدمات، أو المشاكل التي قد تطرأ جراء سوء الاستخدام أو استخدامه في بيئات الإنتاج التجارية الحساسة.

---

## 📚 الاستشهاد والتوثيق الأكاديمي (Citation & DOI)

إذا استخدمت LocalPilot في أبحاثك أو مقالاتك أو مشاريعك مفتوحة المصدر، يرجى الاستشهاد به وفق الآتي:

```bibtex
@software{fakhruldeen_2026_localpilot,
  author       = {Mohamed (Fouad) Fakhruldeen},
  title        = {fakhruldeen/LocalPilot: v0.1.1},
  month        = oct,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v0.1.1},
  doi          = {10.5281/zenodo.23424540},
  url          = {https://doi.org/10.5281/zenodo.23424540}
}
```

**صيغة التوثيق النصية (APA):**
> Mohamed (Fouad) Fakhruldeen. (2026). fakhruldeen/LocalPilot: v0.1.1 (Version v0.1.1) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23424540

---

## 📄 رخصة الاستخدام

هذا المشروع منشور تحت شروط رخصة **GNU General Public License v3 (GPLv3)**. راجع ملف [LICENSE](LICENSE) لمزيد من التفاصيل.
