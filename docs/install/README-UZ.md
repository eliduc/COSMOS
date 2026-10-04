# COSMOS dasturini oʻrnatish va oʻchirish

Toʻplam mutaxassis boʻlmagan odam uchun moʻljallangan: dastur kompyuterni
oʻzi oʻrganadi, faktlardan kelib chiqib aniqlash mumkin boʻlgan hamma
narsani oʻzi hal qiladi va faqat odam javob beradigan narsalarni soʻraydi.
Muloqot besh tilning birida — ingliz, arab, oʻzbek, qozoq yoki rus
tilida boradi.

## Toʻplamda nima bor

| Fayl | Nima uchun |
|---|---|
| `INSTALL-COSMOS.cmd` (ayni fayl: `УСТАНОВИТЬ-COSMOS.cmd`) | oʻrnatish uchun bosiladigan fayl |
| `UNINSTALL-COSMOS.cmd` (ayni fayl: `УДАЛИТЬ-COSMOS.cmd`) | oʻchirish uchun bosiladigan fayl |
| `install_bootstrap.ps1` | tayyorgarlik: Python va git dasturlarini topadi, rozilik boʻlsa yetishmayotganini oʻrnatadi |
| `uninstall_bootstrap.ps1` | oʻchirishga tayyorgarlik: Python dasturini va oʻchiruvchining oʻzini topadi |
| `install_cosmos.py` | oʻrnatuvchi: kompyuterni oʻrganish → reja → oʻrnatish → tekshirish |
| `uninstall_cosmos.py` | oʻrnatilgan dasturni oʻchirish |
| `UNINSTALL-PLAN-RU.md` | asoslash: oʻchirish nega aynan shunday tuzilgan |

Nom juftliklari — ayni bitta fayl: lotin harflaridagi nom kirill
harflarini fayl nomida oʻqiy olmaydigan maydonlar uchun.

## Oʻrnatish

`INSTALL-COSMOS.cmd` fayliga bosing. Birinchi savol — til; keyingi butun
muloqot shu tilda boradi va dasturning oʻzi ham shu tilda ishlaydi.
Ikkinchi savol — qayerga oʻrnatish. Qolgan hamma savolga variant raqami
bilan yoki Enter tugmasi bilan javob beriladi: har bir savolning xavfsiz
standart javobi bor.

Mohiyatan nima soʻraladi:

- **shu maydonda bulutli sunʼiy intellekt modellaridan foydalanishga
  ruxsat berilganmi**. «yoʻq» javobi — sunʼiy intellektning har qanday
  tavsiyasini faqat mahalliy model tayyorlaydi. «ha» javobi — bulut
  «ochiq maʼlumot» deb belgilangan toʻplamlar uchun ruxsat etiladi;
  buyurtmachi toʻplamlari «faqat mahalliy» belgisi bilan turadi va hech
  qachon bulutga chiqmaydi;
- **mahalliy model** (Ollama serveri). Agar u odatdagi manzilda javob
  bermasa, oʻrnatuvchi manzilni soʻraydi — server boshqa kompyuterda yoki
  boshqa portda boʻlishi mumkin. Modellar bir nechta boʻlsa, qaysinisidan
  foydalanishni soʻraydi. Mahalliy model umuman boʻlmasa, buni ochiq
  aytadi: buyurtmachi maʼlumotlari boʻyicha sunʼiy intellekt tavsiyalari
  ishlamaydi, lekin dasturning oʻzi toʻliq ishlaydi — parvoz zanjirlarini
  qurish va ekipaj jadvalini tuzish hisob-kitoblari sunʼiy intellekt
  modellariga bogʻliq emas;
- **foydalanuvchilar dasturga qanday kiradi** — parol bilan (darhol
  ishlaydi) yoki Cloudflare Access orqali bir martalik kod bilan;
- **dastur qaysi kompyuterlardan ochiladi** — faqat shu kompyuterdan yoki
  shu lokal tarmoqdagi barcha kompyuterlardan;
- **JDK va Maven oʻrnatilsinmi**, agar ular boʻlmasa: ular ekipaj
  jadvalini tuzish hisob-kitobi uchun kerak va oʻrnatish katalogiga
  qoʻyiladi, tizim oʻzgartirilmaydi.

Yakunda oʻrnatuvchi **dasturga kirish parolini**, «dastur qanday ishga
tushiriladi» boʻlimini va **oʻrnatish jurnaliga** yoʻlni koʻrsatadi:

```
oʻrnatish jurnali (dasturni oʻchirish uchun kerak): C:\Users\<siz>\AppData\Local\cosmos-install\install-<sana>.jsonl
```

Jurnal yonida **oʻrnatilganlar roʻyxati** (`manifest-<sana>.jsonl.gz`) hosil
boʻladi — oʻrnatish oʻzidan keyin qoldirgan hamma narsaning roʻyxati. Har
ikkala fayl oʻchirish uchun kerak; ular sizning profilingizda saqlanadi va
dasturning oʻzi oʻchirilgandan keyin ham qoladi. Ularni oʻqish shart emas.

Oxirgi savolda oʻrnatuvchi **COSMOS dasturini darhol ishga tushirishni**
taklif qiladi. Rozi boʻlsangiz, u dasturni ishga tushiradi, javobini
kutadi va brauzerda ochadi — brauzer faqat dastur haqiqatan javob
berganda ochiladi.

## Dastur keyinchalik qanday ishga tushiriladi

- **Ishga tushirish:** oʻrnatish katalogidagi (odatda `C:\cosmos`)
  `START-COSMOS.cmd` fayliga ikki marta bosing. U dasturni ishga tushiradi
  va brauzerni ochadi.
- **Manzil:** `http://127.0.0.1:8090`; agar tarmoqdan kirishga ruxsat
  bergan boʻlsangiz — boshqa kompyuterlardan shu kompyuterning manzili
  boʻyicha, masalan `http://192.168.1.50:8090`.
- **Toʻxtatish:** ishga tushirishda ochilgan oynani yoping.
- **Ekipaj jadvalini tuzish (2-bosqich)** — alohida xizmat:
  `run-solver.cmd`, port 9096.
- **Aviakompaniya maʼlumotlarini qayta ishlash moduli** — alohida
  xizmat: `run-cdpm.cmd`, port 8095, manzil `http://127.0.0.1:8095`.
  Modul aviakompaniya hujjatlarini (qoidalar, maʼlumot kitoblari)
  hisob-kitob uchun masala qoʻyilishiga aylantiradi. U faqat shu
  kompyuterni tinglaydi; tarmoqqa ochish uchun `--host 0.0.0.0` kaliti
  bilan ishga tushiring — modulning oʻz kirish tizimi yoʻq, shuning
  uchun buni faqat himoyalangan tarmoqda qiling.
- **Shablonlar katalogi** — alohida xizmat: `run-catalog.cmd`, port 8105,
  manzil `http://127.0.0.1:8105`; buyurtmachi oʻrnatmasida — faqat
  koʻrish (tahrirlar va versiyalar chiqarilishi egasida bajariladi).
  Agar xizmat sizning toʻplamingizga kirmasa, oʻrnatish uning ishga
  tushirish faylini yaratmaydi va bu haqda aytadi.
- **Parolni unutsangiz:** `webapp\config\auth.json` faylini oʻchiring va
  dasturni qaytadan ishga tushiring — u yangi parol yaratadi.

## Dasturni oʻchirish

`UNINSTALL-COSMOS.cmd` fayliga bosing. Dastur avval barcha tekshiruvlarni
**sinov rejimida** bajaradi va aynan nimani oʻchirishini koʻrsatadi, soʻng
rozilik soʻraydi. Siz «ha» deb javob bermaguningizcha hech narsa
oʻzgarmaydi; «ha» dan keyin barcha tekshiruvlar qaytadan — bevosita
oʻchirishning oʻzidan oldin bajariladi.

Buyruq satridan ishlash qulayroq boʻlganlar uchun ayni shu amal bevosita
ham mavjud:

```bash
C:\Python314\python.exe uninstall_cosmos.py
```

```bash
C:\Python314\python.exe uninstall_cosmos.py --apply
```

Foydali kalitlar: `--list` — shu kompyuterda qanday oʻrnatishlar topilgan;
`--target <katalog>` — oʻrnatishlar bir nechta boʻlsa; `--lang <kod>` —
chiqarish tili (standart holatda oʻrnatishda tanlangan til olinadi).

Oʻchiruvchi dastur dasturning oʻzi bilan birga ham keladi
(`C:\cosmos\uninstall_cosmos.py`), shuning uchun oʻrnatishni toʻplamsiz
ham oʻchirish mumkin: uni bevosita oʻsha yerdan ishga tushirish xavfsiz —
u oʻchiriladigan katalogdan oʻzi chiqib ketadi.

Xavfsizlik devori qoidasi va avtomatik ishga tushirish vazifasini
oʻchirish uchun **administrator huquqlari** kerak: agar ular mavjud
boʻlsa-yu, huquqlar boʻlmasa, dastur halol toʻxtaydi va buni aytadi.

### Oʻchirish nima qiladi va nima qilmaydi

Faqat oʻrnatish yaratgan narsalar oʻchiriladi: dastur katalogi,
maʼlumotlar katalogi (agar u oʻrnatish yaratgan holidagidek boʻsh boʻlsa),
ishga tushirish fayllari, xavfsizlik devori qoidasi va avtomatik ishga
tushirish vazifasi — agar ularni shu oʻrnatish yaratgan boʻlsa — hamda
oʻrnatishdan qolgan jarayonlar.

Prinsipial ravishda oʻchirilmaydi: oʻrnatishdan oldin ham kompyuterda
boʻlgan narsalar (Java, Python, git va boshqalar); umumiy resurslar —
boshqa loyihalar foydalanadigan pip keshi va Maven repozitoriysi; SSH
kalitlari, chunki ular repozitoriylarga allaqachon qoʻshilgan boʻlishi
mumkin. Bularning bari chiqarishda sanab oʻtiladi, yashirilmaydi.

Har qanday noaniqlikda oʻchirish **toʻxtaydi va obyektni koʻrsatadi**, siz
uchun qaror qabul qilmaydi. Har bir tekshiruv nechta obyektni koʻrib
chiqqanini chop etadi: koʻrib chiqilganlar soni aytilmagan «nuqson yoʻq»
degan xulosa hisobga olinmaydi.

### Bunday sinchkovlik nimadan kelib chiqqan

Oʻrnatuvchi tuzgan oʻrnatilganlar roʻyxati daraxtdagi **har bir** obyektni
solishtirish imkonini beradi. Usiz butun-butun ichki daraxtlarni — tarixi
bilan olingan nusxani, python muhitini, asboblar toʻplamini — koʻr-koʻrona
qabul qilishga toʻgʻri kelardi, bu esa oʻn toʻqqiz mingtadan oʻn olti ming
obyekt degani; egasi u yerga koʻchirgan begona fayl sezilmay qolardi va
oʻrnatish bilan birga yoʻq boʻlardi. Endi har bir obyekt yo oʻrnatilganlar
roʻyxatida qayd etilgan, yo dasturning oʻz ishi natijasida paydo boʻlgan
(jurnallar, keshlar, ishchi fayllar), yo begona deb tan olinadi — va bunda
oʻchirish toʻxtaydi.

### Agar oʻrnatish uzilib qolsa

Qoʻrqadigan joyi yoʻq: jurnal ish davomida va darhol diskka yozib
boriladi, shuning uchun unda nima yaratilib ulgurgani aniq koʻrsatilgan.
Oʻchirish aynan oʻshani olib tashlaydi. Jurnal umuman boʻlmasa (oʻrnatish
oldingi versiya bilan bajarilgan boʻlsa) — oʻchirish belgilar bilan
kifoyalanadi: ishga tushirish faylidagi belgi va katalog daraxtini
solishtirish; buni u ochiq aytadi, chunki bunday yoʻl kamroq aniq.

## Tillar

Muloqot matnlari repozitoriyda tillar boʻyicha alohida fayllarda turadi:
`crew-demo/handoff/i18n-installer/messages-<til>.json`. Rus tili —
manba; qolganlari uni kalitma-kalit takrorlaydi.

Biror iborani toʻgʻrilash yoki yangi til qoʻshish uchun: kerakli faylni
oʻzgartiring va qaytadan yigʻing —

```bash
C:\Python314\python.exe crew-demo\handoff\i18n-installer\embed_i18n.py
```

Yigʻish lugʻatlarni bevosita `install_cosmos.py` va `uninstall_cosmos.py`
fayllari ichiga joylaydi (har ikkala fayl yakka oʻzi, tashqi lugʻatlarsiz
ishlashi kerak) va biror tilda kalitlar yetishmasa yoki oʻrniga
qoʻyiladigan qiymatlar soni mos kelmasa, **yigʻishdan bosh tortadi** —
aks holda satr buyurtmachining oʻzida ishdan chiqardi. Yigʻishdan soʻng
har ikkala faylni shu toʻplamga nusxalang.
