# -*- coding: utf-8 -*-
"""Деинсталлятор COSMOS.

Работает в двух режимах, и это главное его свойство:

* **по журналу установки** — установщик ведёт контрольные точки
  (`%LOCALAPPDATA%\\cosmos-install\\install-*.jsonl`, на Linux
  `~/.local/state/cosmos-install/`) и записывает каждый созданный объект
  сразу после создания. Тогда снос не угадывает, а знает: удаляется
  ровно то, что записано, и ничего сверх. Журнал пишется с немедленным
  сбросом на диск, поэтому он верен и для установки, прерванной на
  середине или убитой с консоли;

* **без журнала** — установка сделана прежней версией установщика либо
  журнал утрачен. Тогда состав установки восстанавливается по признакам:
  маркер в пусковом файле, сверка дерева с системой контроля версий,
  отбор процессов по правилу. Это менее точный путь, поэтому он и
  объявляется в выводе прямо.

Режимы запуска:
    --list                  показать найденные установки и выйти
    (без ключей)            холостой прогон: все проверки, ничего не менять
    --apply                 выполнить снос

Главный принцип: не повредить ничего, что существовало до установщика.
При любой неоднозначности — остановка с показом объекта, а не догадка.
Каждый гейт печатает, сколько объектов рассмотрел: «нарушений 0» без
числа рассмотренных проверкой не считается.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import gzip
import stat as statmod
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

IS_WIN = os.name == "nt"

# Вывод — всегда UTF-8. Иначе в консоли с кодовой страницей 1252/866
# первая же русская строка валит программу исключением кодировки, и
# человек видит не сообщение, а аварию.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# ------------------------------------------------------------------- языки
# Словарь диалога — тот же, что у установщика, ключ в ключ. Встроен в
# файл: деинсталлятор приносят на площадку отдельно, и недостающий
# словарь означал бы немой вывод в решающий момент.
MESSAGES: dict[str, dict[str, str]] = {
    "cnt.changed": {"ru": "изменено_после_установки", "en": "changed_after_the_installation", "ar": "تغيّرت_بعد_التثبيت", "uz": "oʻrnatishdan_keyin_oʻzgargan", "kk": "орнатудан_кейін_өзгергені"},
    "cnt.composition": {"ru": "состав", "en": "contents", "ar": "المكوّنات", "uz": "tarkib", "kk": "құрам"},
    "cnt.described": {"ru": "в_описи", "en": "in_the_inventory", "ar": "مدرَجة_في_الجرد", "uz": "roʻyxatda", "kk": "тізімдемеде"},
    "cnt.files": {"ru": "файлов", "en": "files", "ar": "ملفات", "uz": "fayllar", "kk": "файлдар"},
    "cnt.foreign": {"ru": "постороннее", "en": "do_not_belong", "ar": "غريبة", "uz": "begona", "kk": "бөгде"},
    "cnt.image_unreadable": {"ru": "путь_образа_нечитаем", "en": "program_path_unreadable", "ar": "مسار_الملف_التنفيذي_غير_مقروء", "uz": "jarayon_fayli_yoʻli_oʻqilmadi", "kk": "образ_жолы_оқылмайды"},
    "cnt.judged": {"ru": "судимо", "en": "judged", "ar": "مصنَّفة", "uz": "baholangan", "kk": "бағаланғаны"},
    "cnt.left": {"ru": "осталось", "en": "left", "ar": "المتبقي", "uz": "qoldi", "kk": "қалғаны"},
    "cnt.mentions_path": {"ru": "упоминают_путь", "en": "mention_the_path", "ar": "تذكر_المسار", "uz": "yoʻlni_tilga_oladi", "kk": "жолды_атап_өтеді"},
    "cnt.multi_named": {"ru": "из_них_с_несколькими_именами", "en": "of_them_with_several_names", "ar": "منها_بأسماء_متعددة", "uz": "shulardan_bir_nechta_nomlisi", "kk": "олардың_ішінде_бірнеше_атаулысы"},
    "cnt.name": {"ru": "имя", "en": "name", "ar": "الاسم", "uz": "nom", "kk": "атауы"},
    "cnt.older_than_start": {"ru": "старше_начала_установки", "en": "older_than_the_installation", "ar": "أقدم_من_بدء_التثبيت", "uz": "oʻrnatish_boshlanishidan_eski", "kk": "орнату_басталғанынан_көне"},
    "cnt.ours": {"ru": "наших", "en": "ours", "ar": "تابعة_لبرنامجنا", "uz": "bizniki", "kk": "біздікілер"},
    "cnt.ours_left": {"ru": "осталось_наших", "en": "ours_left", "ar": "المتبقي_من_عملياتنا", "uz": "bizniki_qoldi", "kk": "қалған_біздікілер"},
    "cnt.product": {"ru": "продукт", "en": "product", "ar": "تابعة_للبرنامج", "uz": "mahsulot", "kk": "өнім"},
    "cnt.readonly": {"ru": "файлов_только_чтение", "en": "read_only_files", "ar": "ملفات_للقراءة_فقط", "uz": "faqat_oʻqish_uchun_fayllar", "kk": "тек_оқуға_арналған_файлдар"},
    "cnt.reparse": {"ru": "точек_повторной_обработки", "en": "reparse_points", "ar": "نقاط_إعادة_المعالجة", "uz": "qayta_ishlash_nuqtalari", "kk": "қайта_өңдеу_нүктелері"},
    "cnt.runtime": {"ru": "порождено_работой", "en": "created_while_running", "ar": "أنشأها_عمل_البرنامج", "uz": "dastur_ishidan_paydo_boʻlgan", "kk": "бағдарлама_жұмысынан_пайда_болғаны"},
    "cnt.skipped_blind": {"ru": "пропущено_слепыми_зонами", "en": "skipped_blind_zones", "ar": "متخطّاة_لتعذّر_القراءة", "uz": "koʻr_zonalarda_oʻtkazib_yuborilgan", "kk": "соқыр_аймақтар_себепті_өткізілгені"},
    "cnt.state": {"ru": "состояние", "en": "state", "ar": "الحالة", "uz": "holat", "kk": "күйі"},
    "cnt.stopped": {"ru": "остановлено", "en": "stopped", "ar": "أُوقفت", "uz": "toʻxtatildi", "kk": "тоқтатылғаны"},
    "cnt.subdirs": {"ru": "подкаталогов", "en": "subfolders", "ar": "مجلدات_فرعية", "uz": "ichki_kataloglar", "kk": "ішкі_каталогтар"},
    "gate.conditions": {"ru": "условия", "en": "conditions", "ar": "الشروط", "uz": "shartlar", "kk": "шарттар"},
    "gate.data": {"ru": "данные", "en": "data", "ar": "البيانات", "uz": "maʼlumotlar", "kk": "деректер"},
    "gate.foreign_content": {"ru": "постороннее по содержимому", "en": "items that do not belong, by content", "ar": "عناصر غريبة بحسب المحتوى", "uz": "tarkibi boʻyicha begona", "kk": "мазмұны бойынша бөгде"},
    "gate.foreign_time": {"ru": "постороннее по времени", "en": "items that do not belong, by date", "ar": "عناصر غريبة بحسب التاريخ", "uz": "vaqti boʻyicha begona", "kk": "уақыты бойынша бөгде"},
    "gate.hardlinks": {"ru": "жёсткие ссылки", "en": "files with several names", "ar": "الروابط الصلبة", "uz": "qattiq havolalar", "kk": "қатты сілтемелер"},
    "gate.links": {"ru": "ссылки", "en": "links", "ar": "الروابط", "uz": "havolalar", "kk": "сілтемелер"},
    "gate.manifest": {"ru": "сверка с описью", "en": "comparison against the inventory", "ar": "المقارنة بقائمة الجرد", "uz": "roʻyxat bilan solishtirish", "kk": "тізімдемемен салыстыру"},
    "gate.processes": {"ru": "процессы", "en": "processes", "ar": "العمليات", "uz": "jarayonlar", "kk": "процестер"},
    "gate.processes_after": {"ru": "процессы после сноса", "en": "processes after the removal", "ar": "العمليات بعد الحذف", "uz": "oʻchirishdan keyingi jarayonlar", "kk": "жоюдан кейінгі процестер"},
    "gate.rule": {"ru": "правило", "en": "firewall rule", "ar": "القاعدة", "uz": "qoida", "kk": "ереже"},
    "gate.scope_journal": {"ru": "состав по журналу", "en": "what to remove, from the log", "ar": "قائمة الحذف بحسب السجل", "uz": "jurnal boʻyicha tarkib", "kk": "журнал бойынша құрам"},
    "gate.scope_signs": {"ru": "состав по признакам", "en": "what to remove, from indirect signs", "ar": "قائمة الحذف بحسب العلامات", "uz": "belgilar boʻyicha tarkib", "kk": "белгілер бойынша құрам"},
    "gate.stop": {"ru": "остановка", "en": "stopping", "ar": "الإيقاف", "uz": "toʻxtatish", "kk": "тоқтату"},
    "gate.system_after": {"ru": "объекты системы после сноса", "en": "system objects after the removal", "ar": "عناصر النظام بعد الحذف", "uz": "oʻchirishdan keyingi tizim obyektlari", "kk": "жоюдан кейінгі жүйе нысандары"},
    "gate.task": {"ru": "задача", "en": "scheduler task", "ar": "المهمة", "uz": "vazifa", "kk": "тапсырма"},
    "inst.cancelled": {"ru": "Отменено. Ничего не изменено.", "en": "Cancelled. Nothing was changed.", "ar": "أُلغي التنفيذ. لم يُجرَ أي تغيير.", "uz": "Bekor qilindi. Hech narsa oʻzgartirilmadi.", "kk": "Бас тартылды. Ешнәрсе өзгертілген жоқ."},
    "inst.checks_done": {"ru": "проверок выполнено: %s", "en": "checks completed: %s", "ar": "الفحوص المنفَّذة: %s", "uz": "bajarilgan tekshiruvlar: %s", "kk": "орындалған тексеру саны: %s"},
    "inst.decisions_done": {"ru": "решений: %s | определено машиной: %s | спрошено: %s", "en": "decisions: %s | determined by the program: %s | asked: %s", "ar": "القرارات: %s | حدَّدها البرنامج: %s | جرى السؤال عنها: %s", "uz": "qarorlar: %s | dastur aniqladi: %s | soʻraldi: %s", "kk": "шешім саны: %s | бағдарлама анықтағаны: %s | сұралғаны: %s"},
    "inst.done": {"ru": "Готово.", "en": "Done.", "ar": "تم.", "uz": "Tayyor.", "kk": "Дайын."},
    "inst.downloaded_mb": {"ru": "… загружено %s МБ", "en": "… downloaded: %s MB", "ar": "… جرى تنزيل %s ميغابايت", "uz": "… %s MB yuklab olindi", "kk": "… жүктелгені: %s МБ"},
    "inst.downloading": {"ru": "… загрузка %s из %s МБ", "en": "… downloading: %s of %s MB", "ar": "… جرى تنزيل %s من أصل %s ميغابايت", "uz": "… yuklab olinmoqda: %s MB, jami %s MB", "kk": "… жүктелуде: %s / %s МБ"},
    "inst.heartbeat": {"ru": "… работа идёт, прошло %s", "en": "… still working, %s elapsed", "ar": "… العمل مستمر، مضى عليه %s", "uz": "… ish davom etmoqda, %s oʻtdi", "kk": "… жұмыс жүріп жатыр, өткен уақыт: %s"},
    "inst.impossible": {"ru": "УСТАНОВКА НЕВОЗМОЖНА. Препятствия:", "en": "INSTALLATION IS NOT POSSIBLE. Obstacles:", "ar": "التثبيت غير ممكن. الموانع:", "uz": "OʻRNATIB BOʻLMAYDI. Toʻsiqlar:", "kk": "ОРНАТУ МҮМКІН ЕМЕС. Кедергілер:"},
    "inst.journal": {"ru": "журнал установки (нужен для удаления): %s", "en": "installation log (needed in order to remove the program): %s", "ar": "سجل التثبيت (لازم عند إزالة البرنامج): %s", "uz": "oʻrnatish jurnali (dasturni oʻchirish uchun kerak): %s", "kk": "орнату журналы (бағдарламаны жою үшін қажет): %s"},
    "inst.not_confirmed": {"ru": "Установка НЕ подтверждена.", "en": "The installation was NOT confirmed.", "ar": "لم يجرِ تأكيد التثبيت.", "uz": "Oʻrnatish TASDIQLANMADI.", "kk": "Орнату РАСТАЛМАДЫ."},
    "inst.nothing_changed": {"ru": "Ничего не изменено.", "en": "Nothing was changed.", "ar": "لم يُجرَ أي تغيير.", "uz": "Hech narsa oʻzgartirilmadi.", "kk": "Ешнәрсе өзгертілген жоқ."},
    "inst.probe_only": {"ru": "Режим обследования: ничего не изменено.", "en": "Check-only mode: nothing was changed.", "ar": "وضع الفحص فقط: لم يُجرَ أي تغيير.", "uz": "Oʻrganish rejimi: hech narsa oʻzgartirilmadi.", "kk": "Тексеру режимі: ешнәрсе өзгертілген жоқ."},
    "inst.probes_done": {"ru": "проб выполнено: %s | препятствий: %s", "en": "probes completed: %s | obstacles: %s", "ar": "الفحوص الاستطلاعية المنفَّذة: %s | الموانع: %s", "uz": "bajarilgan sinovlar: %s | toʻsiqlar: %s", "kk": "орындалған сынақ саны: %s | кедергі саны: %s"},
    "inst.report": {"ru": "отчёт: %s", "en": "report: %s", "ar": "التقرير: %s", "uz": "hisobot: %s", "kk": "есеп: %s"},
    "inst.report_failed": {"ru": "отчёт не записан (%s) — на установку это не влияет", "en": "the report could not be written (%s) — this does not affect the installation", "ar": "تعذّر حفظ التقرير (%s) — وهذا لا يؤثر في التثبيت", "uz": "hisobot yozilmadi (%s) — bu oʻrnatishga taʼsir qilmaydi", "kk": "есеп жазылмады (%s) — бұл орнатуға әсер етпейді"},
    "inst.running": {"ru": "выполняется…", "en": "in progress…", "ar": "جارٍ التنفيذ…", "uz": "bajarilmoqda…", "kk": "орындалуда…"},
    "inst.stage.install": {"ru": "=== 3. УСТАНОВКА ===", "en": "=== 3. INSTALLATION ===", "ar": "=== 3. التثبيت ===", "uz": "=== 3. OʻRNATISH ===", "kk": "=== 3. ОРНАТУ ==="},
    "inst.stage.plan": {"ru": "=== 2. ЗАМЫСЕЛ УСТАНОВКИ ===", "en": "=== 2. INSTALLATION PLAN ===", "ar": "=== 2. خطة التثبيت ===", "uz": "=== 2. OʻRNATISH REJASI ===", "kk": "=== 2. ОРНАТУ ЖОСПАРЫ ==="},
    "inst.stage.survey": {"ru": "=== 1. ОБСЛЕДОВАНИЕ ПЛОЩАДКИ ===", "en": "=== 1. CHECKING THIS COMPUTER ===", "ar": "=== 1. فحص بيئة التشغيل ===", "uz": "=== 1. KOMPYUTERNI OʻRGANISH ===", "kk": "=== 1. КОМПЬЮТЕРДІ ТЕКСЕРУ ==="},
    "inst.stage.verify": {"ru": "=== 4. ПРОВЕРКА ===", "en": "=== 4. VERIFICATION ===", "ar": "=== 4. التحقق ===", "uz": "=== 4. TEKSHIRISH ===", "kk": "=== 4. ТЕКСЕРУ ==="},
    "inst.steps_done": {"ru": "шагов выполнено: %s из %s", "en": "steps completed: %s of %s", "ar": "الخطوات المنفَّذة: %s من أصل %s", "uz": "bajarilgan qadamlar: %s / %s", "kk": "орындалған қадам: %s / %s"},
    "inst.title": {"ru": "Установщик COSMOS", "en": "COSMOS Installer", "ar": "مثبِّت COSMOS", "uz": "COSMOS oʻrnatuvchisi", "kk": "COSMOS орнатқышы"},
    "key.created": {"ru": "Ключ создан.", "en": "The key has been created.", "ar": "جرى إنشاء المفتاح.", "uz": "Kalit yaratildi.", "kk": "Кілт жасалды."},
    "key.easy": {"ru": "Это поправимо за пару минут, ничего сложного не требуется.", "en": "This can be put right in a couple of minutes and needs nothing complicated.", "ar": "الأمر قابل للحل في دقيقتين، ولا يتطلب أي خطوات معقدة.", "uz": "Buni bir necha daqiqada toʻgʻrilash mumkin, murakkab hech narsa talab qilinmaydi.", "kk": "Мұны бірнеше минутта түзетуге болады, күрделі ештеңе қажет емес."},
    "key.failed": {"ru": "Не получилось создать ключ: %s", "en": "The key could not be created: %s", "ar": "تعذّر إنشاء المفتاح: %s", "uz": "Kalit yaratilmadi: %s", "kk": "Кілт жасау мүмкін болмады: %s"},
    "key.no_access": {"ru": "У этого компьютера пока нет доступа к исходным текстам.", "en": "This computer does not yet have access to the source code.", "ar": "لا يملك هذا الجهاز حتى الآن صلاحية الوصول إلى النصوص المصدرية للبرنامج.", "uz": "Bu kompyuterda hozircha manba matnlariga kirish huquqi yoʻq.", "kk": "Бұл компьютерге бастапқы кодтарға қолжетімділік әзірге берілмеген."},
    "key.prepare": {"ru": "Подготовить ключ доступа сейчас?", "en": "Prepare an access key now?", "ar": "هل تريد تجهيز مفتاح الوصول الآن؟", "uz": "Kirish kaliti hozir tayyorlansinmi?", "kk": "Қолжетімділік кілтін қазір дайындау керек пе?"},
    "key.send": {"ru": "Отправьте эту строку целиком тому, кто выдаёт доступ (владельцу репозитория). Он добавит её в настройках: Settings → Deploy keys → Add deploy key, без права записи.", "en": "Send this whole line to the person who grants access (the owner of the repository). They will add it in the settings: Settings → Deploy keys → Add deploy key, without write permission.", "ar": "أرسل هذا السطر كاملًا إلى الشخص الذي يمنح صلاحية الوصول (مالك مستودع النصوص المصدرية). وسيضيفه من عنده في: Settings → Deploy keys → Add deploy key، من دون صلاحية الكتابة.", "uz": "Shu satrni toʻliq holda kirish huquqini beradigan shaxsga (repozitoriy egasiga) yuboring. U uni sozlamalarga qoʻshadi: Settings → Deploy keys → Add deploy key, yozish huquqisiz.", "kk": "Осы жолды толығымен қолжетімділік беретін адамға (репозиторий иесіне) жіберіңіз. Ол оны баптауларда қосады: Settings → Deploy keys → Add deploy key, жазу құқығын бермей."},
    "key.wait": {"ru": "Когда владелец подтвердит, что добавил ключ, нажмите Enter.", "en": "When the owner confirms that the key has been added, press Enter.", "ar": "بعد أن يؤكد المالك إضافة المفتاح، اضغط Enter.", "uz": "Egasi kalitni qoʻshganini tasdiqlagach, Enter tugmasini bosing.", "kk": "Иесі кілтті қосқанын растағаннан кейін Enter басыңыз."},
    "kind.data_dir": {"ru": "каталог данных", "en": "data folder", "ar": "مجلد البيانات", "uz": "maʼlumotlar katalogi", "kk": "деректер каталогы"},
    "kind.launcher": {"ru": "пусковой файл", "en": "startup file", "ar": "ملف التشغيل", "uz": "ishga tushirish fayli", "kk": "іске қосу файлы"},
    "kind.object": {"ru": "объект", "en": "object", "ar": "عنصر", "uz": "obyekt", "kk": "нысан"},
    "kind.report": {"ru": "отчёт об установке", "en": "installation report", "ar": "تقرير التثبيت", "uz": "oʻrnatish hisoboti", "kk": "орнату есебі"},
    "kind.rules_file": {"ru": "файл правил", "en": "rules file", "ar": "ملف القواعد", "uz": "qoidalar fayli", "kk": "ережелер файлы"},
    "kind.solver_build": {"ru": "сборка решателя", "en": "build of the solver", "ar": "بناء محرّك الحل", "uz": "yechuvchi yigʻmasi", "kk": "есеп шешушінің жинағы"},
    "kind.solver_launcher": {"ru": "пусковой файл решателя", "en": "solver startup file", "ar": "ملف تشغيل محرّك الحل", "uz": "yechuvchini ishga tushirish fayli", "kk": "есеп шешушінің іске қосу файлы"},
    "kind.start_file": {"ru": "файл запуска", "en": "file for starting the program", "ar": "ملف البدء", "uz": "ishga tushiruvchi fayl", "kk": "бастау файлы"},
    "kind.target_dir": {"ru": "каталог цели", "en": "target folder", "ar": "مجلد الهدف", "uz": "nishon katalog", "kk": "нысана каталогы"},
    "kind.toolchain": {"ru": "набор инструментов", "en": "set of tools", "ar": "مجموعة الأدوات", "uz": "asboblar toʻplami", "kk": "құралдар жиынтығы"},
    "kind.venv": {"ru": "окружение python", "en": "python environment", "ar": "بيئة python", "uz": "python muhiti", "kk": "python ортасы"},
    "mark.no": {"ru": "нет ", "en": "no  ", "ar": "غياب", "uz": "yoʻq", "kk": "жоқ "},
    "mark.ok": {"ru": "OK  ", "en": "OK  ", "ar": "سليم", "uz": "OK  ", "kk": "OK  "},
    "mark.stop": {"ru": "СТОП", "en": "STOP", "ar": "توقّف", "uz": "STOP", "kk": "СТОП"},
    "plan.access_aud": {"ru": "аудитория Access", "en": "Access audience", "ar": "جمهور Access", "uz": "Access auditoriyasi", "kk": "Access аудиториясы"},
    "plan.access_team": {"ru": "команда Access", "en": "Access team", "ar": "فريق Access", "uz": "Access jamoasi", "kk": "Access тобы"},
    "plan.api_key": {"ru": "ключ облачной модели", "en": "key for the cloud model", "ar": "مفتاح النموذج السحابي", "uz": "bulutli model kaliti", "kk": "бұлттағы модельдің кілті"},
    "plan.auth": {"ru": "режим входа", "en": "way of signing in", "ar": "طريقة الدخول", "uz": "kirish usuli", "kk": "кіру тәсілі"},
    "plan.autostart": {"ru": "автозапуск", "en": "automatic start", "ar": "التشغيل التلقائي", "uz": "avtomatik ishga tushirish", "kk": "өздігінен іске қосылу"},
    "plan.build_solver": {"ru": "сборка решателя", "en": "building the solver", "ar": "بناء محرّك الحل", "uz": "yechuvchini yigʻish", "kk": "есеп шешушіні жинау"},
    "plan.data_scope": {"ru": "область данных", "en": "data policy", "ar": "نطاق البيانات", "uz": "maʼlumotlar sohasi", "kk": "деректер аймағы"},
    "plan.dir": {"ru": "каталог", "en": "folder", "ar": "المجلد", "uz": "katalog", "kk": "каталог"},
    "plan.lang": {"ru": "язык интерфейса", "en": "language of the interface", "ar": "لغة الواجهة", "uz": "interfeys tili", "kk": "интерфейс тілі"},
    "plan.local_model": {"ru": "локальная модель", "en": "local model", "ar": "النموذج المحلي", "uz": "mahalliy model", "kk": "жергілікті модель"},
    "plan.model_default": {"ru": "выбор модели по умолчанию", "en": "model chosen by default", "ar": "النموذج المختار افتراضيًا", "uz": "standart model tanlovi", "kk": "әдепкі модель таңдауы"},
    "plan.net": {"ru": "доступ из сети", "en": "access from the network", "ar": "الوصول من الشبكة", "uz": "tarmoqdan kirish", "kk": "желіден қолжетімділік"},
    "plan.ollama_url": {"ru": "адрес Ollama", "en": "address of Ollama", "ar": "عنوان Ollama", "uz": "Ollama manzili", "kk": "Ollama мекенжайы"},
    "plan.port": {"ru": "порт", "en": "port", "ar": "المنفذ", "uz": "port", "kk": "порт"},
    "plan.secret_set": {"ru": "(задан)", "en": "(set)", "ar": "(محدَّد)", "uz": "(kiritilgan)", "kk": "(көрсетілген)"},
    "plan.secret_unset": {"ru": "(не задан)", "en": "(not set)", "ar": "(غير محدَّد)", "uz": "(kiritilmagan)", "kk": "(көрсетілмеген)"},
    "plan.site": {"ru": "площадка", "en": "platform", "ar": "بيئة التشغيل", "uz": "tizim", "kk": "компьютер"},
    "plan.sources": {"ru": "доступ к исходникам", "en": "access to the source code", "ar": "الوصول إلى النصوص المصدرية", "uz": "manba matnlariga kirish", "kk": "бастапқы кодтарға қолжетімділік"},
    "plan.super_admin": {"ru": "супер-администратор", "en": "super administrator", "ar": "المسؤول الأعلى عن النظام", "uz": "super-administrator", "kk": "супер-әкімші"},
    "plan.toolchain": {"ru": "установка JDK и Maven", "en": "installation of JDK and Maven", "ar": "تثبيت JDK وMaven", "uz": "JDK va Maven oʻrnatish", "kk": "JDK және Maven орнату"},
    "plan.val.absent": {"ru": "нет", "en": "no", "ar": "غير متوفر", "uz": "yoʻq", "kk": "жоқ"},
    "plan.val.external": {"ru": "внешний", "en": "external (cloud models allowed)", "ar": "خارجي", "uz": "tashqi", "kk": "сыртқы"},
    "plan.val.internal": {"ru": "внутренний", "en": "internal (no cloud models)", "ar": "داخلي", "uz": "ichki", "kk": "ішкі"},
    "plan.val.present": {"ru": "есть", "en": "yes", "ar": "متوفر", "uz": "bor", "kk": "бар"},
    "plan.workers": {"ru": "потоков решателя", "en": "solver threads", "ar": "مسارات محرّك الحل", "uz": "yechuvchi oqimlari", "kk": "есеп шешушінің ағын саны"},
    "probe.admin": {"ru": "права администратора", "en": "administrator rights", "ar": "صلاحيات المسؤول", "uz": "administrator huquqlari", "kk": "әкімші құқығы"},
    "probe.admin.no": {"ru": "нет — автозапуск потребует их", "en": "no — automatic start will need them", "ar": "غير متوفرة — والتشغيل التلقائي يحتاج إليها", "uz": "yoʻq — avtomatik ishga tushirish uchun ular kerak boʻladi", "kk": "жоқ — өздігінен іске қосылу үшін ол қажет болады"},
    "probe.admin.yes": {"ru": "есть", "en": "yes", "ar": "متوفرة", "uz": "bor", "kk": "бар"},
    "probe.capacity": {"ru": "вычислительный ресурс", "en": "computing power", "ar": "الموارد الحاسوبية", "uz": "hisoblash resursi", "kk": "есептеу ресурсы"},
    "probe.capacity.ok": {"ru": "ядер %s → потоков решателя %s", "en": "cores %s → solver threads %s", "ar": "الأنوية: %s، مسارات محرّك الحل: %s", "uz": "yadrolar: %s → yechuvchi oqimlari: %s", "kk": "ядро саны %s → есеп шешушінің ағын саны %s"},
    "probe.capacity.ram": {"ru": "ядер %s, память %s ГБ → потоков решателя %s", "en": "cores %s, memory %s GB → solver threads %s", "ar": "الأنوية: %s، الذاكرة: %s غيغابايت، مسارات محرّك الحل: %s", "uz": "yadrolar: %s, xotira: %s GB → yechuvchi oqimlari: %s", "kk": "ядро саны %s, жады %s ГБ → есеп шешушінің ағын саны %s"},
    "probe.cloudflared": {"ru": "cloudflared", "en": "cloudflared", "ar": "cloudflared", "uz": "cloudflared", "kk": "cloudflared"},
    "probe.cloudflared.absent": {"ru": "не найден — вход будет локальным", "en": "not found — signing in will be local", "ar": "غير موجود — وسيكون الدخول محليًا", "uz": "topilmadi — kirish mahalliy boʻladi", "kk": "табылмады — кіру жергілікті болады"},
    "probe.cloudflared.ok": {"ru": "есть — доступен вход через Cloudflare Access", "en": "present — signing in through Cloudflare Access is possible", "ar": "موجود — والدخول عبر Cloudflare Access متاح", "uz": "bor — Cloudflare Access orqali kirish mumkin", "kk": "бар — Cloudflare Access арқылы кіруге болады"},
    "probe.cplex": {"ru": "CPLEX", "en": "CPLEX", "ar": "CPLEX", "uz": "CPLEX", "kk": "CPLEX"},
    "probe.cplex.absent": {"ru": "не найден — расчёт на CP-SAT (движок по умолчанию); CPLEX установщик не ставит", "en": "not found — the calculation runs on CP-SAT (the default engine); the installer does not install CPLEX", "ar": "غير موجود — يجري الحساب على CP-SAT (المحرّك الافتراضي)؛ لا يثبّت المثبّت CPLEX", "uz": "topilmadi — hisob CP-SAT da (sukut boʻyicha dvigatel); oʻrnatuvchi CPLEX ni oʻrnatmaydi", "kk": "табылмады — есеп CP-SAT-та (әдепкі қозғалтқыш); орнатушы CPLEX орнатпайды"},
    "probe.cplex.found": {"ru": "найден: %s — движок cplex доступен; по умолчанию расчёт идёт на CP-SAT", "en": "found: %s — the cplex engine is available; by default the calculation runs on CP-SAT", "ar": "موجود: %s — محرّك cplex متاح؛ يجري الحساب افتراضيًا على CP-SAT", "uz": "topildi: %s — cplex dvigateli mavjud; sukut boʻyicha hisob CP-SAT da", "kk": "табылды: %s — cplex қозғалтқышы қолжетімді; әдепкі бойынша есеп CP-SAT-та"},
    "probe.disk": {"ru": "свободное место", "en": "free disk space", "ar": "المساحة الحرة على القرص", "uz": "boʻsh joy", "kk": "бос орын"},
    "probe.disk.low": {"ru": "%s ГБ — нужно не меньше %s ГБ", "en": "%s GB — at least %s GB is needed", "ar": "%s غيغابايت — والمطلوب %s غيغابايت على الأقل", "uz": "%s GB — kamida %s GB kerak", "kk": "%s ГБ — кемінде %s ГБ қажет"},
    "probe.disk.ok": {"ru": "%s ГБ", "en": "%s GB", "ar": "%s غيغابايت", "uz": "%s GB", "kk": "%s ГБ"},
    "probe.git": {"ru": "git", "en": "git", "ar": "git", "uz": "git", "kk": "git"},
    "probe.git.absent": {"ru": "не найден — исходники придётся принести вручную", "en": "not found — the source code will have to be brought over by hand", "ar": "غير موجود — وسيلزم إحضار النصوص المصدرية يدويًا", "uz": "topilmadi — manba matnlarini qoʻlda keltirishga toʻgʻri keladi", "kk": "табылмады — бастапқы кодтарды қолмен әкелуге тура келеді"},
    "probe.jdk": {"ru": "JDK", "en": "JDK", "ar": "JDK", "uz": "JDK", "kk": "JDK"},
    "probe.jdk.absent": {"ru": "не найден — этап 2 (ростеринг) работать не будет", "en": "not found — stage 2 (rostering) will not work", "ar": "غير موجود — ولن تعمل المرحلة 2 (إعداد جداول عمل الطواقم)", "uz": "topilmadi — 2-bosqich (ekipaj jadvalini tuzish) ishlamaydi", "kk": "табылмады — 2-кезең (экипаж кестесін құру) жұмыс істемейді"},
    "probe.jdk.ok": {"ru": "версия %s", "en": "version %s", "ar": "الإصدار %s", "uz": "%s versiyasi", "kk": "нұсқасы %s"},
    "probe.jdk.old": {"ru": "версия %s — требуется %s и новее", "en": "version %s — version %s or newer is needed", "ar": "الإصدار %s — والمطلوب %s أو أحدث", "uz": "%s versiyasi — %s va undan yangirogʻi kerak", "kk": "нұсқасы %s — %s және одан жаңасы қажет"},
    "probe.maven": {"ru": "Maven", "en": "Maven", "ar": "Maven", "uz": "Maven", "kk": "Maven"},
    "probe.maven.absent": {"ru": "не найден — решатель придётся собрать отдельно", "en": "not found — the solver will have to be built separately", "ar": "غير موجود — وسيلزم بناء محرّك الحل على حدة", "uz": "topilmadi — yechuvchini alohida yigʻishga toʻgʻri keladi", "kk": "табылмады — есеп шешушіні бөлек жинауға тура келеді"},
    "probe.ollama": {"ru": "локальная модель (Ollama)", "en": "local model (Ollama)", "ar": "النموذج المحلي (Ollama)", "uz": "mahalliy model (Ollama)", "kk": "жергілікті модель (Ollama)"},
    "probe.ollama.models": {"ru": "моделей: %s (%s)", "en": "models: %s (%s)", "ar": "عدد النماذج: %s (%s)", "uz": "modellar: %s (%s)", "kk": "модель саны: %s (%s)"},
    "probe.ollama.no_models": {"ru": "сервер есть, моделей нет", "en": "the server is there, but it has no models", "ar": "الخادم موجود ولا توجد نماذج", "uz": "server bor, model yoʻq", "kk": "сервер бар, модель жоқ"},
    "probe.ollama.silent": {"ru": "не отвечает на %s — подсказки ИИ будут отключены", "en": "no answer at %s — artificial intelligence suggestions will be switched off", "ar": "لا يستجيب على %s — وستُعطَّل اقتراحات الذكاء الاصطناعي", "uz": "%s manzilida javob bermayapti — sunʼiy intellekt tavsiyalari oʻchiriladi", "kk": "%s мекенжайы бойынша жауап бермейді — жасанды интеллект кеңестері өшіріледі"},
    "probe.os": {"ru": "операционная система", "en": "operating system", "ar": "نظام التشغيل", "uz": "operatsion tizim", "kk": "операциялық жүйе"},
    "probe.os.unproven": {"ru": "%s  — площадка проверена меньше", "en": "%s  — this platform has been tested less", "ar": "%s  — هذه البيئة خضعت لاختبارات أقل", "uz": "%s  — bu tizim kamroq tekshirilgan", "kk": "%s  — мұндай жүйеде тексеру аз жүргізілген"},
    "probe.ports_busy": {"ru": "занятые порты COSMOS", "en": "ports already taken by COSMOS", "ar": "منافذ COSMOS المشغولة", "uz": "band COSMOS portlari", "kk": "COSMOS алып тұрған порттар"},
    "probe.ports_busy.busy": {"ru": "заняты: %s", "en": "taken: %s", "ar": "المشغول: %s", "uz": "band: %s", "kk": "бос емес: %s"},
    "probe.ports_busy.none": {"ru": "занятых нет", "en": "none are taken", "ar": "لا يوجد منفذ مشغول", "uz": "band port yoʻq", "kk": "бос емесі жоқ"},
    "probe.ports_free": {"ru": "свободные порты", "en": "free ports", "ar": "المنافذ المتاحة", "uz": "boʻsh portlar", "kk": "бос порттар"},
    "probe.ports_free.none": {"ru": "в диапазоне %s-%s свободных нет", "en": "there are no free ports in the range %s-%s", "ar": "لا يوجد منفذ متاح في المدى %s-%s", "uz": "%s-%s oraligʻida boʻsh port yoʻq", "kk": "%s-%s аралығында бос порт жоқ"},
    "probe.ports_free.ok": {"ru": "свободны: %s", "en": "free: %s", "ar": "المتاح: %s", "uz": "boʻsh: %s", "kk": "бос: %s"},
    "probe.pypi": {"ru": "доступ к PyPI", "en": "access to PyPI", "ar": "الوصول إلى PyPI", "uz": "PyPI ga kirish", "kk": "PyPI-ге қолжетімділік"},
    "probe.pypi.fail": {"ru": "недоступен — зависимости не поставить (%s)", "en": "not reachable — the dependencies cannot be installed (%s)", "ar": "غير متاح — ولا يمكن تثبيت الحزم البرمجية المطلوبة (%s)", "uz": "mavjud emas — bogʻliq kutubxonalarni oʻrnatib boʻlmaydi (%s)", "kk": "қолжетімсіз — қажетті кітапханалар орнатылмайды (%s)"},
    "probe.pypi.ok": {"ru": "отвечает (%s)", "en": "answers (%s)", "ar": "يستجيب (%s)", "uz": "javob bermoqda (%s)", "kk": "жауап береді (%s)"},
    "probe.python": {"ru": "python", "en": "python", "ar": "python", "uz": "python", "kk": "python"},
    "probe.python.bad": {"ru": "%s НЕ ГОДИТСЯ (требуется >=%s,<%s)", "en": "%s IS NOT SUITABLE (needed: >=%s,<%s)", "ar": "%s غير صالح (المطلوب >=%s,<%s)", "uz": "%s MOS EMAS (kerak: >=%s,<%s)", "kk": "%s ЖАРАМАЙДЫ (қажеті >=%s,<%s)"},
    "probe.python.ok": {"ru": "%s годится (требуется >=%s,<%s)", "en": "%s is suitable (needed: >=%s,<%s)", "ar": "%s صالح (المطلوب >=%s,<%s)", "uz": "%s mos keladi (kerak: >=%s,<%s)", "kk": "%s жарайды (қажеті >=%s,<%s)"},
    "probe.repo": {"ru": "доступ к исходникам", "en": "access to the source code", "ar": "الوصول إلى النصوص المصدرية", "uz": "manba matnlariga kirish", "kk": "бастапқы кодтарға қолжетімділік"},
    "probe.repo.denied": {"ru": "нет доступа", "en": "no access", "ar": "لا يوجد وصول", "uz": "kirish huquqi yoʻq", "kk": "қолжетімділік жоқ"},
    "probe.repo.need_key": {"ru": "нужен доступ этой машины к приватному репозиторию: добавьте её открытый ключ SSH ключом развёртывания (Settings → Deploy keys) и укажите адрес вида git@github.com:<владелец>/<репозиторий>.git", "en": "this computer needs access to the private repository: add its public SSH key as a deploy key (Settings → Deploy keys) and give an address of the form git@github.com:<owner>/<repository>.git", "ar": "يحتاج هذا الجهاز إلى وصول إلى المستودع الخاص: أضف مفتاحه العام SSH بوصفه مفتاح نشر (Settings → Deploy keys) وحدّد عنوانًا بالشكل git@github.com:<المالك>/<المستودع>.git", "uz": "bu kompyuterga yopiq repozitoriyga kirish huquqi kerak: uning ochiq SSH kalitini joylashtirish kaliti sifatida qoʻshing (Settings → Deploy keys) va git@github.com:<egasi>/<repozitoriy>.git koʻrinishidagi manzilni koʻrsating", "kk": "бұл компьютерге жабық репозиторийге қолжетімділік қажет: оның ашық SSH кілтін орналастыру кілті ретінде қосыңыз (Settings → Deploy keys) және git@github.com:<иесі>/<репозиторий>.git түріндегі мекенжайды көрсетіңіз"},
    "probe.repo.no_git": {"ru": "git не найден — проверить доступ нечем", "en": "git was not found — there is nothing to check the access with", "ar": "git غير موجود — ولا وسيلة للتحقق من الوصول", "uz": "git topilmadi — kirish huquqini tekshirish imkoni yoʻq", "kk": "git табылмады — қолжетімділікті тексеретін құрал жоқ"},
    "probe.repo.no_helper": {"ru": "помощник учётных данных недоступен в этом сеансе (нет интерактивного входа) — вход через браузер тут не сработает, нужен ключ SSH", "en": "the credential helper is not available in this session (there is no interactive sign-in) — signing in through a browser will not work here, an SSH key is needed", "ar": "مساعد بيانات الاعتماد غير متاح في هذه الجلسة (لا يوجد دخول تفاعلي) — والدخول عبر المتصفح لن ينفع هنا، بل يلزم مفتاح SSH", "uz": "hisob maʼlumotlari yordamchisi bu seansda mavjud emas (interaktiv kirish yoʻq) — brauzer orqali kirish bu yerda ishlamaydi, SSH kaliti kerak", "kk": "тіркелгі деректерінің көмекшісі бұл сеансте қолжетімсіз (интерактивті кіру жоқ) — браузер арқылы кіру бұл жерде жұмыс істемейді, SSH кілті қажет"},
    "probe.repo.not_found": {"ru": "репозиторий не найден по этому адресу", "en": "no repository was found at this address", "ar": "لم يُعثر على مستودع على هذا العنوان", "uz": "bu manzilda repozitoriy topilmadi", "kk": "бұл мекенжай бойынша репозиторий табылмады"},
    "probe.repo.ok": {"ru": "репозиторий читается — клонирование пройдёт", "en": "the repository can be read — cloning will work", "ar": "المستودع مقروء — والنسخ سينجح", "uz": "repozitoriy oʻqilmoqda — nusxa olish muvaffaqiyatli boʻladi", "kk": "репозиторий оқылады — көшіріп алу сәтті өтеді"},
    "probe.target": {"ru": "каталог установки", "en": "installation folder", "ar": "مجلد التثبيت", "uz": "oʻrnatish katalogi", "kk": "орнату каталогы"},
    "probe.target.busy": {"ru": "%s — каталог НЕ ПУСТ (будет обновление, не установка с нуля)", "en": "%s — the folder IS NOT EMPTY (this will be an update, not a fresh installation)", "ar": "%s — المجلد ليس فارغًا (سيجري تحديث، لا تثبيت من الصفر)", "uz": "%s — katalog BOʻSH EMAS (yangilash boʻladi, noldan oʻrnatish emas)", "kk": "%s — каталог БОС ЕМЕС (нөлден орнату емес, жаңарту болады)"},
    "probe.target.readonly": {"ru": "%s — нет права записи", "en": "%s — there is no permission to write there", "ar": "%s — لا توجد صلاحية كتابة", "uz": "%s — yozish huquqi yoʻq", "kk": "%s — жазу құқығы жоқ"},
    "probe.venv": {"ru": "модуль venv", "en": "venv module", "ar": "الوحدة venv", "uz": "venv moduli", "kk": "venv модулі"},
    "probe.venv.absent": {"ru": "нет — окружение не создать", "en": "not there — the environment cannot be created", "ar": "غير متوفرة — وبدونها لا يمكن إنشاء بيئة python", "uz": "yoʻq — muhit yaratib boʻlmaydi", "kk": "жоқ — орта жасалмайды"},
    "probe.venv.no_ensurepip": {"ru": "модуль venv есть, но без ensurepip — окружение не создать; установите пакет python3-venv (например: sudo apt install python3-venv)", "en": "the venv module is present but without ensurepip — the environment cannot be created; install the python3-venv package (for example: sudo apt install python3-venv)", "ar": "وحدة venv موجودة لكن بدون ensurepip — لا يمكن إنشاء البيئة؛ ثبّت حزمة python3-venv (مثلًا: sudo apt install python3-venv)", "uz": "venv moduli bor, lekin ensurepip yoʻq — muhit yaratib boʻlmaydi; python3-venv paketini oʻrnating (masalan: sudo apt install python3-venv)", "kk": "venv модулі бар, бірақ ensurepip жоқ — орта жасалмайды; python3-venv пакетін орнатыңыз (мысалы: sudo apt install python3-venv)"},
    "probe.venv.ok": {"ru": "есть", "en": "present", "ar": "متوفرة", "uz": "bor", "kk": "бар"},
    "q.access_aud": {"ru": "Идентификатор приложения (строка AUD). Где взять: панель Cloudflare → Zero Trust → Access → Applications → ваше приложение → вкладка Overview → поле Application Audience (AUD) Tag — скопируйте её целиком", "en": "Application identifier (the AUD string). Where to find it: Cloudflare dashboard → Zero Trust → Access → Applications → your application → Overview tab → Application Audience (AUD) Tag field — copy the whole value", "ar": "معرّف التطبيق (سلسلة AUD). طريقة الحصول عليه: افتح لوحة تحكم Cloudflare واتبع المسار Zero Trust → Access → Applications، ثم اختر تطبيقك، ثم افتح تبويب Overview وانسخ محتوى حقل Application Audience (AUD) Tag كاملًا", "uz": "Ilova identifikatori (AUD satri). Qayerdan olinadi: Cloudflare paneli → Zero Trust → Access → Applications → sizning ilovangiz → Overview boʻlimi → Application Audience (AUD) Tag maydoni — uni toʻliq nusxalab oling", "kk": "Қосымшаның идентификаторы (AUD жолы). Қайдан алуға болады: Cloudflare басқару тақтасы → Zero Trust → Access → Applications → өзіңіздің қосымшаңыз → Overview қойындысы → Application Audience (AUD) Tag өрісі — оны толығымен көшіріп алыңыз"},
    "q.access_team": {"ru": "Название вашей организации в Cloudflare — часть адреса до .cloudflareaccess.com. Если страница входа выглядит как mycompany.cloudflareaccess.com, введите: mycompany", "en": "The name of your organisation in Cloudflare — the part of the address before .cloudflareaccess.com. If your sign-in page looks like mycompany.cloudflareaccess.com, enter: mycompany", "ar": "اسم مؤسستك في Cloudflare — وهو الجزء من العنوان الذي يسبق .cloudflareaccess.com. فإذا كانت صفحة الدخول بالشكل mycompany.cloudflareaccess.com فأدخل: mycompany", "uz": "Tashkilotingizning Cloudflare tizimidagi nomi — manzilning .cloudflareaccess.com dan oldingi qismi. Agar kirish sahifasi mycompany.cloudflareaccess.com koʻrinishida boʻlsa, kiriting: mycompany", "kk": "Cloudflare жүйесіндегі ұйымыңыздың атауы — мекенжайдың .cloudflareaccess.com дегенге дейінгі бөлігі. Кіру беті mycompany.cloudflareaccess.com түрінде болса, мынаны енгізіңіз: mycompany"},
    "q.api_key": {"ru": "Ключ облачной модели (Enter — пропустить, задать позже в разделе «Установки»)", "en": "Key for the cloud model (press Enter to skip and enter it later in the Settings section)", "ar": "مفتاح الوصول إلى النموذج السحابي (اضغط Enter لتخطي هذه الخطوة وإدخال المفتاح لاحقًا في قسم «الإعدادات»)", "uz": "Bulutli model kaliti (Enter — oʻtkazib yuborish, keyinroq «Sozlamalar» boʻlimida kiritish mumkin)", "kk": "Бұлттағы модельдің кілті (Enter — өткізіп жіберу, оны кейін «Баптаулар» бөлімінде де көрсетуге болады)"},
    "q.auth": {"ru": "Как пользователи будут входить в программу?", "en": "How will people sign in to the program?", "ar": "كيف سيدخل المستخدمون إلى البرنامج؟", "uz": "Foydalanuvchilar dasturga qanday kiradi?", "kk": "Пайдаланушылар бағдарламаға қалай кіреді?"},
    "q.auth.access": {"ru": "по одноразовому коду на рабочую почту — для этого нужен аккаунт Cloudflare (бесплатного достаточно) с настроенным Zero Trust и программа, открытая наружу через их туннель; если этого нет — выбирайте пароль", "en": "with a one-time code sent to a work email address — this needs a Cloudflare account (a free one is enough) with Zero Trust set up and the program published to the outside world through their tunnel; if you do not have that, choose the password option", "ar": "برمز يُرسَل مرة واحدة إلى بريد العمل — ويتطلب ذلك حسابًا في Cloudflare (الحساب المجاني يكفي) مع إعداد Zero Trust، وإتاحة البرنامج للخارج عبر النفق الذي توفره؛ وإن لم يتوفر ذلك فاختر كلمة المرور", "uz": "ish pochtasiga yuboriladigan bir martalik kod bilan — buning uchun Zero Trust sozlangan Cloudflare hisobi (bepul tarif ham yetarli) va ularning tunneli orqali tashqariga ochilgan dastur kerak; bu yoʻq boʻlsa — parolni tanlang", "kk": "жұмыс поштасына жіберілетін бір реттік кодпен — ол үшін Cloudflare тіркелгісі (тегін нұсқасы да жеткілікті), онда бапталған Zero Trust және бағдарламаның сол қызметтің туннелі арқылы сыртқа ашылуы қажет; мұның бәрі жоқ болса — құпия сөзді таңдаңыз"},
    "q.auth.token": {"ru": "по паролю — программа сама создаст его при первом запуске и покажет вам; работает сразу, никакие внешние службы не нужны", "en": "with a password — the program creates it on first start and shows it to you; it works straight away and needs no outside services", "ar": "بكلمة مرور — ينشئها البرنامج بنفسه عند أول تشغيل ويعرضها لك؛ تعمل فورًا ولا تحتاج إلى أي خدمات خارجية", "uz": "parol bilan — dastur uni birinchi ishga tushganda oʻzi yaratadi va sizga koʻrsatadi; darhol ishlaydi, hech qanday tashqi xizmat kerak emas", "kk": "құпия сөзбен — бағдарлама оны алғаш іске қосылғанда өзі жасайды және сізге көрсетеді; бірден жұмыс істейді, ешқандай сыртқы қызмет қажет емес"},
    "q.autostart": {"ru": "Зарегистрировать автозапуск при загрузке системы (%s)?", "en": "Start the program automatically when the computer starts up (%s)?", "ar": "هل تريد تسجيل تشغيل البرنامج تلقائيًا عند إقلاع النظام (%s)؟", "uz": "Tizim yuklanganda avtomatik ishga tushirish roʻyxatga olinsinmi (%s)?", "kk": "Жүйе қосылғанда бағдарлама өздігінен іске қосылатындай етіп тіркеу керек пе (%s)?"},
    "q.autostart.task": {"ru": "задача планировщика", "en": "scheduler task", "ar": "مهمة مجدول المهام", "uz": "rejalashtiruvchi vazifasi", "kk": "жоспарлаушының тапсырмасы"},
    "q.contour": {"ru": "Разрешить ли облачные модели ИИ на этой установке?", "en": "Allow artificial intelligence models in the cloud on this installation?", "ar": "هل تسمح باستخدام نماذج الذكاء الاصطناعي السحابية (العاملة على خوادم خارجية) في هذا التثبيت؟", "uz": "Shu oʻrnatishda bulutli sunʼiy intellekt modellaridan foydalanishga ruxsat berilsinmi?", "kk": "Осы орнатылымда бұлттағы (интернет арқылы жұмыс істейтін) жасанды интеллект модельдеріне рұқсат берілсін бе?"},
    "q.contour.external": {"ru": "да — для наборов, помеченных как открытые данные; наборы заказчика (пометка «только локально», она стоит по умолчанию) в облако не попадают никогда", "en": "yes — for data sets marked as open data; customer data sets (marked \"local only\", which is the default setting) are never sent to the cloud", "ar": "نعم — لمجموعات البيانات المعلَّمة بأنها بيانات مفتوحة؛ أما بيانات العميل (المعلَّمة «محليًا فقط»، وهي العلامة الموضوعة افتراضيًا) فلا تصل إلى السحابة أبدًا", "uz": "ha — «ochiq maʼlumot» deb belgilangan toʻplamlar uchun; buyurtmachi toʻplamlari («faqat mahalliy» belgisi bilan, u standart holatda turadi) hech qachon bulutga chiqmaydi", "kk": "иә — «ашық деректер» деп белгіленген жиынтықтар үшін; тапсырыс берушінің жиынтықтары («тек жергілікті» белгісімен, ол әдепкі күйде тұрады) бұлтқа ешқашан жіберілмейді"},
    "q.contour.internal": {"ru": "нет — площадка с жёсткой политикой: любые подсказки ИИ готовит только локальная модель", "en": "no — this is a site with a strict policy: every artificial intelligence suggestion is prepared by a local model only", "ar": "لا — الجهاز خاضع لسياسة أمنية صارمة: كل اقتراحات الذكاء الاصطناعي يعدّها نموذج محلي على هذا الجهاز وحده", "uz": "yoʻq — qatʼiy siyosatli maydon: sunʼiy intellektning har qanday tavsiyasini faqat shu kompyuterdagi mahalliy model tayyorlaydi", "kk": "жоқ — қатаң саясат қолданылатын орта: жасанды интеллекттің кез келген кеңесін тек осы компьютердегі жергілікті модель дайындайды"},
    "q.go": {"ru": "Выполнить установку по этому замыслу?", "en": "Install the program according to this plan?", "ar": "هل تريد تنفيذ التثبيت وفق هذه الخطة؟", "uz": "Oʻrnatish shu reja boʻyicha bajarilsinmi?", "kk": "Осы жоспар бойынша орнату орындалсын ба?"},
    "q.install_toolchain": {"ru": "Не найдено: %s — без них этап 2 (ростеринг) работать не будет. Загрузить их с официальных источников (Adoptium и Apache), сверить контрольную сумму и распаковать В КАТАЛОГ УСТАНОВКИ? Система не меняется, права администратора не нужны, объём загрузки около 200 МБ.", "en": "Not found: %s — without these, stage 2 (rostering) will not work. Download them from the official sources (Adoptium and Apache), check the checksum and unpack them INTO THE INSTALLATION FOLDER? Your system is not changed, administrator rights are not needed, and the download is about 200 MB.", "ar": "لم يُعثر على: %s — وبدونها لن تعمل المرحلة 2 (إعداد جداول عمل الطواقم). هل تريد تنزيلها من مصادرها الرسمية (Adoptium وApache)، والتحقق من بصمتها الرقمية، وفكّ ضغطها داخل مجلد التثبيت؟ لن يطرأ أي تغيير على النظام، ولا حاجة إلى صلاحيات المسؤول، وحجم التنزيل نحو 200 ميغابايت.", "uz": "Topilmadi: %s — ularsiz 2-bosqich (ekipaj jadvalini tuzish) ishlamaydi. Ular rasmiy manbalardan (Adoptium va Apache) yuklab olinsin, nazorat yigʻindisi solishtirilsin va OʻRNATISH KATALOGIGA ochib qoʻyilsinmi? Tizim oʻzgartirilmaydi, administrator huquqlari kerak emas, yuklab olish hajmi taxminan 200 MB.", "kk": "Табылмады: %s — оларсыз 2-кезең (экипаж кестесін құру) жұмыс істемейді. Оларды ресми көздерден (Adoptium және Apache) жүктеп алып, бақылау сомасын салыстырып, ОРНАТУ КАТАЛОГЫНА тарқату керек пе? Жүйе өзгертілмейді, әкімші құқығы қажет емес, жүктелетін көлем шамамен 200 МБ."},
    "q.lang": {"ru": "Language / اللغة / Til / Тіл / Язык", "en": "Language / اللغة / Til / Тіл / Язык", "ar": "Language / اللغة / Til / Тіл / Язык", "uz": "Language / اللغة / Til / Тіл / Язык", "kk": "Language / اللغة / Til / Тіл / Язык"},
    "q.linux_ack": {"ru": "Площадка не Windows. Приложение проверено полным набором тестов на Windows; на Linux сборка отмечена как необязательная и переносимость не доказана. Продолжать?", "en": "This computer is not running Windows. The application has been checked with the full set of tests on Windows; on Linux the build is marked as optional and it has not been proven to work there. Continue?", "ar": "نظام التشغيل ليس Windows. جرى اختبار البرنامج بمجموعة الاختبارات الكاملة على Windows، أما على Linux فبناء البرنامج معدود اختياريًا ولم تثبت صلاحيته للعمل عليه. هل تريد المتابعة؟", "uz": "Bu kompyuterda Windows emas. Dastur toʻliq testlar toʻplami bilan Windows tizimida tekshirilgan; Linux uchun yigʻish majburiy emas deb belgilangan va boshqa tizimda ishlashi isbotlanmagan. Davom etilsinmi?", "kk": "Бұл компьютерде Windows емес, басқа операциялық жүйе орнатылған. Бағдарлама Windows жүйесінде сынақтардың толық жиынтығымен тексерілген; Linux жүйесінде жинақтау міндетті емес деп белгіленген және онда дұрыс жұмыс істейтіні дәлелденбеген. Жалғастыру керек пе?"},
    "q.net": {"ru": "С каких компьютеров открывать программу?", "en": "Which computers should be able to open the program?", "ar": "من أي أجهزة يمكن فتح البرنامج؟", "uz": "Dastur qaysi kompyuterlardan ochilsin?", "kk": "Бағдарламаны қай компьютерлерден ашуға болады?"},
    "q.net.lan": {"ru": "со всех компьютеров этой локальной сети (наружу, в интернет, доступа не будет)", "en": "every computer on this local network (it will not be reachable from the internet)", "ar": "من جميع أجهزة هذه الشبكة المحلية (ولن يكون متاحًا من خارجها عبر الإنترنت)", "uz": "shu lokal tarmoqdagi barcha kompyuterlardan (tashqariga, internetga chiqish boʻlmaydi)", "kk": "осы жергілікті желідегі барлық компьютерден (сыртқа, интернетке қолжетімділік болмайды)"},
    "q.net.this": {"ru": "только с этого компьютера", "en": "this computer only", "ar": "من هذا الجهاز وحده", "uz": "faqat shu kompyuterdan", "kk": "тек осы компьютерден"},
    "q.ollama_model": {"ru": "Какой локальной моделью пользоваться? От выбора зависят качество и скорость подсказок.", "en": "Which local model should be used? The quality and the speed of the suggestions depend on this choice.", "ar": "أي نموذج محلي تريد استخدامه؟ جودة الاقتراحات وسرعتها تتوقفان على هذا الاختيار.", "uz": "Qaysi mahalliy modeldan foydalanilsin? Tavsiyalarning sifati va tezligi shu tanlovga bogʻliq.", "kk": "Қай жергілікті модель пайдаланылсын? Кеңестердің сапасы мен жылдамдығы осы таңдауға байланысты."},
    "q.ollama_url": {"ru": "Локальная модель ИИ (Ollama) по обычному адресу не отвечает. Она нужна для подсказок по данным заказчика: такие наборы в облако не уходят никогда. Если сервер работает на другом компьютере или порту — введите его адрес, например http://192.168.1.50:11434. Нет такого сервера — просто нажмите Enter.", "en": "The local artificial intelligence model (Ollama) is not answering at the usual address. It is needed for suggestions about customer data: such data sets are never sent to the cloud. If the server runs on another computer or on another port, enter its address, for example http://192.168.1.50:11434. If there is no such server, just press Enter.", "ar": "نموذج الذكاء الاصطناعي المحلي (Ollama) لا يستجيب على العنوان المعتاد. وهو لازم لاقتراحات الذكاء الاصطناعي على بيانات العميل: فهذه المجموعات لا تصل إلى السحابة أبدًا. فإن كان الخادم يعمل على جهاز آخر أو على منفذ آخر فأدخل عنوانه، مثل http://192.168.1.50:11434. وإن لم يكن لديك خادم من هذا النوع فاضغط Enter فقط.", "uz": "Mahalliy sunʼiy intellekt modeli (Ollama) odatdagi manzilda javob bermayapti. U buyurtmachi maʼlumotlari boʻyicha tavsiyalar uchun kerak: bunday toʻplamlar hech qachon bulutga chiqmaydi. Agar server boshqa kompyuterda yoki boshqa portda ishlayotgan boʻlsa — uning manzilini kiriting, masalan http://192.168.1.50:11434. Bunday server boʻlmasa — shunchaki Enter tugmasini bosing.", "kk": "Жергілікті жасанды интеллект моделі (Ollama) әдеттегі мекенжай бойынша жауап бермей тұр. Ол тапсырыс берушінің деректері бойынша кеңестер беру үшін қажет: мұндай жиынтықтар бұлтқа ешқашан жіберілмейді. Егер сервер басқа компьютерде немесе басқа портта жұмыс істеп тұрса — оның мекенжайын енгізіңіз, мысалы http://192.168.1.50:11434. Ондай сервер болмаса — жай ғана Enter басыңыз."},
    "q.start_now": {"ru": "Запустить COSMOS прямо сейчас?", "en": "Start COSMOS right now?", "ar": "هل تريد تشغيل COSMOS الآن؟", "uz": "COSMOS hozir ishga tushirilsinmi?", "kk": "COSMOS қазір іске қосылсын ба?"},
    "q.super_admin": {"ru": "Адрес электронной почты супер-администратора (можно несколько через запятую; первый — супер-администратор)", "en": "Email address of the super administrator (several allowed, comma-separated; the first one is the super administrator)", "ar": "عنوان البريد الإلكتروني للمسؤول الأعلى عن النظام (يمكن إدخال عدة عناوين مفصولة بفواصل؛ الأول هو المسؤول الأعلى)", "uz": "Super-administratorning elektron pochta manzili (vergul bilan bir nechtasini kiritish mumkin; birinchisi — super-administrator)", "kk": "Супер-әкімшінің электрондық пошта мекенжайы (үтір арқылы бірнешеуін енгізуге болады; біріншісі — супер-әкімші)"},
    "q.target": {"ru": "Куда установить COSMOS? (каталог будет создан)", "en": "Where should COSMOS be installed? (the folder will be created)", "ar": "أين تريد تثبيت COSMOS؟ (سيُنشأ المجلد تلقائيًا)", "uz": "COSMOS qayerga oʻrnatilsin? (katalog yaratiladi)", "kk": "COSMOS қайда орнатылсын? (каталог жасалады)"},
    "rep.decisions": {"ru": "Решения", "en": "Decisions", "ar": "القرارات", "uz": "Qarorlar", "kk": "Шешімдер"},
    "rep.envvars": {"ru": "Дополнительные переменные окружения", "en": "Additional environment variables", "ar": "متغيرات بيئة إضافية", "uz": "Qoʻshimcha muhit oʻzgaruvchilari", "kk": "Қосымша орта айнымалылары"},
    "rep.envvars.body": {"ru": "CREW_TASK_PACKAGE — путь к пакету постановки (манифест с матрицей готовности ВМП; без него главный экран честно показывает «пакет не подключён»). CREW_VMP_REGISTRY — путь к реестру исполнителей ВМП (без него расчёты по пакету постановки остановлены гейтом соответствия). CREW_DATA_DIR — перенос каталога данных (по умолчанию — соседний каталог «<цель>-data»). CREW_CPLEX_EXE — путь к внешнему решателю CPLEX (необязателен: движок отбора cplex без него честно отказывает).", "en": "CREW_TASK_PACKAGE — path to the task package (manifest with the VMP readiness matrix; without it the main screen honestly shows \"package not connected\"). CREW_VMP_REGISTRY — path to the VMP executor registry (without it runs on a task package are stopped by the conformance gate). CREW_DATA_DIR — relocates the data directory (default is the sibling \"<target>-data\"). CREW_CPLEX_EXE — path to the external CPLEX solver (optional: the cplex selection engine refuses honestly without it).", "ar": "CREW_TASK_PACKAGE — مسار حزمة الصياغة. CREW_VMP_REGISTRY — مسار سجل منفذي VMP. CREW_DATA_DIR — نقل دليل البيانات. CREW_CPLEX_EXE — مسار CPLEX الخارجي (اختياري).", "uz": "CREW_TASK_PACKAGE — topshiriq paketi yoʻli. CREW_VMP_REGISTRY — VMP ijrochilar reyestri yoʻli. CREW_DATA_DIR — maʼlumotlar katalogini koʻchirish. CREW_CPLEX_EXE — tashqi CPLEX yoʻli (ixtiyoriy).", "kk": "CREW_TASK_PACKAGE — қойылым пакетінің жолы. CREW_VMP_REGISTRY — ВМП орындаушылар тізілімінің жолы. CREW_DATA_DIR — деректер каталогын көшіру. CREW_CPLEX_EXE — сыртқы CPLEX жолы (міндетті емес)."},
    "rep.note.unfinished": {"ru": "установка не завершена", "en": "the installation was not finished", "ar": "التثبيت غير مكتمل", "uz": "oʻrnatish yakunlanmagan", "kk": "орнату аяқталмаған"},
    "rep.platform": {"ru": "Площадка: %s, каталог `%s`.", "en": "Computer: %s, folder `%s`.", "ar": "بيئة التشغيل: %s، المجلد `%s`.", "uz": "Tizim: %s, katalog `%s`.", "kk": "Компьютер: %s, каталог `%s`."},
    "rep.secrets": {"ru": "Секреты в отчёт не записываются — только признак «задан».", "en": "Secrets are not written into the report — only the mark \"set\".", "ar": "لا تُدوَّن الأسرار في التقرير — بل تُدوَّن علامة «محدَّد» فقط.", "uz": "Maxfiy qiymatlar hisobotga yozilmaydi — faqat «kiritilgan» belgisi koʻrsatiladi.", "kk": "Құпия мәндер есепке жазылмайды — тек «көрсетілген» деген белгі ғана."},
    "rep.step.done": {"ru": "сделано — %s", "en": "done — %s", "ar": "منفَّذة — %s", "uz": "bajarildi — %s", "kk": "орындалды — %s"},
    "rep.step.failed": {"ru": "НЕ СДЕЛАНО — %s", "en": "NOT DONE — %s", "ar": "غير منفَّذة — %s", "uz": "BAJARILMADI — %s", "kk": "ОРЫНДАЛМАДЫ — %s"},
    "rep.steps": {"ru": "Шаги", "en": "Steps", "ar": "الخطوات", "uz": "Qadamlar", "kk": "Қадамдар"},
    "rep.survey": {"ru": "Обследование", "en": "Checking this computer", "ar": "فحص بيئة التشغيل", "uz": "Oʻrganish", "kk": "Компьютерді тексеру"},
    "rep.th.probe": {"ru": "Проба", "en": "Probe", "ar": "الفحص الاستطلاعي", "uz": "Sinov", "kk": "Сынақ"},
    "rep.th.result": {"ru": "Итог", "en": "Result", "ar": "النتيجة", "uz": "Natija", "kk": "Қорытынды"},
    "rep.th.source": {"ru": "Источник", "en": "Source", "ar": "المصدر", "uz": "Manba", "kk": "Дереккөз"},
    "rep.th.step": {"ru": "Шаг", "en": "Step", "ar": "الخطوة", "uz": "Qadam", "kk": "Қадам"},
    "rep.th.value": {"ru": "Значение", "en": "Value", "ar": "القيمة", "uz": "Qiymat", "kk": "Мәні"},
    "rep.th.what": {"ru": "Что", "en": "What", "ar": "البند", "uz": "Nima", "kk": "Не"},
    "rep.th.why": {"ru": "Почему", "en": "Why", "ar": "السبب", "uz": "Nega", "kk": "Себебі"},
    "rep.title": {"ru": "Отчёт об установке COSMOS", "en": "Report on the COSMOS installation", "ar": "تقرير تثبيت COSMOS", "uz": "COSMOS oʻrnatilishi haqida hisobot", "kk": "COSMOS орнату есебі"},
    "rep.verify": {"ru": "Проверка", "en": "Verification", "ar": "التحقق", "uz": "Tekshirish", "kk": "Тексеру"},
    "rep.verify.fail": {"ru": "Приложение НЕ подтверждено:", "en": "The application was NOT confirmed:", "ar": "لم يجرِ تأكيد عمل البرنامج:", "uz": "Dastur TASDIQLANMADI:", "kk": "Бағдарлама РАСТАЛМАДЫ:"},
    "rep.verify.ok": {"ru": "Приложение отвечает.", "en": "The application answers.", "ar": "البرنامج يستجيب.", "uz": "Dastur javob bermoqda.", "kk": "Бағдарлама жауап береді."},
    "res.autostart.task": {"ru": "задача планировщика %s", "en": "scheduler task %s", "ar": "مهمة مجدول المهام %s", "uz": "rejalashtiruvchi vazifasi %s", "kk": "жоспарлаушының %s тапсырмасы"},
    "res.autostart.unit": {"ru": "юнит systemd %s", "en": "systemd unit %s", "ar": "وحدة systemd %s", "uz": "systemd yuniti %s", "kk": "systemd %s юниті"},
    "res.catalog_launcher.skipped": {"ru": "пропущено: службы каталога нет в каталоге установки", "en": "skipped: the catalog service is not in the installation folder", "ar": "متخطّى: خدمة فهرس القوالب غير موجودة في مجلد التثبيت", "uz": "oʻtkazib yuborildi: shablonlar katalogi xizmati oʻrnatish katalogida yoʻq", "kk": "өткізіп жіберілді: шаблондар каталогы қызметі орнату каталогында жоқ"},
    "res.cdpm_launcher.skipped": {"ru": "пропущено: модуля данных нет в каталоге установки", "en": "skipped: the data module is not in the installation folder", "ar": "متخطّى: وحدة البيانات غير موجودة في مجلد التثبيت", "uz": "oʻtkazib yuborildi: maʼlumotlar moduli oʻrnatish katalogida yoʻq", "kk": "өткізіп жіберілді: деректер модулі орнату каталогында жоқ"},
    "res.deps.ok": {"ru": "зависимости установлены из pyproject.toml", "en": "the dependencies were installed from pyproject.toml", "ar": "جرى تثبيت الحزم البرمجية المطلوبة من pyproject.toml", "uz": "bogʻliq kutubxonalar pyproject.toml boʻyicha oʻrnatildi", "kk": "қажетті кітапханалар pyproject.toml файлы бойынша орнатылды"},
    "res.firewall.created": {"ru": "правило «%s»: входящие из местной подсети разрешены", "en": "rule \"%s\": incoming connections from the local subnet are allowed", "ar": "القاعدة «%s»: الاتصالات الواردة من الشبكة المحلية الفرعية مسموحة", "uz": "«%s» qoidasi: mahalliy quyi tarmoqdan kiruvchi ulanishlarga ruxsat berildi", "kk": "«%s» ережесі: жергілікті ішкі желіден кіріс қосылымдарға рұқсат берілді"},
    "res.firewall.linux": {"ru": "на Linux правило сетевого экрана добавьте средствами дистрибутива (например ufw allow from <подсеть> to any port %s)", "en": "on Linux, add the firewall rule with the tools of your distribution (for example ufw allow from <subnet> to any port %s)", "ar": "على Linux أضف قاعدة جدار الحماية بأدوات التوزيعة (مثال: ufw allow from <الشبكة الفرعية> to any port %s)", "uz": "Linux tizimida xavfsizlik devori qoidasini distributiv vositalari bilan qoʻshing (masalan ufw allow from <quyi tarmoq> to any port %s)", "kk": "Linux жүйесінде желіаралық қалқан ережесін дистрибутив құралдарымен қосыңыз (мысалы ufw allow from <ішкі желі> to any port %s)"},
    "res.firewall.skipped": {"ru": "пропущено: доступ только с этого компьютера", "en": "skipped: access from this computer only", "ar": "متخطّى: الوصول من هذا الجهاز وحده", "uz": "oʻtkazib yuborildi: kirish faqat shu kompyuterdan", "kk": "өткізіп жіберілді: қолжетімділік тек осы компьютерден"},
    "res.layout.created": {"ru": "создана из образца — ПРОВЕРЬТЕ модели ролей под заказчика (без раскладки облачные стадии модуля обработки данных не стартуют): %s", "en": "created from the sample — CHECK the role models against the customer's needs (without a layout the cloud stages of the data processing module do not start): %s", "ar": "أُنشئ من النسخة النموذجية — راجع نماذج الأدوار لتناسب العميل (من دون التخطيط لا تبدأ المراحل السحابية لوحدة معالجة البيانات): %s", "uz": "namunadan yaratildi — rollar modellarini buyurtmachiga moslab TEKSHIRING (joylashuvsiz maʼlumotlarni qayta ishlash modulining bulutli bosqichlari ishga tushmaydi): %s", "kk": "үлгіден жасалды — рөл модельдерін тапсырыс берушіге қарай ТЕКСЕРІҢІЗ (орналасусыз деректерді өңдеу модулінің бұлттық кезеңдері іске қосылмайды): %s"},
    "res.layout.exists": {"ru": "уже есть, оставлена без изменений: %s", "en": "already there, left unchanged: %s", "ar": "موجود سلفًا، وتُرك من دون تغيير: %s", "uz": "allaqachon bor, oʻzgarishsiz qoldirildi: %s", "kk": "бұрыннан бар, өзгеріссіз қалдырылды: %s"},
    "res.manifest.failed": {"ru": "опись не записана (%s) — удаление будет осторожнее, но сработает", "en": "the inventory could not be written (%s) — the removal will be more cautious, but it will still work", "ar": "تعذّر حفظ قائمة الجرد (%s) — وستجري الإزالة بحذر أكبر، لكنها ستنجح", "uz": "roʻyxat yozilmadi (%s) — oʻchirish ehtiyotkorroq boʻladi, lekin baribir ishlaydi", "kk": "тізімдеме жазылмады (%s) — жою неғұрлым сақтықпен жүргізіледі, бірақ орындалады"},
    "res.manifest.ok": {"ru": "описано объектов: %s → %s", "en": "objects written into the inventory: %s → %s", "ar": "عدد العناصر المدرَجة في قائمة الجرد: %s → %s", "uz": "roʻyxatga kiritilgan obyektlar: %s → %s", "kk": "тізімдемеге енгізілген нысан саны: %s → %s"},
    "res.rules.created": {"ru": "создан из образца — ПРОВЕРЬТЕ значения под заказчика: %s", "en": "created from the sample — CHECK the values against the customer's own: %s", "ar": "أُنشئ من النسخة النموذجية — راجع القيم لتناسب العميل: %s", "uz": "namunadan yaratildi — qiymatlarni buyurtmachiga moslab TEKSHIRING: %s", "kk": "үлгіден жасалды — мәндерін тапсырыс берушіге қарай ТЕКСЕРІҢІЗ: %s"},
    "res.rules.exists": {"ru": "уже есть, оставлен без изменений: %s", "en": "already there, left unchanged: %s", "ar": "موجود سلفًا، وتُرك من دون تغيير: %s", "uz": "allaqachon bor, oʻzgarishsiz qoldirildi: %s", "kk": "бұрыннан бар, өзгеріссіз қалдырылды: %s"},
    "res.skipped.not_allowed": {"ru": "пропущено: не разрешено", "en": "skipped: not allowed", "ar": "متخطّى: غير مسموح", "uz": "oʻtkazib yuborildi: ruxsat berilmadi", "kk": "өткізіп жіберілді: рұқсат берілмеген"},
    "res.solver.built": {"ru": "решатель собран", "en": "the solver has been built", "ar": "جرى بناء محرّك الحل", "uz": "yechuvchi yigʻildi", "kk": "есеп шешуші жиналды"},
    "res.solver.built_local_jdk": {"ru": "решатель собран (JDK из каталога установки)", "en": "the solver has been built (with the JDK from the installation folder)", "ar": "جرى بناء محرّك الحل (بـ JDK من مجلد التثبيت)", "uz": "yechuvchi yigʻildi (JDK oʻrnatish katalogidan)", "kk": "есеп шешуші жиналды (JDK орнату каталогынан алынды)"},
    "res.solver.skipped": {"ru": "пропущено: нет JDK 21 или Maven (этап 2 будет недоступен)", "en": "skipped: JDK 21 or Maven is missing (stage 2 will not be available)", "ar": "متخطّى: لا يوجد JDK 21 أو Maven (وستبقى المرحلة 2 غير متاحة)", "uz": "oʻtkazib yuborildi: JDK 21 yoki Maven yoʻq (2-bosqich ishlamaydi)", "kk": "өткізіп жіберілді: JDK 21 немесе Maven жоқ (2-кезең қолжетімсіз болады)"},
    "res.solver_launcher.ok": {"ru": "%s (порт %s)", "en": "%s (port %s)", "ar": "%s (المنفذ %s)", "uz": "%s (port %s)", "kk": "%s (%s порты)"},
    "res.solver_launcher.skipped": {"ru": "пропущено: решатель не собран", "en": "skipped: the solver has not been built", "ar": "متخطّى: لم يُبنَ محرّك الحل", "uz": "oʻtkazib yuborildi: yechuvchi yigʻilmagan", "kk": "өткізіп жіберілді: есеп шешуші жиналмаған"},
    "res.sources.cloned": {"ru": "клонировано из %s", "en": "cloned from %s", "ar": "جرى النسخ من %s", "uz": "%s dan nusxa olindi", "kk": "%s ішінен көшіріп алынды"},
    "res.sources.updated": {"ru": "обновлено на месте", "en": "updated in place", "ar": "جرى التحديث في مكانه", "uz": "joyida yangilandi", "kk": "орнында жаңартылды"},
    "res.toolchain.maven_nosha": {"ru": "Maven %s (сумма sha256 не опубликована)", "en": "Maven %s (no sha256 checksum has been published)", "ar": "Maven %s (بصمة sha256 غير منشورة)", "uz": "Maven %s (sha256 yigʻindisi eʼlon qilinmagan)", "kk": "Maven %s (sha256 сомасы жарияланбаған)"},
    "res.toolchain.present": {"ru": "уже установлены в %s", "en": "already installed in %s", "ar": "مثبَّتة سلفًا في %s", "uz": "allaqachon %s ichida oʻrnatilgan", "kk": "%s ішінде бұрыннан орнатылған"},
    "run.address": {"ru": "Программа откроется в браузере по адресу %s", "en": "The program will open in your browser at %s", "ar": "سيُفتح البرنامج في المتصفح على العنوان %s", "uz": "Dastur brauzerda %s manzilida ochiladi", "kk": "Бағдарлама браузерде %s мекенжайы бойынша ашылады"},
    "run.address_lan": {"ru": "С других компьютеров этой сети: %s", "en": "From other computers on this network: %s", "ar": "من أجهزة أخرى في هذه الشبكة: %s", "uz": "Shu tarmoqdagi boshqa kompyuterlardan: %s", "kk": "Осы желідегі басқа компьютерлерден: %s"},
    "run.catalog": {"ru": "Каталог шаблонов открывается отдельной службой (только просмотр): %s, адрес %s", "en": "The template catalog opens as its own service (view only): %s, address %s", "ar": "فهرس القوالب يفتح كخدمة منفصلة (للعرض فقط): %s، العنوان %s", "uz": "Shablonlar katalogi alohida xizmat sifatida ochiladi (faqat koʻrish): %s, manzil %s", "kk": "Шаблондар каталогы бөлек қызмет ретінде ашылады (тек қарау): %s, мекенжай %s"},
    "run.cdpm": {"ru": "Модуль формализации CDPM поднимается отдельной службой: %s", "en": "The CDPM formalization module runs as its own service: %s", "ar": "وحدة CDPM تعمل كخدمة منفصلة: %s", "uz": "CDPM moduli alohida xizmat sifatida ishlaydi: %s", "kk": "CDPM модулі бөлек қызмет ретінде іске қосылады: %s"},
    "run.password": {"ru": "Вход — по паролю, показанному выше.", "en": "Sign in with the password shown above.", "ar": "الدخول يكون بكلمة المرور المعروضة أعلاه.", "uz": "Kirish — yuqorida koʻrsatilgan parol bilan.", "kk": "Кіру — жоғарыда көрсетілген құпия сөз арқылы."},
    "run.section": {"ru": "КАК ЗАПУСКАТЬ ПРОГРАММУ", "en": "HOW TO START THE PROGRAM", "ar": "كيفية تشغيل البرنامج", "uz": "DASTUR QANDAY ISHGA TUSHIRILADI", "kk": "БАҒДАРЛАМАНЫ ҚАЛАЙ ІСКЕ ҚОСУ КЕРЕК"},
    "run.solver": {"ru": "Расчёт ростеринга (этап 2) требует отдельной службы: %s", "en": "Rostering (stage 2) needs a separate service of its own: %s", "ar": "إعداد جداول عمل الطواقم (المرحلة 2) يحتاج إلى خدمة منفصلة: %s", "uz": "Ekipaj jadvalini tuzish (2-bosqich) uchun alohida xizmat kerak: %s", "kk": "Экипаж кестесін құру (2-кезең) үшін бөлек қызмет қажет: %s"},
    "run.start": {"ru": "Запуск: двойной щелчок по файлу %s", "en": "To start it: double-click the file %s", "ar": "التشغيل: انقر نقرًا مزدوجًا على الملف %s", "uz": "Ishga tushirish: %s fayliga ikki marta bosing", "kk": "Іске қосу: %s файлын екі рет шертіңіз"},
    "run.start_failed": {"ru": "Программа не ответила за %s секунд. Запустите её позже файлом %s — окно запуска покажет, что случилось.", "en": "The program did not respond within %s seconds. You can start it later with the file %s — the window that opens will show what happened.", "ar": "لم يستجب البرنامج خلال %s ثانية. شغّله لاحقًا بالملف %s — وستُظهر لك نافذة التشغيل ما الذي حدث.", "uz": "Dastur %s soniya ichida javob bermadi. Uni keyinroq %s fayli orqali ishga tushiring — ishga tushirish oynasi nima boʻlganini koʻrsatadi.", "kk": "Бағдарлама %s секунд ішінде жауап бермеді. Оны кейінірек %s файлы арқылы іске қосыңыз — іске қосу терезесі не болғанын көрсетеді."},
    "run.started": {"ru": "Программа работает. Открываю её в браузере: %s", "en": "The program is running. Opening it in your browser: %s", "ar": "البرنامج يعمل. وجارٍ فتحه في المتصفح على العنوان: %s", "uz": "Dastur ishlayapti. U brauzerda ochilmoqda: %s", "kk": "Бағдарлама жұмыс істеп тұр. Оны браузерде ашып жатырмын: %s"},
    "run.starting": {"ru": "Запускаю программу и жду отклика…", "en": "Starting the program and waiting for it to respond…", "ar": "جارٍ تشغيل البرنامج وانتظار استجابته…", "uz": "Dastur ishga tushirilmoqda, javobi kutilmoqda…", "kk": "Бағдарлама іске қосылуда, жауабы күтілуде…"},
    "run.stop": {"ru": "Остановка: закрыть окно, которое открылось при запуске.", "en": "To stop it: close the window that opened when you started the program.", "ar": "الإيقاف: أغلق النافذة التي فُتحت عند التشغيل.", "uz": "Toʻxtatish: ishga tushirishda ochilgan oynani yoping.", "kk": "Тоқтату: іске қосқанда ашылған терезені жабыңыз."},
    "run.uninstall": {"ru": "Удаление программы: двойной щелчок по UNINSTALL-COSMOS.cmd либо запуск %s", "en": "To remove the program: double-click UNINSTALL-COSMOS.cmd, or run %s", "ar": "إزالة البرنامج: انقر نقرًا مزدوجًا على UNINSTALL-COSMOS.cmd، أو شغّل الملف %s", "uz": "Dasturni oʻchirish: UNINSTALL-COSMOS.cmd fayliga ikki marta bosing yoki %s faylini ishga tushiring", "kk": "Бағдарламаны жою: UNINSTALL-COSMOS.cmd файлын екі рет шертіңіз немесе %s файлын іске қосыңыз"},
    "src.asked": {"ru": "спрошено", "en": "asked", "ar": "جرى السؤال عنه", "uz": "soʻraldi", "kk": "сұралды"},
    "src.auto": {"ru": "определено", "en": "determined by the program", "ar": "حدَّده البرنامج", "uz": "dastur aniqladi", "kk": "анықталды"},
    "src.default": {"ru": "умолчание", "en": "default", "ar": "قيمة افتراضية", "uz": "standart qiymat", "kk": "әдепкі"},
    "src.flag": {"ru": "задан ключом", "en": "given as a command-line option", "ar": "محدَّد بمفتاح سطر الأوامر", "uz": "buyruq kaliti bilan berilgan", "kk": "кілтпен берілген"},
    "step.autostart": {"ru": "автозапуск", "en": "automatic start", "ar": "التشغيل التلقائي", "uz": "avtomatik ishga tushirish", "kk": "өздігінен іске қосылу"},
    "step.catalog_launcher": {"ru": "пусковой файл службы каталога", "en": "catalog service startup file", "ar": "ملف تشغيل خدمة فهرس القوالب", "uz": "shablonlar katalogi xizmatini ishga tushirish fayli", "kk": "шаблондар каталогы қызметінің іске қосу файлы"},
    "step.cdpm_launcher": {"ru": "пусковой файл модуля данных", "en": "data module startup file", "ar": "ملف تشغيل وحدة البيانات", "uz": "maʼlumotlar modulini ishga tushirish fayli", "kk": "деректер модулінің іске қосу файлы"},
    "step.data": {"ru": "каталог данных", "en": "data folder", "ar": "مجلد البيانات", "uz": "maʼlumotlar katalogi", "kk": "деректер каталогы"},
    "step.deps": {"ru": "зависимости", "en": "dependencies", "ar": "الحزم البرمجية المطلوبة", "uz": "bogʻliq kutubxonalar", "kk": "қажетті кітапханалар"},
    "step.firewall": {"ru": "сетевой экран", "en": "firewall", "ar": "جدار الحماية", "uz": "xavfsizlik devori", "kk": "желіаралық қалқан"},
    "step.launcher": {"ru": "пусковой файл", "en": "startup file", "ar": "ملف تشغيل البرنامج", "uz": "ishga tushirish fayli", "kk": "іске қосу файлы"},
    "step.layout": {"ru": "раскладка моделей ролей из образца", "en": "role model layout created from the sample", "ar": "تخطيط نماذج الأدوار المأخوذ من النسخة النموذجية", "uz": "namunadan olingan rollar modellari joylashuvi", "kk": "үлгіден жасалған рөл модельдерінің орналасуы"},
    "step.manifest": {"ru": "опись установленного", "en": "inventory of what was installed", "ar": "قائمة جرد بما جرى تثبيته", "uz": "oʻrnatilganlar roʻyxati", "kk": "орнатылғандардың тізімдемесі"},
    "step.rules": {"ru": "файл правил из образца", "en": "rules file created from the sample", "ar": "ملف القواعد المأخوذ من النسخة النموذجية", "uz": "namunadan olingan qoidalar fayli", "kk": "үлгіден жасалған ережелер файлы"},
    "step.solver": {"ru": "решатель этапа 2", "en": "stage 2 solver", "ar": "محرّك الحل الخاص بالمرحلة 2", "uz": "2-bosqich yechuvchisi", "kk": "2-кезеңнің есеп шешушісі"},
    "step.solver_launcher": {"ru": "пусковой файл решателя", "en": "solver startup file", "ar": "ملف تشغيل محرّك الحل", "uz": "yechuvchini ishga tushirish fayli", "kk": "есеп шешушінің іске қосу файлы"},
    "step.sources": {"ru": "исходные тексты", "en": "source code", "ar": "النصوص المصدرية للبرنامج", "uz": "dasturning manba matnlari", "kk": "бастапқы кодтар"},
    "step.toolchain": {"ru": "JDK и Maven", "en": "JDK and Maven", "ar": "JDK وMaven", "uz": "JDK va Maven", "kk": "JDK және Maven"},
    "step.venv": {"ru": "окружение python", "en": "python environment", "ar": "بيئة python", "uz": "python muhiti", "kk": "python ортасы"},
    "stop.catalog_queues": {"ru": "перенесите очереди заявок каталога шаблонов: %s — их создаёт работа программ, а не установка, и удалить их вслепую нельзя", "en": "please move the template catalog request queues elsewhere: %s — they are created by the programs at work, not by the installation, and they must not be removed blind", "ar": "انقل قوائم انتظار طلبات فهرس القوالب إلى مكان آخر: %s — فهي من إنشاء عمل البرامج لا من التثبيت، ولا يجوز حذفها من دون علم", "uz": "shablonlar katalogi arizalari navbatlarini boshqa joyga koʻchiring: %s — ularni oʻrnatish emas, dasturlarning ishi yaratadi, koʻr-koʻrona oʻchirib boʻlmaydi", "kk": "шаблондар каталогы өтінімдерінің кезектерін басқа жерге ауыстырыңыз: %s — оларды орнату емес, бағдарламалардың жұмысы жасайды, оларды тексерусіз жоюға болмайды"},
    "stop.data_not_empty": {"ru": "в каталоге данных есть содержимое — это данные владельца", "en": "the data folder is not empty — this is the owner's data", "ar": "مجلد البيانات ليس فارغًا — وما فيه بيانات تخص المالك", "uz": "maʼlumotlar katalogida tarkib bor — bu egasining maʼlumotlari", "kk": "деректер каталогында мазмұн бар — бұл иесінің деректері"},
    "stop.empty_answer": {"ru": "пустой ответ", "en": "empty answer", "ar": "استجابة فارغة", "uz": "javob boʻsh", "kk": "бос жауап"},
    "stop.enum_error": {"ru": "ошибка перечисления дерева (%s шт.)", "en": "error while listing the folder tree (%s items)", "ar": "خطأ أثناء قراءة شجرة الملفات (عدد الحالات %s)", "uz": "katalog daraxtini sanab chiqishda xatolik (%s ta)", "kk": "каталогтар ағашын тізіп шығу қатесі (%s дана)"},
    "stop.enum_error_path": {"ru": "ошибка перечисления %s (%s шт.)", "en": "error while listing %s (%s items)", "ar": "خطأ أثناء قراءة %s (عدد الحالات %s)", "uz": "%s ni sanab chiqishda xatolik (%s ta)", "kk": "%s тізіп шығу қатесі (%s дана)"},
    "stop.foreign": {"ru": "в каталоге установки найдено постороннее (%s шт.): владелец мог ПЕРЕМЕСТИТЬ сюда свой файл", "en": "items that do not belong to the installation were found in its folder (%s of them): the owner may have MOVED a file of their own here", "ar": "عُثر داخل مجلد التثبيت على عناصر غريبة عنه (عددها %s): ربما نقل المالك ملفًا خاصًا به إلى هنا", "uz": "oʻrnatish katalogida begona narsalar topildi (%s ta): egasi bu yerga oʻz faylini KOʻCHIRGAN boʻlishi mumkin", "kk": "орнату каталогынан бөгде нысандар табылды (%s дана): иесі өз файлын осында АУЫСТЫРЫП САЛҒАН болуы мүмкін"},
    "stop.fw_state_unknown": {"ru": "не удалось выяснить, существует ли правило «%s»: %s", "en": "it could not be established whether the firewall rule \"%s\" exists: %s", "ar": "تعذّر التحقق مما إذا كانت القاعدة «%s» موجودة: %s", "uz": "«%s» qoidasi mavjudligini aniqlab boʻlmadi: %s", "kk": "«%s» ережесінің бар-жоғын анықтау мүмкін болмады: %s"},
    "stop.hardlinks": {"ru": "файлы «только чтение» с несколькими именами (%s шт.): удаление обезоружило бы чужой оригинал", "en": "read-only files that have more than one name (%s of them): removing them would ruin someone else's original file", "ar": "ملفات للقراءة فقط لها أكثر من اسم (عددها %s): حذفها سيتلف نسخة أصلية تخص جهة أخرى", "uz": "bir nechta nomga ega «faqat oʻqish uchun» fayllar (%s ta): oʻchirish begona asl nusxani himoyasiz qoldirardi", "kk": "бірнеше атауы бар «тек оқуға арналған» файлдар (%s дана): жойылса, бөгде түпнұсқа зақымданар еді"},
    "stop.incomplete": {"ru": "удаление неполное: %s ещё существует (объектов: %s)", "en": "the removal is not complete: %s still exists (objects: %s)", "ar": "الحذف غير مكتمل: %s ما زال موجودًا (عدد العناصر: %s)", "uz": "oʻchirish toʻliq emas: %s hali ham mavjud (obyektlar soni: %s)", "kk": "жою толық емес: %s әлі бар (нысан саны: %s)"},
    "stop.links": {"ru": "найдены ссылки (%s шт.): их ставил человек, разбирать их назначение имеет право только он", "en": "links were found (%s of them): a person created them, and only that person has the right to decide what they are for", "ar": "عُثر على روابط (عددها %s): أنشأها إنسان بنفسه، ولا يحق تحديد الغرض منها إلا له", "uz": "havolalar topildi (%s ta): ularni odam qoʻygan, ularning vazifasini faqat oʻsha odam hal qilishga haqli", "kk": "сілтемелер табылды (%s дана): оларды адам жасаған, олардың не үшін керегін тек сол адам шеше алады"},
    "stop.need_admin": {"ru": "для этого шага нужны права администратора: %s", "en": "this step needs administrator rights: %s", "ar": "هذه الخطوة تتطلب صلاحيات المسؤول: %s", "uz": "bu qadam uchun administrator huquqlari kerak: %s", "kk": "бұл қадам үшін әкімші құқығы қажет: %s"},
    "stop.no_evidence": {"ru": "ни сверка по системе контроля версий, ни сверка по времени невозможны для %s — снос вслепую запрещён", "en": "for %s neither a comparison against the version control system nor a comparison by date is possible — removing it blind is not allowed", "ar": "لا المقارنة عبر نظام إدارة إصدارات النصوص المصدرية ولا المقارنة بالتواريخ ممكنة في %s — والحذف من دون دليل ممنوع", "uz": "%s uchun na versiyalarni nazorat qilish tizimi boʻyicha, na vaqt boʻyicha solishtirish mumkin — koʻr-koʻrona oʻchirish taqiqlanadi", "kk": "%s үшін нұсқаларды бақылау жүйесі бойынша да, уақыт бойынша да салыстыру мүмкін емес — тексерусіз жоюға тыйым салынған"},
    "stop.no_marker": {"ru": "в %s нет маркера установщика и журнала установки тоже нет — происхождение каталога не доказано", "en": "%s holds no installer marker and there is no installation log either — it has not been proven where this folder came from", "ar": "لا توجد في %s علامة المثبِّت، ولا يوجد سجل تثبيت — أي أن كون هذا المجلد من إنشاء المثبِّت غير مثبت", "uz": "%s ichida oʻrnatuvchining belgisi ham, oʻrnatish jurnali ham yoʻq — katalogning kelib chiqishi isbotlanmagan", "kk": "%s ішінде орнатқыштың белгісі де, орнату журналы да жоқ — каталогтың қайдан шыққаны дәлелденбеген"},
    "stop.older": {"ru": "внутри каталога есть объекты старше самой установки — их могли перенести сюда, удаление уничтожило бы оригинал", "en": "the folder contains objects older than the installation itself — they may have been moved here, and removing them would destroy the original", "ar": "داخل المجلد عناصر أقدم من التثبيت نفسه — ربما نُقلت إلى هنا، وحذفها سيتلف النسخة الأصلية", "uz": "katalog ichida oʻrnatishning oʻzidan eskiroq obyektlar bor — ular bu yerga koʻchirilgan boʻlishi mumkin, oʻchirish asl nusxani yoʻq qilardi", "kk": "каталог ішінде орнатылымның өзінен де көне нысандар бар — оларды осында ауыстырып салуы мүмкін, жойылса түпнұсқа да жоғалады"},
    "stop.own_console": {"ru": "деинсталляция запущена из пусковой консоли установки (PID %s): остановить её значило бы убить самого себя. Запустите из отдельного окна.", "en": "the removal was started from the very console that runs the installation (PID %s): stopping it would mean killing itself. Please start it from a separate window.", "ar": "أمر الإزالة شُغّل من نافذة الأوامر نفسها التي شُغّل منها البرنامج (PID %s): إيقافها يعني إيقاف الأمر نفسه. شغّله من نافذة منفصلة.", "uz": "oʻchirish oʻrnatishning ishga tushirish konsolidan boshlangan (PID %s): uni toʻxtatish oʻzini oʻzi toʻxtatish boʻlardi. Alohida oynadan ishga tushiring.", "kk": "бағдарламаны жою орнатылымның іске қосу консолінен басталған (PID %s): оны тоқтату өзін-өзі тоқтатқанмен бірдей болар еді. Бөлек терезеден іске қосыңыз."},
    "stop.pid_reused": {"ru": "PID %s принадлежит уже другому процессу", "en": "PID %s now belongs to a different process", "ar": "المعرّف PID %s يخص الآن عملية أخرى", "uz": "PID %s allaqachon boshqa jarayonga tegishli", "kk": "PID %s енді басқа процеске тиесілі"},
    "stop.postcheck": {"ru": "пост-проверка нашла расхождения", "en": "the check after removal found differences", "ar": "الفحص بعد الحذف كشف اختلافات", "uz": "yakuniy tekshiruv nomuvofiqliklar topdi", "kk": "жоюдан кейінгі тексеру сәйкессіздіктер тапты"},
    "stop.ps_failed": {"ru": "не удалось перечислить процессы: %s", "en": "the list of processes could not be obtained: %s", "ar": "تعذّر الحصول على قائمة العمليات العاملة: %s", "uz": "jarayonlarni sanab chiqib boʻlmadi: %s", "kk": "процестерді тізіп шығу мүмкін болмады: %s"},
    "stop.read_attrs_failed": {"ru": "не удалось прочитать атрибуты %s: %s", "en": "the properties of %s could not be read: %s", "ar": "تعذّرت قراءة خصائص %s: %s", "uz": "%s atributlari oʻqilmadi: %s", "kk": "%s атрибуттарын оқу мүмкін болмады: %s"},
    "stop.read_dir_failed": {"ru": "не удалось прочитать каталог %s: %s", "en": "the folder %s could not be read: %s", "ar": "تعذّرت قراءة المجلد %s: %s", "uz": "%s katalogi oʻqilmadi: %s", "kk": "%s каталогын оқу мүмкін болмады: %s"},
    "stop.read_failed": {"ru": "не удалось прочитать %s: %s", "en": "%s could not be read: %s", "ar": "تعذّرت قراءة %s: %s", "uz": "%s oʻqilmadi: %s", "kk": "%s оқу мүмкін болмады: %s"},
    "stop.task_elsewhere": {"ru": "задача %s ведёт не в %s: %s", "en": "the scheduler task %s does not lead to %s: %s", "ar": "المهمة %s لا تشير إلى %s بل إلى: %s", "uz": "%s vazifasi %s ga emas, boshqa joyga olib boradi: %s", "kk": "%s тапсырмасы %s дегенге емес, басқа жерге сілтейді: %s"},
    "stop.task_state_unknown": {"ru": "не удалось выяснить, существует ли задача «%s»: %s", "en": "it could not be established whether the scheduler task \"%s\" exists: %s", "ar": "تعذّر التحقق مما إذا كانت المهمة «%s» موجودة: %s", "uz": "«%s» vazifasi mavjudligini aniqlab boʻlmadi: %s", "kk": "«%s» тапсырмасының бар-жоғын анықтау мүмкін болмады: %s"},
    "stop.terminate_failed": {"ru": "не удалось остановить PID %s", "en": "PID %s could not be stopped", "ar": "تعذّر إيقاف PID %s", "uz": "PID %s toʻxtatilmadi", "kk": "PID %s тоқтату мүмкін болмады"},
    "ui.enter_hint": {"ru": "  <-- Enter", "en": "  <-- Enter", "ar": "  <-- Enter", "uz": "  <-- Enter", "kk": "  <-- Enter"},
    "ui.enter_number": {"ru": "Введите номер (1-%s): ", "en": "Enter a number (1-%s): ", "ar": "أدخل الرقم (1-%s): ", "uz": "Raqamni kiriting (1-%s): ", "kk": "Нөмірді енгізіңіз (1-%s): "},
    "ui.need_number": {"ru": "Нужен номер от 1 до %s (или просто Enter).", "en": "Please enter a number from 1 to %s (or just press Enter).", "ar": "المطلوب رقم من 1 إلى %s (أو اضغط Enter فقط).", "uz": "1 dan %s gacha boʻlgan raqam kerak (yoki shunchaki Enter).", "kk": "1 мен %s аралығындағы нөмір керек (немесе жай ғана Enter басыңыз)."},
    "ui.no": {"ru": "нет", "en": "no", "ar": "لا", "uz": "yoʻq", "kk": "жоқ"},
    "ui.no_input": {"ru": "ввода нет — принято умолчание", "en": "nothing was entered — the default was used", "ar": "لا يوجد إدخال — جرى اعتماد القيمة الافتراضية", "uz": "kiritilmadi — standart qiymat qabul qilindi", "kk": "енгізілген мән жоқ — әдепкі мән қабылданды"},
    "ui.press_enter": {"ru": "Нажмите Enter, чтобы закрыть окно.", "en": "Press Enter to close this window.", "ar": "اضغط Enter لإغلاق النافذة.", "uz": "Oynani yopish uchun Enter tugmasini bosing.", "kk": "Терезені жабу үшін Enter басыңыз."},
    "ui.prompt": {"ru": "> ", "en": "> ", "ar": "> ", "uz": "> ", "kk": "> "},
    "ui.yes": {"ru": "да", "en": "yes", "ar": "نعم", "uz": "ha", "kk": "иә"},
    "ui.yes_no": {"ru": "(да/нет)", "en": "(yes/no)", "ar": "(نعم/لا)", "uz": "(ha/yoʻq)", "kk": "(иә/жоқ)"},
    "un.admin": {"ru": "права администратора: %s", "en": "administrator rights: %s", "ar": "صلاحيات المسؤول: %s", "uz": "administrator huquqlari: %s", "kk": "әкімші құқығы: %s"},
    "un.already_gone": {"ru": "  (уже нет)", "en": "  (already gone)", "ar": "  (غير موجود أصلًا)", "uz": "  (allaqachon yoʻq)", "kk": "  (бұрыннан жоқ)"},
    "un.cancelled": {"ru": "Отменено. Ничего не удалено.", "en": "Cancelled. Nothing was removed.", "ar": "أُلغي التنفيذ. لم يُحذف أي شيء.", "uz": "Bekor qilindi. Hech narsa oʻchirilmadi.", "kk": "Бас тартылды. Ешнәрсе жойылған жоқ."},
    "un.confirm": {"ru": "Удалить COSMOS с этого компьютера? Действие необратимо: пароль входа и журнал программы будут уничтожены.", "en": "Remove COSMOS from this computer? This cannot be undone: the password for signing in and the program's log will be destroyed.", "ar": "هل تريد إزالة COSMOS من هذا الجهاز؟ لا يمكن التراجع عن هذا الإجراء: ستُتلَف كلمة مرور الدخول وسجل البرنامج.", "uz": "COSMOS shu kompyuterdan oʻchirilsinmi? Bu amalni ortga qaytarib boʻlmaydi: kirish paroli va dastur jurnali butunlay yoʻq qilinadi.", "kk": "COSMOS осы компьютерден жойылсын ба? Бұл әрекетті кері қайтару мүмкін емес: кіру құпия сөзі мен бағдарлама журналы біржола жойылады."},
    "un.data": {"ru": "Каталог данных: %s", "en": "Data folder: %s", "ar": "مجلد البيانات: %s", "uz": "Maʼlumotlar katalogi: %s", "kk": "Деректер каталогы: %s"},
    "un.dry_notice": {"ru": "Ничего не будет изменено. Для исполнения добавьте --apply.", "en": "Nothing will be changed. To carry the removal out for real, add --apply.", "ar": "لن يُجرى أي تغيير. وللتنفيذ الفعلي أضف --apply.", "uz": "Hech narsa oʻzgartirilmaydi. Haqiqatda bajarish uchun --apply qoʻshing.", "kk": "Ешнәрсе өзгертілмейді. Нақты орындау үшін --apply кілтін қосыңыз."},
    "un.dry_ok": {"ru": "холостой прогон: все проверки пройдены, изменений нет", "en": "trial run: all checks passed, nothing was changed", "ar": "تشغيل تجريبي: اجتيزت جميع الفحوص، ولم يطرأ أي تغيير", "uz": "sinov rejimi: barcha tekshiruvlardan oʻtildi, oʻzgarishlar yoʻq", "kk": "сынақ жүрісі: барлық тексеру сәтті өтті, өзгеріс жоқ"},
    "un.empty_left": {"ru": "удалено всё содержимое; пустой каталог %s держится системой и исчезнет по завершении программы", "en": "all the contents have been removed; the empty folder %s is still held by the system and will disappear once this removal program has finished", "ar": "حُذف كل المحتوى؛ ولا يزال النظام محتفظًا بالمجلد الفارغ %s، وسيختفي عند انتهاء برنامج الإزالة", "uz": "butun tarkib oʻchirildi; boʻsh %s katalogini tizim hozircha ushlab turibdi va u oʻchirish dasturi ishini tugatgach yoʻqoladi", "kk": "барлық мазмұны жойылды; бос %s каталогын жүйе әлі ұстап тұр, ол жою бағдарламасы аяқталған соң жоғалады"},
    "un.examined": {"ru": "рассмотрено объектов: %s", "en": "objects examined: %s", "ar": "عدد العناصر التي جرى فحصها: %s", "uz": "koʻrib chiqilgan obyektlar: %s", "kk": "қаралған нысан саны: %s"},
    "un.journal_found": {"ru": "Журнал установки: %s (%s)", "en": "Installation log: %s (%s)", "ar": "سجل التثبيت: %s (%s)", "uz": "Oʻrnatish jurnali: %s (%s)", "kk": "Орнату журналы: %s (%s)"},
    "un.journal_interrupted": {"ru": "установка была ПРЕРВАНА — сношу то, что успело создаться", "en": "the installation was INTERRUPTED — removing whatever had already been created", "ar": "التثبيت توقّف قبل اكتماله — وتجري إزالة ما أُنشئ منه فقط", "uz": "oʻrnatish TOʻXTAB QOLGAN edi — yaratilib ulgurgan narsalar oʻchirilmoqda", "kk": "орнату ҮЗІЛГЕН — жасалып үлгергеннің бәрін жоямын"},
    "un.journal_kept": {"ru": "журнал установки оставлен для истории: %s", "en": "the installation log has been kept for the record: %s", "ar": "بقي سجل التثبيت للتوثيق: %s", "uz": "oʻrnatish jurnali tarix uchun saqlab qoldirildi: %s", "kk": "орнату журналы тарих үшін қалдырылды: %s"},
    "un.journal_missing": {"ru": "Журнала установки нет — состав восстанавливается по признакам (маркер пускового файла, сверка дерева, правило отбора процессов). Это менее точный путь.", "en": "There is no installation log — what to remove is worked out from indirect signs (the marker in the startup file, a comparison of the folder tree, a rule for picking out processes). This way is less exact.", "ar": "لا يوجد سجل تثبيت — لذلك يجري استنتاج ما ينبغي حذفه من العلامات المتاحة (علامة ملف التشغيل، ومقارنة شجرة الملفات، وقاعدة اختيار العمليات العاملة). وهذه طريقة أقل دقة.", "uz": "Oʻrnatish jurnali yoʻq — tarkib belgilar boʻyicha tiklanmoqda (ishga tushirish faylidagi belgi, katalog daraxtini solishtirish, jarayonlarni tanlash qoidasi). Bu kamroq aniq yoʻl.", "kk": "Орнату журналы жоқ — жойылатындардың құрамы жанама белгілер бойынша анықталады (іске қосу файлындағы белгі, каталогтар ағашын салыстыру, процестерді іріктеу ережесі). Бұл — дәлдігі төмен жол."},
    "un.keep_preexisting": {"ru": "НЕ удаляется (существовало до установки): %s", "en": "NOT removed (existed before the installation): %s", "ar": "لن يُحذف (كان موجودًا قبل التثبيت): %s", "uz": "OʻCHIRILMAYDI (oʻrnatishdan oldin ham mavjud edi): %s", "kk": "ЖОЙЫЛМАЙДЫ (орнатуға дейін де бар болған): %s"},
    "un.keep_ssh": {"ru": "НЕ удаляется (мог быть добавлен к репозиториям): ключ %s", "en": "NOT removed (may have been added to other repositories): key %s", "ar": "لن يُحذف (يُحتمل أنه أُضيف إلى مستودعات أخرى): المفتاح %s", "uz": "OʻCHIRILMAYDI (repozitoriylarga qoʻshilgan boʻlishi mumkin): %s kaliti", "kk": "ЖОЙЫЛМАЙДЫ (репозиторийлерге қосылған болуы мүмкін): %s кілті"},
    "un.left.exists": {"ru": "%s ещё существует: %s", "en": "%s still exists: %s", "ar": "%s ما زال موجودًا: %s", "uz": "%s hali ham mavjud: %s", "kk": "%s әлі бар: %s"},
    "un.left.processes": {"ru": "остались процессы установки: %s", "en": "processes of the installation are still running: %s", "ar": "بقيت عمليات تخص هذا التثبيت: %s", "uz": "oʻrnatish jarayonlari qoldi: %s", "kk": "орнатылымның процестері қалды: %s"},
    "un.left.rule": {"ru": "правило «%s» ещё существует", "en": "the firewall rule \"%s\" still exists", "ar": "القاعدة «%s» ما زالت موجودة", "uz": "«%s» qoidasi hali ham mavjud", "kk": "«%s» ережесі әлі бар"},
    "un.left.task": {"ru": "задача %s ещё существует", "en": "the scheduler task %s still exists", "ar": "المهمة %s ما زالت موجودة", "uz": "%s vazifasi hali ham mavjud", "kk": "%s тапсырмасы әлі бар"},
    "un.list.header": {"ru": "Найденные установки (журнал → цель → состояние):", "en": "Installations found (log → target → state):", "ar": "عمليات التثبيت التي عُثر عليها (السجل، الهدف، الحالة):", "uz": "Topilgan oʻrnatishlar (jurnal → nishon katalog → holat):", "kk": "Табылған орнатылымдар (журнал → нысана → күйі):"},
    "un.list.interrupted": {"ru": "ПРЕРВАНА (не завершена)", "en": "INTERRUPTED (not finished)", "ar": "متوقّف قبل اكتماله (غير مكتمل)", "uz": "TOʻXTAB QOLGAN (yakunlanmagan)", "kk": "ҮЗІЛГЕН (аяқталмаған)"},
    "un.list.none": {"ru": "Журналов установки не найдено в %s", "en": "No installation logs were found in %s", "ar": "لم يُعثر على أي سجل تثبيت في %s", "uz": "%s ichida oʻrnatish jurnallari topilmadi", "kk": "%s ішінен орнату журналдары табылмады"},
    "un.manifest_found": {"ru": "опись установки найдена: объектов %s — слепых зон нет, сверяется каждый", "en": "the inventory of the installation was found: %s objects — there are no blind zones, every one of them is checked", "ar": "عُثر على قائمة جرد التثبيت: عدد العناصر %s — لا توجد مناطق غير مفحوصة، ويجري التحقق من كل عنصر", "uz": "oʻrnatish roʻyxati topildi: obyektlar soni %s — koʻr zonalar yoʻq, har biri solishtiriladi", "kk": "орнату тізімдемесі табылды: нысан саны %s — соқыр аймақ жоқ, әрқайсысы салыстырылады"},
    "un.manifest_missing": {"ru": "описи установки нет — часть поддеревьев принимается без пофайловой сверки", "en": "there is no inventory of the installation — parts of the folder tree are accepted without a file-by-file comparison", "ar": "لا توجد قائمة جرد للتثبيت — لذلك تُقبل أجزاء من شجرة الملفات من دون مقارنة ملفًا ملفًا", "uz": "oʻrnatish roʻyxati yoʻq — katalog daraxtining bir qismi har bir fayl boʻyicha solishtirilmasdan qabul qilinadi", "kk": "орнату тізімдемесі жоқ — каталогтар ағашының бір бөлігі әр файл бойынша салыстырылмай қабылданады"},
    "un.mark.foreign": {"ru": "! постороннее: %s", "en": "! does not belong here: %s", "ar": "! عنصر غريب: %s", "uz": "! begona: %s", "kk": "! бөгде: %s"},
    "un.mark.left": {"ru": "! осталось: %s", "en": "! left over: %s", "ar": "! متبقٍّ: %s", "uz": "! qoldi: %s", "kk": "! қалғаны: %s"},
    "un.mark.link": {"ru": "! ссылка: %s", "en": "! link: %s", "ar": "! رابط: %s", "uz": "! havola: %s", "kk": "! сілтеме: %s"},
    "un.mark.names": {"ru": "! %s — имён: %s", "en": "! %s — names: %s", "ar": "! %s — عدد الأسماء: %s", "uz": "! %s — nomlar soni: %s", "kk": "! %s — атау саны: %s"},
    "un.mark.older": {"ru": "! старше установки: %s", "en": "! older than the installation: %s", "ar": "! أقدم من التثبيت: %s", "uz": "! oʻrnatishdan eski: %s", "kk": "! орнатылымнан көне: %s"},
    "un.mentions": {"ru": "(не трогаю, лишь упоминает путь): PID %s", "en": "(left alone, it only mentions the path): PID %s", "ar": "(لن يُمَس، فهو يذكر المسار فحسب): PID %s", "uz": "(tegilmaydi, faqat yoʻlni tilga oladi): PID %s", "kk": "(тиіспеймін, тек жолды атап өтеді): PID %s"},
    "un.mode.apply": {"ru": "ИСПОЛНЕНИЕ", "en": "REAL REMOVAL", "ar": "تنفيذ فعلي", "uz": "BAJARISH", "kk": "НАҚТЫ ОРЫНДАУ"},
    "un.mode.dry": {"ru": "ХОЛОСТОЙ ПРОГОН", "en": "TRIAL RUN", "ar": "تشغيل تجريبي من دون تغيير", "uz": "SINOV REJIMI", "kk": "СЫНАҚ ЖҮРІСІ"},
    "un.nothing_to_remove": {"ru": "удалять нечего: %s отсутствует", "en": "nothing to remove: %s is not there", "ar": "لا شيء لحذفه: %s غير موجود", "uz": "oʻchiradigan narsa yoʻq: %s mavjud emas", "kk": "жоятын ештеңе жоқ: %s жоқ"},
    "un.ours": {"ru": "наш: PID %s — %s", "en": "ours: PID %s — %s", "ar": "تابع لبرنامجنا: PID %s — %s", "uz": "bizniki: PID %s — %s", "kk": "біздікі: PID %s — %s"},
    "un.pid_gone": {"ru": "PID %s уже недоступен", "en": "PID %s is no longer there", "ar": "المعرّف PID %s لم يعد متاحًا", "uz": "PID %s allaqachon mavjud emas", "kk": "PID %s енді қолжетімсіз"},
    "un.port": {"ru": "порт приложения: %s", "en": "port of the application: %s", "ar": "منفذ البرنامج: %s", "uz": "dastur porti: %s", "kk": "бағдарламаның порты: %s"},
    "un.port_unknown": {"ru": "неизвестен", "en": "not known", "ar": "غير معروف", "uz": "nomaʼlum", "kk": "белгісіз"},
    "un.removed": {"ru": "удалён: %s", "en": "removed: %s", "ar": "حُذف: %s", "uz": "oʻchirildi: %s", "kk": "жойылды: %s"},
    "un.result.done": {"ru": "ИТОГ: деинсталляция выполнена", "en": "RESULT: the removal has been carried out", "ar": "النتيجة: تمت إزالة البرنامج", "uz": "YAKUN: dastur oʻchirildi", "kk": "ҚОРЫТЫНДЫ: бағдарлама толық жойылды"},
    "un.result.dry": {"ru": "ИТОГ: проверки пройдены, можно запускать с --apply", "en": "RESULT: the checks passed, you can now run it with --apply", "ar": "النتيجة: اجتيزت الفحوص، ويمكن التشغيل مع --apply", "uz": "YAKUN: tekshiruvlardan oʻtildi, endi --apply bilan ishga tushirish mumkin", "kk": "ҚОРЫТЫНДЫ: тексерулер өтті, енді --apply кілтімен іске қосуға болады"},
    "un.rule_no_name": {"ru": "правило: имя неизвестно — шаг не выполнен", "en": "firewall rule: the name is not known — this step was not carried out", "ar": "القاعدة: الاسم غير معروف — لم تُنفَّذ هذه الخطوة", "uz": "qoida: nomi nomaʼlum — qadam bajarilmadi", "kk": "ереже: атауы белгісіз — қадам орындалмады"},
    "un.rule_preexisting": {"ru": "правило «%s» существовало ДО установки — не трогаю", "en": "the firewall rule \"%s\" existed BEFORE the installation — it is left alone", "ar": "القاعدة «%s» كانت موجودة قبل التثبيت — ولن تُمَس", "uz": "«%s» qoidasi oʻrnatishdan OLDIN ham mavjud edi — tegilmaydi", "kk": "«%s» ережесі орнатуға ДЕЙІН де бар болған — тиіспеймін"},
    "un.section.checks": {"ru": "Проверки: %s", "en": "Checks: %s", "ar": "الفحوص: %s", "uz": "Tekshiruvlar: %s", "kk": "Тексерулер: %s"},
    "un.section.conditions": {"ru": "Условия исполнения", "en": "Conditions for removal", "ar": "شروط التنفيذ", "uz": "Bajarish shartlari", "kk": "Орындау шарттары"},
    "un.section.postcheck": {"ru": "Пост-проверка", "en": "Check after removal", "ar": "الفحص بعد الحذف", "uz": "Yakuniy tekshiruv", "kk": "Жоюдан кейінгі тексеру"},
    "un.section.processes": {"ru": "Процессы установки", "en": "Processes of the installation", "ar": "عمليات البرنامج العاملة", "uz": "Oʻrnatish jarayonlari", "kk": "Орнатылымның процестері"},
    "un.section.removal": {"ru": "Удаление", "en": "Removal", "ar": "الحذف", "uz": "Oʻchirish", "kk": "Жою"},
    "un.section.scope": {"ru": "Состав удаления", "en": "What will be removed", "ar": "قائمة ما سيُحذف", "uz": "Oʻchiriladiganlar tarkibi", "kk": "Жойылатындардың құрамы"},
    "un.section.task_rule": {"ru": "Задача планировщика и правило сетевого экрана", "en": "Scheduler task and firewall rule", "ar": "مهمة مجدول المهام وقاعدة جدار الحماية", "uz": "Rejalashtiruvchi vazifasi va xavfsizlik devori qoidasi", "kk": "Жоспарлаушының тапсырмасы және желіаралық қалқан ережесі"},
    "un.stop": {"ru": "СТОП: %s", "en": "STOP: %s", "ar": "توقّف: %s", "uz": "TOʻXTATILDI: %s", "kk": "ТОҚТА: %s"},
    "un.stop_tail": {"ru": "Ничего не удалено сверх уже напечатанного. Решение за владельцем.", "en": "Nothing has been removed beyond what is printed above. The decision is up to the owner.", "ar": "لم يُحذف أي شيء زيادة على ما طُبع أعلاه. والقرار يعود إلى المالك.", "uz": "Yuqorida chop etilganidan tashqari hech narsa oʻchirilmadi. Qaror egasining ixtiyorida.", "kk": "Жоғарыда көрсетілгеннен басқа ештеңе жойылған жоқ. Шешімді иесі қабылдайды."},
    "un.stopped": {"ru": "остановлен PID %s", "en": "stopped PID %s", "ar": "أُوقف PID %s", "uz": "PID %s toʻxtatildi", "kk": "тоқтатылды: PID %s"},
    "un.systemd_unit": {"ru": "юнит systemd: %s", "en": "systemd unit: %s", "ar": "وحدة systemd: %s", "uz": "systemd yuniti: %s", "kk": "systemd юниті: %s"},
    "un.target": {"ru": "Цель: %s", "en": "Target: %s", "ar": "الهدف: %s", "uz": "Nishon katalog: %s", "kk": "Нысана: %s"},
    "un.task_no_name": {"ru": "задача: имя неизвестно — шаг не выполнен", "en": "scheduler task: the name is not known — this step was not carried out", "ar": "المهمة: الاسم غير معروف — لم تُنفَّذ هذه الخطوة", "uz": "vazifa: nomi nomaʼlum — qadam bajarilmadi", "kk": "тапсырма: атауы белгісіз — қадам орындалмады"},
    "un.title": {"ru": "Деинсталляция COSMOS", "en": "COSMOS Removal", "ar": "إزالة COSMOS", "uz": "COSMOS dasturini oʻchirish", "kk": "COSMOS бағдарламасын жою"},
    "un.unknown_port": {"ru": "порт неизвестен — шаг не выполнен", "en": "the port is not known — this step was not carried out", "ar": "المنفذ غير معروف — لم تُنفَّذ هذه الخطوة", "uz": "port nomaʼlum — qadam bajarilmadi", "kk": "порт белгісіз — қадам орындалмады"},
    "un.would_remove": {"ru": "[холостой прогон] был бы удалён %s", "en": "[trial run] this would have been removed: %s", "ar": "[تشغيل تجريبي] كان سيُحذف %s", "uz": "[sinov rejimi] %s oʻchirilgan boʻlardi", "kk": "[сынақ жүрісі] %s жойылатын еді"},
    "un.would_remove_rule": {"ru": "[холостой прогон] было бы удалено правило «%s»", "en": "[trial run] the firewall rule \"%s\" would have been removed", "ar": "[تشغيل تجريبي] كانت ستُحذف القاعدة «%s»", "uz": "[sinov rejimi] «%s» qoidasi oʻchirilgan boʻlardi", "kk": "[сынақ жүрісі] «%s» ережесі жойылатын еді"},
    "un.would_remove_task": {"ru": "[холостой прогон] была бы удалена задача %s", "en": "[trial run] the scheduler task %s would have been removed", "ar": "[تشغيل تجريبي] كانت ستُحذف المهمة %s", "uz": "[sinov rejimi] %s vazifasi oʻchirilgan boʻlardi", "kk": "[сынақ жүрісі] %s тапсырмасы жойылатын еді"},
    "un.would_stop": {"ru": "[холостой прогон] был бы остановлен PID %s", "en": "[trial run] PID %s would have been stopped", "ar": "[تشغيل تجريبي] كان سيُوقَف PID %s", "uz": "[sinov rejimi] PID %s toʻxtatilgan boʻlardi", "kk": "[сынақ жүрісі] PID %s тоқтатылатын еді"},
    "val.dir_absent": {"ru": "каталог отсутствует", "en": "the folder is not there", "ar": "المجلد غير موجود", "uz": "katalog mavjud emas", "kk": "каталог жоқ"},
    "verify.answer": {"ru": "отклик приложения (пусковой файл, порт %s)", "en": "response from the application (startup file, port %s)", "ar": "استجابة البرنامج (ملف التشغيل، المنفذ %s)", "uz": "dasturning javobi (ishga tushirish fayli, port %s)", "kk": "бағдарламаның жауабы (іске қосу файлы, %s порты)"},
    "verify.catalog": {"ru": "каталог шаблонов: ошибок %s, предупреждений %s", "en": "template catalog: errors %s, warnings %s", "ar": "فهرس القوالب: أخطاء %s، تحذيرات %s", "uz": "shablonlar katalogi: xatolar %s, ogohlantirishlar %s", "kk": "шаблондар каталогы: қателер %s, ескертулер %s"},
    "verify.catalog.answer": {"ru": "отклик службы каталога (пусковой файл, порт %s)", "en": "response of the catalog service (startup file, port %s)", "ar": "استجابة خدمة فهرس القوالب (ملف التشغيل، المنفذ %s)", "uz": "shablonlar katalogi xizmatining javobi (ishga tushirish fayli, port %s)", "kk": "шаблондар каталогы қызметінің жауабы (іске қосу файлы, порт %s)"},
    "verify.catalog.bridge": {"ru": "мост CDPM: каталог %s %s", "en": "CDPM bridge: catalog %s %s", "ar": "جسر CDPM: الفهرس %s %s", "uz": "CDPM koʻprigi: katalog %s %s", "kk": "CDPM көпірі: каталог %s %s"},
    "verify.catalog.bridge_skipped": {"ru": "мост CDPM не входит в комплект — пропуск", "en": "the CDPM bridge is not part of the kit - skipped", "ar": "جسر CDPM ليس ضمن الحزمة — تم التخطي", "uz": "CDPM koʻprigi toʻplamga kirmaydi — oʻtkazib yuborildi", "kk": "CDPM көпірі жинаққа кірмейді — өткізіп жіберілді"},
    "verify.catalog.skipped": {"ru": "служба каталога не входит в комплект — пропуск", "en": "the catalog service is not part of the kit - skipped", "ar": "خدمة فهرس القوالب ليست ضمن الحزمة — تم التخطي", "uz": "shablonlar katalogi xizmati toʻplamga kirmaydi — oʻtkazib yuborildi", "kk": "шаблондар каталогы қызметі жинаққа кірмейді — өткізіп жіберілді"},
    "verify.import": {"ru": "импорт приложения", "en": "loading of the application", "ar": "تحميل وحدات البرنامج", "uz": "dastur modullarini yuklash", "kk": "бағдарламаның жүктелуі"},
    "verify.note.answer": {"ru": "приложение не ответило на порту %s; хвост журнала: %s", "en": "the application did not answer on port %s; the end of the log: %s", "ar": "لم يستجب البرنامج على المنفذ %s؛ آخر أسطر السجل: %s", "uz": "dastur %s portida javob bermadi; jurnalning oxirgi satrlari: %s", "kk": "бағдарлама %s портында жауап бермеді; журналдың соңы: %s"},
    "verify.note.catalog": {"ru": "служба каталога не ответила на порту %s; хвост журнала: %s", "en": "the catalog service did not answer on port %s; log tail: %s", "ar": "خدمة فهرس القوالب لم تستجب على المنفذ %s؛ نهاية السجل: %s", "uz": "shablonlar katalogi xizmati %s portida javob bermadi; jurnal oxiri: %s", "kk": "шаблондар каталогы қызметі %s портында жауап бермеді; журнал соңы: %s"},
    "verify.note.catalog_bridge": {"ru": "мост CDPM не прочитал каталог шаблонов: %s", "en": "the CDPM bridge did not read the template catalog: %s", "ar": "جسر CDPM لم يقرأ فهرس القوالب: %s", "uz": "CDPM koʻprigi shablonlar katalogini oʻqimadi: %s", "kk": "CDPM көпірі шаблондар каталогын оқымады: %s"},
    "verify.note.catalog_check": {"ru": "каталог шаблонов не проверен: %s", "en": "the template catalog was not checked: %s", "ar": "لم يجر فحص فهرس القوالب: %s", "uz": "shablonlar katalogi tekshirilmadi: %s", "kk": "шаблондар каталогы тексерілмеді: %s"},
    "verify.note.catalog_port": {"ru": "в пусковом файле службы каталога нет порта (--port): %s — отклик не проверен", "en": "the catalog service startup file names no port (--port): %s - the response was not checked", "ar": "ملف تشغيل خدمة الفهرس لا يحدد منفذاً (--port): %s — لم يُفحص الرد", "uz": "katalog xizmatining ishga tushirish faylida port yo'q (--port): %s — javob tekshirilmadi", "kk": "каталог қызметінің іске қосу файлында порт жоқ (--port): %s — жауап тексерілмеді"},
    "verify.note.catalog_port_mismatch": {"ru": "порт службы каталога расходится: проверке назван %s, пусковой файл ставит службу на %s — отклик не проверен", "en": "catalog service port mismatch: the check was told %s, the startup file starts the service on %s - the response was not checked", "ar": "تعارض منفذ خدمة الفهرس: أُبلغ الفحص بـ %s، وملف التشغيل يشغّل الخدمة على %s — لم يُفحص الرد", "uz": "katalog xizmati porti mos kelmaydi: tekshiruvga %s aytilgan, ishga tushirish fayli xizmatni %s portida ko'taradi — javob tekshirilmadi", "kk": "каталог қызметінің порты сәйкес емес: тексеруге %s айтылған, іске қосу файлы қызметті %s портында көтереді — жауап тексерілмеді"},
    "verify.note.import": {"ru": "приложение не импортируется: %s", "en": "the application does not load: %s", "ar": "تعذّر تحميل وحدات البرنامج: %s", "uz": "dastur modullari yuklanmadi: %s", "kk": "бағдарлама жүктелмейді: %s"},
    "verify.note.no_password": {"ru": "пароль входа не найден в журнале — если вход не работает, удалите webapp/config/auth.json и запустите заново", "en": "the password for signing in was not found in the log — if signing in does not work, delete webapp/config/auth.json and start the program again", "ar": "لم يُعثر على كلمة مرور الدخول في السجل — فإن تعذّر الدخول فاحذف الملف webapp/config/auth.json وشغّل البرنامج من جديد", "uz": "kirish paroli jurnalda topilmadi — agar kirish ishlamasa, webapp/config/auth.json faylini oʻchiring va dasturni qaytadan ishga tushiring", "kk": "кіру құпия сөзі журналдан табылмады — кіру жұмыс істемесе, webapp/config/auth.json файлын жойып, бағдарламаны қайта іске қосыңыз"},
    "verify.note.venv": {"ru": "интерпретатор окружения не найден: %s", "en": "the interpreter of the environment was not found: %s", "ar": "لم يُعثر على مفسّر لغة python في البيئة المُنشأة: %s", "uz": "muhit interpretatori topilmadi: %s", "kk": "python ортасының интерпретаторы табылмады: %s"},
    "verify.password_reset": {"ru": "Забыли пароль: удалите файл", "en": "If you forget the password: delete the file", "ar": "إذا نسيت كلمة المرور: احذف الملف", "uz": "Parolni unutsangiz: faylni oʻchiring", "kk": "Құпия сөзді ұмытсаңыз: мына файлды жойыңыз"},
    "verify.password_reset2": {"ru": "и запустите программу заново — она создаст новый.", "en": "and start the program again — it will create a new one.", "ar": "ثم شغّل البرنامج من جديد — وسينشئ كلمة مرور جديدة.", "uz": "va dasturni qaytadan ishga tushiring — u yangi parol yaratadi.", "kk": "және бағдарламаны қайта іске қосыңыз — ол жаңасын жасайды."},
    "verify.password_title": {"ru": "ПАРОЛЬ ДЛЯ ВХОДА В ПРОГРАММУ:  %s", "en": "PASSWORD FOR SIGNING IN TO THE PROGRAM:  %s", "ar": "كلمة المرور للدخول إلى البرنامج:  %s", "uz": "DASTURGA KIRISH PAROLI:  %s", "kk": "БАҒДАРЛАМАҒА КІРУ ҚҰПИЯ СӨЗІ:  %s"},
    "verify.password_where": {"ru": "Запишите его. Он также лежит в файле", "en": "Write it down. It is also kept in the file", "ar": "دوّنها لديك. وهي محفوظة أيضًا في الملف", "uz": "Uni yozib qoʻying. U shu faylda ham saqlanadi", "kk": "Оны жазып алыңыз. Сондай-ақ ол мына файлда сақталған"},
    "verify.venv": {"ru": "интерпретатор окружения", "en": "interpreter of the environment", "ar": "مفسّر لغة python في البيئة المُنشأة", "uz": "muhit interpretatori", "kk": "python ортасының интерпретаторы"},
    "warn.no_local_model": {"ru": "ВНИМАНИЕ: локальной модели нет. Подсказки ИИ по данным заказчика работать не будут — такие наборы помечены «только локально» и в облако не отправляются. Программа при этом полностью работоспособна: расчёты пейринга и ростеринга от моделей ИИ не зависят. Локальную модель можно поставить позже и указать её в разделе «Установки».", "en": "IMPORTANT: there is no local model. Artificial intelligence suggestions about customer data will not work — such data sets are marked \"local only\" and are not sent to the cloud. The program itself is fully usable: the pairing and rostering calculations do not depend on artificial intelligence models. You can install a local model later and enter it in the Settings section.", "ar": "تنبيه: لا يوجد نموذج محلي. لن تعمل اقتراحات الذكاء الاصطناعي على بيانات العميل — فهذه المجموعات معلَّمة «محليًا فقط» ولا تُرسَل إلى السحابة. أما البرنامج نفسه فيعمل بكامل وظائفه: حسابات بناء سلاسل الرحلات وإعداد جداول عمل الطواقم لا تعتمد على نماذج الذكاء الاصطناعي. ويمكنك تثبيت نموذج محلي لاحقًا وتحديده في قسم «الإعدادات».", "uz": "DIQQAT: mahalliy model yoʻq. Buyurtmachi maʼlumotlari boʻyicha sunʼiy intellekt tavsiyalari ishlamaydi — bunday toʻplamlar «faqat mahalliy» deb belgilangan va bulutga yuborilmaydi. Dasturning oʻzi esa toʻliq ishlaydi: parvoz zanjirlarini qurish va ekipaj jadvalini tuzish hisob-kitoblari sunʼiy intellekt modellariga bogʻliq emas. Mahalliy modelni keyinroq oʻrnatib, uni «Sozlamalar» boʻlimida koʻrsatish mumkin.", "kk": "НАЗАР АУДАРЫҢЫЗ: жергілікті модель жоқ. Тапсырыс берушінің деректері бойынша жасанды интеллект кеңестері жұмыс істемейді — мұндай жиынтықтар «тек жергілікті» деп белгіленген және бұлтқа жіберілмейді. Бағдарламаның өзі бұл жағдайда толық жұмыс істейді: ұшу тізбектерін құру мен экипаж кестесін құру есептеулері жасанды интеллект модельдеріне тәуелді емес. Жергілікті модельді кейінірек орнатып, оны «Баптаулар» бөлімінде көрсетуге болады."},
    "warn.ollama_silent": {"ru": "По адресу %s сервер локальных моделей не ответил.", "en": "The local model server at %s did not answer.", "ar": "لم يستجب خادم النماذج المحلية على العنوان %s.", "uz": "%s manzilida mahalliy modellar serveri javob bermadi.", "kk": "%s мекенжайы бойынша жергілікті модельдер сервері жауап бермеді."},
    "why.access": {"ru": "по ней проверяется пропуск входа", "en": "the sign-in pass is checked against it", "ar": "بها يجري التحقق من تصريح الدخول", "uz": "kirish ruxsatnomasi shu boʻyicha tekshiriladi", "kk": "кіру рұқсаты сол бойынша тексеріледі"},
    "why.api_key.external": {"ru": "внешний контур допускает облачную модель", "en": "an external installation may use a cloud model", "ar": "النطاق الخارجي يسمح بالنموذج السحابي", "uz": "tashqi soha: bulutli modeldan foydalanish mumkin", "kk": "сыртқы аймақ бұлттағы модельге рұқсат етеді"},
    "why.api_key.internal": {"ru": "внутренний контур: облачная модель запрещена, ключ не нужен", "en": "an internal installation: a cloud model is not allowed, so no key is needed", "ar": "النطاق الداخلي: النموذج السحابي ممنوع، ولا حاجة إلى المفتاح", "uz": "ichki soha: bulutli model taqiqlangan, kalit kerak emas", "kk": "ішкі аймақ: бұлттағы модельге тыйым салынған, кілт қажет емес"},
    "why.auth.access": {"ru": "выбран вход по коду на почту", "en": "signing in with a code sent to an email address was chosen", "ar": "جرى اختيار الدخول برمز يُرسَل إلى البريد", "uz": "pochtaga yuboriladigan kod bilan kirish tanlandi", "kk": "поштаға жіберілетін код арқылы кіру таңдалды"},
    "why.auth.token": {"ru": "пароль создаётся программой при первом запуске", "en": "the program creates the password on first start", "ar": "البرنامج ينشئ كلمة المرور عند أول تشغيل", "uz": "parolni dastur birinchi ishga tushganda yaratadi", "kk": "құпия сөзді бағдарлама алғаш іске қосылғанда жасайды"},
    "why.autostart.allowed": {"ru": "изменение в системе — только с разрешения", "en": "a change to the system needs permission", "ar": "أي تغيير في النظام لا يجري إلا بإذن", "uz": "tizimga oʻzgartirish kiritish — faqat ruxsat bilan", "kk": "жүйедегі өзгеріс — тек рұқсатпен"},
    "why.autostart.no_admin": {"ru": "нет прав администратора, регистрация невозможна", "en": "there are no administrator rights, so it cannot be registered", "ar": "لا توجد صلاحيات المسؤول، والتسجيل غير ممكن", "uz": "administrator huquqlari yoʻq, roʻyxatga olish mumkin emas", "kk": "әкімші құқығы жоқ, тіркеу мүмкін емес"},
    "why.catalog_port.default": {"ru": "порт службы каталога по умолчанию (%s); иной — ключом --catalog-port", "en": "the default catalog service port (%s); another one — with --catalog-port", "ar": "المنفذ الافتراضي لخدمة الفهرس (%s)؛ لمنفذ آخر — المفتاح --catalog-port", "uz": "katalog xizmatining sukut boʻyicha porti (%s); boshqasi — --catalog-port kaliti bilan", "kk": "каталог қызметінің әдепкі порты (%s); басқасы — --catalog-port кілтімен"},
    "why.data_scope": {"ru": "политика площадки машиной не выводима; защита данных заказчика действует в обоих случаях — признак стоит на каждом наборе", "en": "the program cannot work out the policy of this site by itself; the protection of customer data holds in both cases — the mark is set on every data set", "ar": "سياسة بيئة التشغيل لا يستطيع البرنامج استنتاجها؛ وحماية بيانات العميل سارية في الحالتين — فالعلامة موضوعة على كل مجموعة بيانات", "uz": "tizim siyosatini dastur oʻzi aniqlay olmaydi; buyurtmachi maʼlumotlarining himoyasi har ikki holatda ham amal qiladi — belgi har bir toʻplamda turadi", "kk": "мұндай саясатты бағдарлама өзі анықтай алмайды; тапсырыс беруші деректерін қорғау екі жағдайда да күшінде — белгі әр жиынтықта тұрады"},
    "why.dir": {"ru": "куда ставится приложение", "en": "where the application is installed", "ar": "المكان الذي يُثبَّت فيه البرنامج", "uz": "dastur qayerga oʻrnatiladi", "kk": "бағдарлама қайда орнатылады"},
    "why.lang": {"ru": "первый вопрос установки; станет языком программы", "en": "the first question of the installation; it becomes the language of the program", "ar": "أول سؤال في التثبيت؛ وستصبح لغة البرنامج", "uz": "oʻrnatishning birinchi savoli; dastur tili ham shu boʻladi", "kk": "орнатудың бірінші сұрағы; бағдарламаның тілі болады"},
    "why.missing.jdk": {"ru": "JDK %s и новее", "en": "JDK %s or newer", "ar": "JDK %s أو أحدث", "uz": "JDK %s va undan yangirogʻi", "kk": "JDK %s және одан жаңасы"},
    "why.model.choice": {"ru": "выбор модели влияет на качество и скорость подсказок", "en": "the choice of model affects the quality and the speed of the suggestions", "ar": "اختيار النموذج يؤثر في جودة الاقتراحات وسرعتها", "uz": "model tanlovi tavsiyalarning sifati va tezligiga taʼsir qiladi", "kk": "модель таңдауы кеңестердің сапасы мен жылдамдығына әсер етеді"},
    "why.model.none": {"ru": "сервера локальных моделей нет", "en": "there is no server of local models", "ar": "لا يوجد خادم للنماذج المحلية", "uz": "mahalliy modellar serveri yoʻq", "kk": "жергілікті модельдер сервері жоқ"},
    "why.model.single": {"ru": "на сервере доступна одна модель", "en": "the server has only one model", "ar": "لا يتوفر على الخادم سوى نموذج واحد", "uz": "serverda bitta model mavjud", "kk": "серверде бір ғана модель бар"},
    "why.model_default": {"ru": "выведено из области данных и наличия локального сервера", "en": "worked out from the data policy and from whether a local server is present", "ar": "مستنتَج من نطاق البيانات ومن وجود خادم محلي", "uz": "maʼlumotlar sohasi va mahalliy server mavjudligidan kelib chiqib aniqlandi", "kk": "деректер аймағы мен жергілікті сервердің бар-жоғынан шығарылды"},
    "why.net": {"ru": "видимость программы машиной не выводима — это решение владельца", "en": "the program cannot work out by itself which computers should see it — this is the owner's decision", "ar": "مدى ظهور البرنامج في الشبكة لا يستطيع البرنامج استنتاجه — وهذا قرار المالك", "uz": "dastur qaysi kompyuterlarga koʻrinishini dastur oʻzi aniqlay olmaydi — buni egasi hal qiladi", "kk": "бағдарламаның желіде көрінуін бағдарлама өзі шеше алмайды — бұл иесінің шешімі"},
    "why.ollama_url": {"ru": "по этому адресу отвечает сервер локальных моделей", "en": "a server of local models answers at this address", "ar": "على هذا العنوان يستجيب خادم النماذج المحلية", "uz": "shu manzilda mahalliy modellar serveri javob bermoqda", "kk": "осы мекенжай бойынша жергілікті модельдер сервері жауап береді"},
    "why.port.default_free": {"ru": "порт по умолчанию свободен", "en": "the default port is free", "ar": "المنفذ الافتراضي متاح", "uz": "standart port boʻsh", "kk": "әдепкі порт бос"},
    "why.port.flag": {"ru": "порт указан при запуске", "en": "the port was given when the installer was started", "ar": "المنفذ محدَّد عند تشغيل المثبِّت", "uz": "port ishga tushirishda koʻrsatilgan", "kk": "порт іске қосу кезінде көрсетілген"},
    "why.port.taken": {"ru": "порт %s занят, взят ближайший свободный", "en": "port %s is taken, the nearest free one was used instead", "ar": "المنفذ %s مشغول، وجرى أخذ أقرب منفذ متاح", "uz": "%s porti band, eng yaqin boʻsh port olindi", "kk": "%s порты бос емес, ең жақын бос порт алынды"},
    "why.site.proven": {"ru": "проверенная площадка", "en": "a platform that has been tested", "ar": "بيئة تشغيل مختبَرة", "uz": "tekshirilgan tizim", "kk": "тексерілген жүйе"},
    "why.site.unproven_no": {"ru": "непроверенная площадка: продолжение НЕ подтверждено", "en": "a platform that has not been tested: continuing was NOT confirmed", "ar": "بيئة تشغيل غير مختبَرة: ولم يجرِ تأكيد المتابعة", "uz": "tekshirilmagan tizim: davom etish TASDIQLANMADI", "kk": "тексеру аз жүргізілген жүйе: жалғастыру РАСТАЛМАДЫ"},
    "why.site.unproven_ok": {"ru": "непроверенная площадка: продолжение подтверждено", "en": "a platform that has not been tested: continuing was confirmed", "ar": "بيئة تشغيل غير مختبَرة: وجرى تأكيد المتابعة", "uz": "tekshirilmagan tizim: davom etish tasdiqlandi", "kk": "тексеру аз жүргізілген жүйе: жалғастыру расталды"},
    "why.solver.missing": {"ru": "нет: %s", "en": "missing: %s", "ar": "غير موجود: %s", "uz": "yoʻq: %s", "kk": "жоқ: %s"},
    "why.solver.ready": {"ru": "JDK %s и Maven найдены — этап 2 будет собран", "en": "JDK %s and Maven were found — stage 2 will be built", "ar": "عُثر على JDK %s وMaven — وستُبنى المرحلة 2", "uz": "JDK %s va Maven topildi — 2-bosqich yigʻiladi", "kk": "JDK %s және Maven табылды — 2-кезең жиналады"},
    "why.solver.will_build": {"ru": "решатель будет собран загруженным набором инструментов", "en": "the solver will be built with the set of tools that was downloaded", "ar": "سيُبنى محرّك الحل بمجموعة الأدوات التي جرى تنزيلها", "uz": "yechuvchi yuklab olingan asboblar toʻplami bilan yigʻiladi", "kk": "есеп шешуші жүктелген құралдар жиынтығымен жиналады"},
    "why.sources.unchecked": {"ru": "не проверялся", "en": "was not checked", "ar": "لم يجرِ التحقق منه", "uz": "tekshirilmadi", "kk": "тексерілмеді"},
    "why.super_admin": {"ru": "первый адрес открывает раздел «Установки»; все перечисленные становятся администраторами при первом запуске", "en": "the first address can open the Settings section; every listed address becomes an administrator on first start", "ar": "العنوان الأول يفتح قسم «الإعدادات»؛ وكل العناوين المذكورة تصبح مسؤولين عند أول تشغيل", "uz": "birinchi manzil «Sozlamalar» boʻlimini ochadi; sanab oʻtilgan barcha manzillar birinchi ishga tushirishda administrator boʻladi", "kk": "бірінші мекенжай «Баптаулар» бөлімін ашады; тізімдегі барлық мекенжай алғашқы іске қосуда әкімші болады"},
    "why.toolchain.allowed": {"ru": "загрузка из сети — только с разрешения", "en": "downloading from the network needs permission", "ar": "التنزيل من الشبكة لا يجري إلا بإذن", "uz": "tarmoqdan yuklab olish — faqat ruxsat bilan", "kk": "желіден жүктеу — тек рұқсатпен"},
    "why.toolchain.denied": {"ru": "не разрешено: этап 1 (пейринг) работает, этап 2 (ростеринг) останется недоступен", "en": "not allowed: stage 1 (pairing) works, stage 2 (rostering) will stay unavailable", "ar": "غير مسموح: المرحلة 1 (بناء سلاسل الرحلات) تعمل، أما المرحلة 2 (إعداد جداول عمل الطواقم) فتبقى غير متاحة", "uz": "ruxsat berilmadi: 1-bosqich (parvoz zanjirlarini qurish) ishlaydi, 2-bosqich (ekipaj jadvalini tuzish) ishlamay qoladi", "kk": "рұқсат етілмеді: 1-кезең (ұшу тізбектерін құру) жұмыс істейді, 2-кезең (экипаж кестесін құру) қолжетімсіз болып қалады"},
    "why.toolchain.present": {"ru": "уже есть на площадке — ставить нечего", "en": "already on this computer — there is nothing to install", "ar": "موجودة في بيئة التشغيل — فلا شيء يُثبَّت", "uz": "tizimda allaqachon bor — oʻrnatadigan narsa yoʻq", "kk": "бұл компьютерде бұрыннан бар — орнататын ештеңе жоқ"},
    "why.workers": {"ru": "по числу ядер, два оставлены системе", "en": "from the number of cores, two are left to the system", "ar": "بحسب عدد الأنوية، مع ترك نواتين للنظام", "uz": "yadrolar soniga qarab, ikkitasi tizimga qoldirilgan", "kk": "ядро санына қарай, екеуі жүйеге қалдырылды"},
}
LANG = "ru"


def set_lang(code: str) -> None:
    global LANG
    LANG = code if code in ("ru", "en", "ar", "uz", "kk") else "en"


def T(key: str, *args: object) -> str:
    """Строка на выбранном языке; нет перевода — русский исходник."""
    row = MESSAGES.get(key) or {}
    text = row.get(LANG) or row.get("ru") or key
    if not args:
        return text
    try:
        return text % args
    except (TypeError, ValueError):
        # Строка без подстановок — например, перевод ещё не собран и
        # вернулся сам ключ. Недостающий перевод не должен ронять снос:
        # показываем что есть и дописываем значения.
        return "%s %s" % (text, " ".join(str(a) for a in args))

# Вид объекта приходит из журнала установки как ДАННЫЕ: журнал —
# машинная запись и не переводится никогда. Переводится только показ,
# и делается это по словарю вид→ключ. Незнакомый вид печатается как есть:
# лучше русское слово из журнала, чем пустое место.
KIND_KEYS = {
    "каталог цели": "kind.target_dir",
    "каталог данных": "kind.data_dir",
    "окружение python": "kind.venv",
    "набор инструментов": "kind.toolchain",
    "файл правил": "kind.rules_file",
    "пусковой файл": "kind.launcher",
    "пусковой файл решателя": "kind.solver_launcher",
    "сборка решателя": "kind.solver_build",
    "отчёт об установке": "kind.report",
    "файл запуска": "kind.start_file",
    "объект": "kind.object",
}


def kind_name(kind: str) -> str:
    """Название вида объекта на языке вывода."""
    key = KIND_KEYS.get(kind)
    return T(key) if key else kind


# Подкаталоги каталога данных, которые создаёт установщик (пустыми).
INSTALLER_DATA_DIRS = frozenset({"final-report", "rules", "cdpm-runs"})

# Очереди заявок каталога шаблонов (шаг 6 каталога): их создаёт РАБОТА
# программ, а не установка, — CDPM пишет `cdpm-catalog-requests/`,
# COSMOS `cosmos-catalog-requests/` рядом с прогонами под каталогом
# данных. В перечень установщика они не входят и войти не могут: общий
# стоп «данные владельца» назвал бы их безымянно, а владельцу нужно
# знать, что именно перенести. Поэтому гейт данных останавливается
# ПОИМЁННО и с путями.
CATALOG_QUEUE_DIRS = frozenset({"cdpm-catalog-requests",
                                "cosmos-catalog-requests"})

MARKER_LAUNCHER = "Пусковой файл создан установщиком COSMOS"
LAUNCHERS = ("run-cosmos.cmd", "run-cosmos.sh", "run-solver.cmd", "run-solver.sh",
             "run-cdpm.cmd", "run-cdpm.sh",
             "run-catalog.cmd", "run-catalog.sh",
             "START-COSMOS.cmd", "START-COSMOS.sh")

# Слепые зоны гейта постороннего: продуктовые внутренности и порождаемое
# работой. Принимаются целиком, БЕЗ пофайловой сверки, и это объявлено
# прямо: чужой файл, перемещённый сюда, этим гейтом не замечается.
BLIND_DIRS = (".git", ".venv", ".tools", "__pycache__")
BLIND_PATH_PARTS = (("kotlin-solver", "target"), ("webapp", "config"))
BLIND_FILES = tuple(LAUNCHERS) + ("webapp-server.log", "solver-server.log",
                                  "cdpm-server.log", "catalog-server.log")


class Stop(Exception):
    """Гейт не пропустил. Дальше — только решение владельца."""


# --------------------------------------------------------------- журналы

def state_dir(name: str) -> Path:
    if IS_WIN:
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "state"
    return base / name


class Install:
    """Одна установка, восстановленная по журналу контрольных точек."""

    def __init__(self, journal: Path, rows: list[dict]):
        self.journal = journal
        self.rows = rows
        self.target: Path | None = None
        self.data: Path | None = None
        self.started: datetime | None = None
        self.finished: str | None = None
        self.created: list[dict] = []
        self.firewall: dict | None = None
        self.task: dict | None = None
        self.unit: dict | None = None
        self.ssh_key: str | None = None
        self.port: int | None = None
        self.lang: str | None = None
        self.manifest_path: Path | None = None
        self.preexisting: list[str] = []
        for row in rows:
            kind = row.get("запись")
            if kind == "начало":
                self.target = Path(row["цель"])
                self.data = Path(row.get("каталог_данных") or "")
                self.started = _parse_time(row.get("время"))
                if row.get("цель_существовала"):
                    self.preexisting.append(row["цель"])
            elif kind == "создан объект":
                self.created.append(row)
                # Порт берётся ТОЛЬКО у пускового файла приложения: у
                # службы решателя свой порт (9096), и он затирал бы
                # нужный — тогда имя задачи и правила брандмауэра
                # вычислялись бы неверно.
                if row.get("вид") == "пусковой файл" and row.get("порт"):
                    self.port = int(row["порт"])
            elif kind == "создано правило брандмауэра":
                self.firewall = row
            elif kind == "создана задача планировщика":
                self.task = row
            elif kind == "создан юнит systemd":
                self.unit = row
            elif kind == "создан ключ ssh":
                self.ssh_key = row.get("путь")
            elif kind == "манифест дерева":
                self.manifest_path = Path(row["путь"])
            elif kind == "замысел принят":
                if row.get("порт"):
                    self.port = int(row["порт"])
                # Снос говорит на языке, выбранном при установке: тот же
                # человек, тот же язык, лишнего вопроса не задаём.
                self.lang = row.get("язык") or None
            elif kind == "каталог данных существовал до установки":
                self.preexisting.append(row["путь"])
            elif kind == "установка завершена":
                self.finished = row.get("итог")

    @property
    def complete(self) -> bool:
        return self.finished is not None

    def roots(self) -> list[tuple[str, Path]]:
        """Корневые объекты к удалению — в порядке от мелких к крупным,
        без вложенных в уже перечисленные."""
        out: list[tuple[str, Path]] = []
        for row in self.created:
            p = Path(row["путь"])
            if str(p) in self.preexisting:
                continue
            out.append((row.get("вид", "объект"), p))
        # Каталог цели поглощает всё внутри себя — вложенное не нужно.
        tops = [p for _, p in out]
        keep = []
        for kind, p in out:
            if any(p != q and _inside(p, q) for q in tops):
                continue
            keep.append((kind, p))
        return keep


def _parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _inside(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def find_installs() -> list[Install]:
    d = state_dir("cosmos-install")
    found: list[Install] = []
    if not d.is_dir():
        return found
    for path in sorted(d.glob("install-*.jsonl")):
        rows = []
        try:
            # utf-8-sig: если журнал когда-нибудь запишут с признаком
            # порядка байтов, первая строка не должна из-за этого
            # перестать разбираться — а с ней и вся установка.
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    # Прерванная запись последней строки — не повод терять
                    # весь журнал: читаем всё, что успело лечь целиком.
                    continue
        except OSError:
            continue
        if rows and rows[0].get("запись") == "начало":
            found.append(Install(path, rows))
    return found


# ------------------------------------------------------------------ печать

class Log:
    def __init__(self, journal_path: Path | None):
        self._fh = None
        self.journal_path = journal_path
        if journal_path is not None:
            try:
                journal_path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = journal_path.open("a", encoding="utf-8")
            except OSError:
                self._fh = None

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def step(self, title: str) -> None:
        self.say("")
        self.say("=== %s" % title)

    def counted(self, gate: str, examined: int, **facts) -> None:
        tail = "".join(", %s=%s" % (k, v) for k, v in facts.items())
        self.say("    [%s] %s%s" % (gate, T("un.examined", examined), tail))

    def record(self, kind: str, **payload) -> None:
        if self._fh is None:
            return
        row = {"время": datetime.now().isoformat(timespec="seconds"), "запись": kind}
        row.update(payload)
        try:
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._fh.flush()
        except OSError:
            pass

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()


# ------------------------------------------------------------- обход дерева

def walk_tree(root: Path) -> tuple[list[Path], list[Path], list[str]]:
    """Перечисление с ЯВНЫМ сбором ошибок: молчаливо пропущенный
    подкаталог — слепота, а не пустота, и она обязана останавливать."""
    files, dirs, errors = [], [], []

    def onerror(exc):
        errors.append("%s: %s" % (getattr(exc, "filename", "?"), exc))

    for base, subdirs, names in os.walk(root, onerror=onerror):
        # Порядок обхода — по имени на каждом уровне: os.scandir на ext4
        # отдаёт записи в порядке хеша, на NTFS — по алфавиту, и без
        # сортировки строки журнала «! путь» и возврат гейтов зависели бы
        # от файловой системы (CI № 65: Ubuntu красный, Windows зелёный).
        subdirs.sort()
        names.sort()
        b = Path(base)
        for d in subdirs:
            dirs.append(b / d)
        for n in names:
            files.append(b / n)
    return files, dirs, errors


def in_blind_zone(rel: Path) -> bool:
    parts = tuple(p.lower() for p in rel.parts)
    if any(d in parts for d in BLIND_DIRS):
        return True
    for chain in BLIND_PATH_PARTS:
        n = len(chain)
        for i in range(len(parts) - n + 1):
            if parts[i:i + n] == chain:
                return True
    if len(parts) == 1 and parts[0] in tuple(f.lower() for f in BLIND_FILES):
        return True
    if rel.suffix.lower() == ".pyc":
        return True
    return any(p.endswith(".egg-info") for p in parts)


# ------------------------------------------------------------------- гейты

def gate_foreign_by_time(root: Path, started: datetime, log: Log) -> list[Path]:
    """Гейт постороннего ПО ВРЕМЕНИ — работает без системы контроля версий.

    Всё внутри каталога, созданного установщиком, обязано быть создано
    ПОСЛЕ начала установки. Объект старше — не продукт этой установки:
    его либо перенесли сюда, либо каталог не тот. И то и другое —
    остановка. Гейт годен и там, где git недоступен: например, установку
    прервали на середине клонирования.
    """
    files, dirs, errors = walk_tree(root)
    if errors:
        for e in errors[:20]:
            log.say("    ! %s" % e)
        raise Stop(T("stop.enum_error", len(errors)))
    cutoff = started.timestamp() - 120        # запас на расхождение часов
    older = []
    for p in files + dirs:
        try:
            st = os.stat(p, follow_symlinks=False)
        except OSError as exc:
            raise Stop(T("stop.read_failed", p, exc))
        if st.st_ctime < cutoff:
            older.append(p)
    log.counted(T("gate.foreign_time"), len(files) + len(dirs),
                **{T("cnt.older_than_start"): len(older)})
    if older:
        for p in older[:40]:
            log.say("    " + T("un.mark.older", p))
        raise Stop(T("stop.older"))
    return files + dirs


# Места, куда пишет САМА РАБОТА программы уже после установки: журналы,
# рабочие файлы приложения, кеши интерпретатора, служебные файлы системы
# контроля версий, сборка решателя. Объект, которого нет в описи, но он
# лежит здесь, — след работы, а не чужое. Всё остальное вне описи —
# постороннее.
RUNTIME_DIRS = (".git", "__pycache__")
RUNTIME_PATH_PARTS = (("webapp", "config"), ("kotlin-solver", "target"))
RUNTIME_SUFFIXES = (".pyc", ".log")


def in_runtime_zone(rel: Path) -> bool:
    parts = tuple(p.lower() for p in rel.parts)
    if any(d in parts for d in RUNTIME_DIRS):
        return True
    for chain in RUNTIME_PATH_PARTS:
        n = len(chain)
        for i in range(len(parts) - n + 1):
            if parts[i:i + n] == chain:
                return True
    return rel.suffix.lower() in RUNTIME_SUFFIXES


def load_manifest(path: Path) -> dict[str, tuple | None] | None:
    """Опись дерева, снятая установщиком сразу после установки.

    Формат 2: сжатый файл, первая строка — заголовок с корнем, дальше по
    строке на объект: `[путь, размер, время]` у файла и `[путь]` у
    каталога, путь ОТНОСИТЕЛЬНЫЙ. Читается и несжатая опись, и прежний
    формат со словарями — снос не вправе отказываться работать с
    установкой, сделанной прежней версией установщика.

    Возвращает: полный путь в нижнем регистре → (размер, время) у файла
    либо None у каталога.
    """
    if path is None or not path.exists():
        return None
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        else:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, EOFError, gzip.BadGzipFile):
        return None

    rows: dict[str, tuple | None] = {}
    root: Path | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "корень" in row:
            root = Path(row["корень"])
        elif isinstance(row, dict) and "п" in row:          # прежний формат
            rows[str(row["п"]).lower()] = (
                (row.get("р"), row.get("и")) if "р" in row else None)
        elif isinstance(row, list) and row:
            if root is None:
                return None            # относительные пути без корня бесполезны
            full = str(root / str(row[0])).lower()
            rows[full] = (row[1], row[2]) if len(row) >= 3 else None
    return rows or None


def gate_by_manifest(root: Path, manifest: dict[str, dict], log: Log,
                     known: set[str] | None = None) -> list[Path]:
    """Гейт постороннего ПО ОПИСИ — без слепых зон.

    Прежде снос принимал целые поддеревья на веру: клон с историей,
    окружение python, загруженный набор инструментов — 16 тысяч объектов
    из 19, которые никто не проверял, и о слепоте которых план честно
    предупреждал. Опись, снятая установщиком, снимает эту слепоту: в ней
    перечислено ВСЁ, что установка после себя оставила.

    Судим так: объект есть в описи — продукт установки; объекта нет, но
    он лежит там, куда пишет сама работа программы, — след работы; всё
    прочее — постороннее, и это СТОП.
    """
    files, dirs, errors = walk_tree(root)
    if errors:
        for e in errors[:20]:
            log.say("    ! %s" % e)
        raise Stop(T("stop.enum_error", len(errors)))
    known = {k.lower() for k in (known or set())}

    def recorded(p: Path) -> bool:
        # ТОЧНОЕ совпадение, без вложенного. Журнал записывает как
        # созданные и сам каталог цели, и `.venv`, и `.tools` — приём
        # «всё, что внутри записанного» превращал бы эти поддеревья в
        # слепые зоны заново и сводил проверку к нулю.
        return str(p).lower() in known

    described = runtime = changed = 0
    foreign: list[Path] = []
    for p in files + dirs:
        s = str(p).lower()
        rel = p.relative_to(root)
        if s in manifest:
            described += 1
            marks = manifest[s]
            # Файл вне зоны работы программы после установки меняться не
            # должен. Изменился — под именем продукта может лежать чужой
            # документ; имя доказывает не больше, чем маркер.
            if marks and not in_runtime_zone(rel):
                try:
                    st = os.stat(p, follow_symlinks=False)
                except OSError as exc:
                    raise Stop("не удалось прочитать %s: %s" % (p, exc))
                if (st.st_size, int(st.st_mtime_ns)) != (marks[0], marks[1]):
                    changed += 1
                    foreign.append(p)
        elif recorded(p):
            described += 1
        elif in_runtime_zone(rel):
            runtime += 1
        else:
            foreign.append(p)
    log.counted(T("gate.manifest"), len(files) + len(dirs),
                **{T("cnt.described"): described,
                   T("cnt.runtime"): runtime,
                   T("cnt.changed"): changed,
                   T("cnt.foreign"): len(foreign)})
    if foreign:
        for p in foreign[:40]:
            log.say("    " + T("un.mark.foreign", p))
        raise Stop(T("stop.foreign", len(foreign)))
    return files + dirs


def gate_foreign_by_git(root: Path, log: Log,
                        known: set[str] | None = None) -> list[Path] | None:
    """Гейт постороннего ПО СОДЕРЖИМОМУ. Возвращает None, если сверка
    невозможна (нет git или это не клон) — тогда судит гейт времени.

    `known` — пути, которые установщик записал в свой журнал как им
    созданные. Это и есть выигрыш от контрольных точек: то, что раньше
    приходилось угадывать по косвенным признакам (отчёт об установке,
    файл правил из образца — они не под учётом и выглядели «посторонним»),
    теперь известно как факт.
    """
    if not (root / ".git").is_dir():
        return None

    def git(*args):
        p = subprocess.run(["git", "-C", str(root), "--no-optional-locks",
                            "-c", "core.quotepath=false", *args],
                           capture_output=True)
        return p.returncode, p.stdout

    rc, out = git("ls-files", "-z")
    if rc != 0:
        return None
    tracked = {Path(s.replace("/", "\\") if IS_WIN else s)
               for s in (c.decode("utf-8") for c in out.split(b"\0") if c)}
    if not tracked:
        return None
    changed: set[Path] = set()
    rc, out = git("status", "--porcelain", "-z")
    if rc != 0:
        return None
    for entry in (c.decode("utf-8") for c in out.split(b"\0") if c):
        if len(entry) > 3 and entry[2] == " ":
            changed.add(Path(entry[3:].replace("/", "\\") if IS_WIN else entry[3:]))
    rc, out = git("diff", "--name-only", "-z", "HEAD")
    if rc == 0:
        changed |= {Path(s.replace("/", "\\") if IS_WIN else s)
                    for s in (c.decode("utf-8") for c in out.split(b"\0") if c)}

    files, dirs, errors = walk_tree(root)
    if errors:
        raise Stop(T("stop.enum_error", len(errors)))
    known = {k.lower() for k in (known or set())}

    def recorded(p: Path) -> bool:
        # ТОЧНОЕ совпадение, без вложенного. Журнал записывает как
        # созданные и сам каталог цели, и `.venv`, и `.tools` — приём
        # «всё, что внутри записанного» превращал бы эти поддеревья в
        # слепые зоны заново и сводил проверку к нулю.
        return str(p).lower() in known

    product = blind = 0
    foreign: list[Path] = []
    for f in files:
        rel = f.relative_to(root)
        if in_blind_zone(rel):
            blind += 1
        elif recorded(f) or (rel in tracked and rel not in changed):
            product += 1
        else:
            foreign.append(f)
    for d in dirs:
        rel = d.relative_to(root)
        if in_blind_zone(rel) or recorded(d):
            continue
        try:
            if not any(d.iterdir()):
                foreign.append(d)
        except OSError as exc:
            raise Stop(T("stop.read_dir_failed", d, exc))
    log.counted(T("gate.foreign_content"), len(files) + len(dirs),
                **{T("cnt.judged"): product + len(foreign),
                   T("cnt.skipped_blind"): blind,
                   T("cnt.product"): product,
                   T("cnt.foreign"): len(foreign)})
    if foreign:
        for p in foreign[:40]:
            log.say("    " + T("un.mark.foreign", p))
        raise Stop(T("stop.foreign", len(foreign)))
    return files + dirs


def gate_reparse(paths: list[Path], roots: list[Path], log: Log) -> None:
    """Единственная защита от подмены ссылкой: перечисление внутрь
    соединения не входит, поэтому подменённый подкаталог выглядел бы
    пустым и прочие гейты его не заметили бы."""
    found, examined = [], 0
    for p in [r for r in roots if r.exists()] + paths:
        examined += 1
        try:
            st = os.stat(p, follow_symlinks=False)
        except OSError as exc:
            raise Stop(T("stop.read_attrs_failed", p, exc))
        if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT \
                or statmod.S_ISLNK(st.st_mode):
            found.append(p)
    log.counted(T("gate.links"), examined,
                **{T("cnt.reparse"): len(found)})
    if found:
        for p in found[:40]:
            log.say("    " + T("un.mark.link", p))
        raise Stop(T("stop.links", len(found)))


def gate_hardlinks(paths: list[Path], log: Log) -> None:
    """Удаление снимает атрибут «только чтение» с записи файла, а запись
    в NTFS одна на все его имена: у чужого оригинала за пределами
    установки, имеющего второе имя внутри неё, защита пропала бы."""
    readonly, multi, examined = 0, [], 0
    for p in paths:
        try:
            st = os.stat(p, follow_symlinks=False)
        except OSError as exc:
            raise Stop(T("stop.read_failed", p, exc))
        if statmod.S_ISDIR(st.st_mode):
            continue
        examined += 1
        if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_READONLY:
            readonly += 1
            if st.st_nlink > 1:
                multi.append((p, st.st_nlink))
    log.counted(T("gate.hardlinks"), examined,
                **{T("cnt.readonly"): readonly,
                   T("cnt.multi_named"): len(multi)})
    if multi:
        for p, n in multi[:40]:
            log.say("    " + T("un.mark.names", p, n))
        raise Stop(T("stop.hardlinks", len(multi)))


def gate_data_dir(data: Path, log: Log,
                  journaled: dict | None = None) -> list[Path]:
    """Каталог данных удаляется, только если он в точности такой, каким
    его создал установщик: пустые подкаталоги плюс файлы, которые
    установка создала САМА и записала в журнал вместе с отпечатком
    байтов (носитель раскладки ролей из образца, ревизия 1.18.5 —
    GPT-060/062). Журналированный файл считается созданным установкой
    только если его байты и сейчас совпадают с журналом: изменённый
    владельцем носитель — его данные, снос останавливается. Любой другой
    файл внутри — данные владельца; без журнала журналированных файлов
    нет (безопасный отказ). `journaled`: {путь: sha256 | None}; запись
    без отпечатка (старый журнал) тоже считается данными владельца."""
    if not data.exists():
        log.counted(T("gate.data"), 0,
                    **{T("cnt.state"): T("val.dir_absent")})
        return []
    files, dirs, errors = walk_tree(data)
    if errors:
        raise Stop(T("stop.enum_error_path", data, len(errors)))
    names = sorted(d.name.lower() for d in dirs)
    journaled = journaled or {}

    def _installer_made(f: Path) -> bool:
        want = journaled.get(str(f))
        if not want:
            return False
        try:
            import hashlib
            return hashlib.sha256(f.read_bytes()).hexdigest() == want
        except OSError:
            return False
    foreign = [f for f in files if not _installer_made(f)]
    # Пустые подкаталоги, которые создаёт САМ установщик, данными
    # владельца не являются. Проверка вложенности множеств, а не
    # перечисление сочетаний: прежний список из четырёх вариантов
    # ломался при появлении каждого нового подкаталога — так и вышло с
    # каталогом прогонов модуля данных.
    ok = (not foreign) and set(names) <= INSTALLER_DATA_DIRS
    log.counted(T("gate.data"), len(files) + len(dirs),
                **{T("cnt.files"): len(files),
                   T("cnt.subdirs"): len(dirs),
                   T("cnt.composition"): ",".join(names) or "-"})
    # Очереди заявок каталога шаблонов — стоп ПОИМЁННО и раньше общего:
    # безымянное «данные владельца» не подсказало бы, что переносить.
    queues = [d for d in dirs if d.name.lower() in CATALOG_QUEUE_DIRS]
    if queues:
        for p in queues[:40]:
            log.say("    ! %s" % p)
        raise Stop(T("stop.catalog_queues",
                     "; ".join(str(p) for p in queues)))
    if not ok:
        for p in (files + dirs)[:40]:
            log.say("    ! %s" % p)
        raise Stop(T("stop.data_not_empty"))
    return files + dirs


# ---------------------------------------------------------------- процессы

PS_SNAPSHOT = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,"
    "@{n='Created';e={if($_.CreationDate){$_.CreationDate.ToFileTimeUtc()}else{0}}} | "
    "ConvertTo-Json -Depth 2 -Compress"
)
SHELL_IMAGES = ("cmd.exe", "powershell.exe", "pwsh.exe", "sh", "bash")
SHELL_SWITCHES = ("/k", "/c", "/d", "/s", "/q", "-noprofile", "-nologo",
                  "-executionpolicy", "bypass", "-noexit", "-file", "-c")


def tokenize_cmdline(line: str) -> list[str]:
    tokens, cur, inq = [], [], False
    for ch in line:
        if ch == '"':
            inq = not inq
        elif ch.isspace() and not inq:
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def invokes_launcher(cmdline: str | None, launchers: list[str]) -> bool:
    """Оболочка, ЗАПУЩЕННАЯ пусковым файлом. Признак намеренно узкий:
    `type <файл>`, `Get-Content <файл>` и любое иное упоминание пути под
    него не подпадают — лучше назвать лишнее, чем убить чужое."""
    if not cmdline:
        return False
    tokens = tokenize_cmdline(cmdline)
    if not tokens:
        return False
    low = [t.lower() for t in tokens]
    if not any(low[0].endswith(img) for img in SHELL_IMAGES):
        return False
    targets = {l.lower() for l in launchers}
    for i, tok in enumerate(low):
        if tok in targets:
            for j, prev in enumerate(low[:i]):
                if j == 0:
                    continue
                if prev in SHELL_SWITCHES or prev.startswith(("/", "-")):
                    continue
                return False
            return True
    return False


def snapshot_processes() -> list[dict]:
    if not IS_WIN:
        out = subprocess.run(["ps", "-eo", "pid,ppid,args"],
                             capture_output=True).stdout.decode("utf-8", "replace")
        rows = []
        for line in out.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) == 3:
                rows.append({"ProcessId": int(parts[0]),
                             "ParentProcessId": int(parts[1]),
                             "ExecutablePath": "", "CommandLine": parts[2],
                             "Created": 0})
        return rows
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", PS_SNAPSHOT], capture_output=True)
    if p.returncode != 0:
        raise Stop(T("stop.ps_failed",
                     p.stderr.decode("utf-8", "replace")[:200]))
    text = p.stdout.decode("utf-8", "replace").strip()
    # Список процессов — НЕДОВЕРЕННЫЙ ввод: командная строка чужого
    # процесса вправе содержать управляющий символ (перевод строки,
    # табуляцию, символ escape), и строгий разбор на нём падает —
    # уборщик отказывался работать из-за постороннего процесса на
    # машине (живой случай 2026-09-03: пять тестов цикла установки).
    # strict=False разрешает управляющие символы внутри строк JSON;
    # неразбираемый ответ — остановка словами, а не трейсбек.
    try:
        data = json.loads(text, strict=False) if text else []
    except json.JSONDecodeError as e:
        raise Stop(T("stop.ps_failed",
                     "снимок процессов не разобран: %s" % e)) from None
    return [data] if isinstance(data, dict) else data


def classify_processes(snapshot: list[dict], target: Path,
                       launchers: list[str]) -> dict:
    """Наш процесс — по пути образа, по ЗАПУСКАЕМОМУ файлу из argv[0] или
    по запуску пускового файла. Второй признак обязателен: виртуальное
    окружение Python 3.14 запускает БАЗОВЫЙ интерпретатор, и у процесса
    приложения путь образа ведёт в системный Python, а внутри цели лежит
    только запускаемый файл. Нечитаемый путь образа — не подозрение: у
    половины процессов машины он не читается без прав администратора."""
    tl = str(target).lower().rstrip("\\/") + os.sep
    ours, mentioned, unreadable = [], [], 0
    for pr in snapshot:
        path = (pr.get("ExecutablePath") or "").lower()
        cmdline = pr.get("CommandLine") or ""
        tokens = tokenize_cmdline(cmdline)
        argv0 = tokens[0].lower() if tokens else ""
        if not path:
            unreadable += 1
        if path.startswith(tl) or argv0.startswith(tl):
            ours.append(pr)
        elif invokes_launcher(cmdline, launchers):
            ours.append(pr)
        elif tl[:-1] in cmdline.lower():
            mentioned.append(pr)
    return {"наши": ours, "упоминают": mentioned, "путь_нечитаем": unreadable}


def ancestors_of(pid: int, by_pid: dict[int, dict]) -> list[dict]:
    """Цепочка предков с проверкой времени создания: номер процесса
    переиспользуется, поэтому «родитель» моложе ребёнка родителем не
    является — цепочка на нём обрывается."""
    chain, seen = [], set()
    cur = by_pid.get(pid)
    while cur is not None:
        ppid = cur.get("ParentProcessId")
        parent = by_pid.get(ppid)
        if parent is None or ppid in seen:
            break
        if (parent.get("Created") or 0) > (cur.get("Created") or 0):
            break
        seen.add(ppid)
        chain.append(parent)
        cur = parent
    return chain


class PinnedProcess:
    """Закреплённый описатель: пока он открыт, номер процесса не может
    быть переиспользован системой, поэтому проверка и остановка заведомо
    адресуют один объект."""

    QUERY = 0x1000
    TERMINATE = 0x0001

    def __init__(self, pid: int):
        self.pid = pid
        self.handle = ctypes.windll.kernel32.OpenProcess(
            self.QUERY | self.TERMINATE, False, pid) if IS_WIN else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if IS_WIN and self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
        return False

    @property
    def ok(self) -> bool:
        return bool(self.handle) if IS_WIN else True

    def creation_filetime(self) -> int | None:
        if not IS_WIN or not self.handle:
            return None
        c, e, k, u = (wt.FILETIME() for _ in range(4))
        if not ctypes.windll.kernel32.GetProcessTimes(
                self.handle, ctypes.byref(c), ctypes.byref(e),
                ctypes.byref(k), ctypes.byref(u)):
            return None
        return (c.dwHighDateTime << 32) | c.dwLowDateTime

    def has_exited(self) -> bool:
        """Процесс уже завершился? Это НЕ отказ, а обычное дело: останов
        родителя уносит с собой потомка, и к моменту очереди потомка
        останавливать уже некого. Признак — код выхода не «ещё жив»."""
        if not IS_WIN or not self.handle:
            return False
        code = wt.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(self.handle,
                                                         ctypes.byref(code)):
            return False
        return code.value != 259                      # 259 = STILL_ACTIVE

    def terminate(self) -> bool:
        if not IS_WIN:
            try:
                os.kill(self.pid, 9)
                return True
            except ProcessLookupError:
                return True                            # уже завершился
            except OSError:
                return False
        if ctypes.windll.kernel32.TerminateProcess(self.handle, 1):
            return True
        # Отказ на уже мёртвом процессе — не отказ. Система возвращает
        # «доступ запрещён» и для завершившегося процесса, поэтому судим
        # по коду выхода, а не по коду ошибки.
        return self.has_exited()


# ---------------------------------------------------------------- удаление

def remove_tree(path: Path, log: Log, apply: bool) -> None:
    """Удаление БЕЗ следования по ссылкам. Код возврата средства удаления
    бесполезен и обманчив (0 при частичном отказе, 2 при отсутствии
    каталога), поэтому результат проверяется существованием."""
    if not path.exists():
        log.say("    " + T("un.nothing_to_remove", path))
        return
    if not apply:
        log.say("    " + T("un.would_remove", path))
        return
    if path.is_file():
        path.unlink(missing_ok=True)
    elif IS_WIN:
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(path)],
                       capture_output=True)
    else:
        subprocess.run(["rm", "-rf", "--one-file-system", str(path)],
                       capture_output=True)
    # Каталог исчезает не мгновенно: система убирает запись, когда
    # закроется последний описатель, и проверка сразу после команды
    # застаёт ПУСТОЙ, но ещё существующий каталог. Ждать нужно ровно
    # этого случая, а не занятых файлов: если внутри что-то осталось,
    # ждать нечего — это отказ.
    waited = 0.0
    while path.exists() and waited < 5.0:
        rest_f, rest_d, _ = walk_tree(path) if path.is_dir() else ([path], [], [])
        if rest_f or rest_d:
            break
        time.sleep(0.2)
        waited += 0.2
    if path.exists():
        rest_f, rest_d, _ = walk_tree(path) if path.is_dir() else ([path], [], [])
        if not rest_f and not rest_d:
            # Содержимое удалено полностью, а сама запись каталога
            # держится системой до выхода программы: так бывает, когда
            # службы файловой системы (например, проверка на вирусы)
            # ещё не отпустили только что удалённые файлы. Называть это
            # отказом нечестно — удалять больше нечего; но и молчать
            # нельзя, поэтому пустой остаток называется владельцу.
            log.say("    " + T("un.empty_left", path))
            log.record("остался пустой каталог", путь=str(path))
            return
        for r in (rest_f + rest_d)[:40]:
            log.say("    " + T("un.mark.left", r))
        raise Stop(T("stop.incomplete", path, len(rest_f) + len(rest_d)))
    log.say("    " + T("un.removed", path))


# Признаки наличия объекта — ЛАТИНИЦЕЙ. Вывод PowerShell приходит в
# кодовой странице консоли, и русские слова возвращались как «????»:
# сравнение с «ЕСТЬ» не срабатывало никогда, и существующее правило
# брандмауэра молча не удалялось. Латиница переживает любую кодировку.
PRESENT, ABSENT = "PRESENT", "ABSENT"


def firewall_state(name: str) -> str:
    if not IS_WIN:
        return ABSENT
    script = ("$r = Get-NetFirewallRule -DisplayName %s -ErrorAction SilentlyContinue; "
              "if ($null -eq $r) { 'ABSENT' } else { 'PRESENT' }" % json.dumps(name))
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", script], capture_output=True)
    out = p.stdout.decode("utf-8", "replace").strip()
    if out not in (PRESENT, ABSENT):
        raise Stop(T("stop.fw_state_unknown", name, out or T("stop.empty_answer")))
    return out


def task_state(name: str) -> tuple[str, str]:
    if not IS_WIN:
        return ABSENT, ""
    script = ("$t = Get-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue; "
              "if ($null -eq $t) { 'ABSENT' } else { 'PRESENT|' + "
              "(($t.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ') }"
              % json.dumps(name))
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", script], capture_output=True)
    out = p.stdout.decode("utf-8", "replace").strip()
    if not out or out.startswith(ABSENT):
        return ABSENT, ""
    if not out.startswith(PRESENT):
        raise Stop(T("stop.task_state_unknown", name, out))
    return PRESENT, out.partition("|")[2]


def is_admin() -> bool:
    if not IS_WIN:
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def read_port(launcher: Path, pattern: str) -> int | None:
    try:
        m = re.search(pattern, launcher.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def known_without_journal(target: Path) -> set[str]:
    """Продукт установки, который не значится в системе контроля версий и
    не попадает в слепые зоны. Без журнала такой файл опознаётся по
    маркеру в первой строке — установщик пишет отчёт ВНУТРЬ цели, значит
    маркер здесь доказывает происхождение, а не только содержимое."""
    known: set[str] = set()
    report = target / "installation-report.md"
    if report.is_file():
        try:
            first = report.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        except OSError:
            first = []
        if first and first[0].strip() == "# Отчёт об установке COSMOS":
            known.add(str(report))
    return known


def has_marker(target: Path) -> bool:
    for name in ("run-cosmos.cmd", "run-cosmos.sh"):
        p = target / name
        if p.is_file():
            try:
                if MARKER_LAUNCHER in p.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                pass
    return False


# ------------------------------------------------------------------ главная

def choose_install(installs: list[Install], target: Path | None) -> Install | None:
    if target is not None:
        same = [i for i in installs if i.target and
                str(i.target).lower() == str(target).lower()]
        return same[-1] if same else None
    return installs[-1] if len(installs) >= 1 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Деинсталлятор COSMOS (по умолчанию — холостой прогон)")
    ap.add_argument("--target", help="каталог установки (если журналов несколько)")
    ap.add_argument("--data", help="каталог данных (умолчание: <цель>-data)")
    ap.add_argument("--apply", action="store_true", help="выполнить снос")
    ap.add_argument("--list", action="store_true",
                    help="показать найденные установки и выйти")
    ap.add_argument("--lang", choices=("ru", "en", "ar", "uz", "kk"),
                    help="язык вывода (умолчание — язык, выбранный при установке)")
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    a = ap.parse_args(argv_list)

    installs = find_installs()
    if a.list:
        # Перечень тоже читает человек, поэтому язык выбирается ДО печати:
        # явный ключ важнее, иначе — язык последней установки.
        set_lang(a.lang or (installs[-1].lang if installs and installs[-1].lang
                            else "ru"))
        if not installs:
            print(T("un.list.none", state_dir("cosmos-install")))
            return 0
        print(T("un.list.header"))
        for i in installs:
            print("  %s → %s → %s" % (i.journal.name, i.target,
                                      i.finished or T("un.list.interrupted")))
        return 0

    target = Path(a.target).resolve() if a.target else None
    install = choose_install(installs, target)
    if install is not None and target is None:
        target = install.target
    if target is None:
        target = Path("C:/cosmos") if IS_WIN else Path.home() / "cosmos"
    data = Path(a.data) if a.data else (
        install.data if install and install.data else Path(str(target) + "-data"))

    # Уйти из удаляемого дерева. Деинсталлятор лежит ВНУТРИ установки, и
    # человек естественно запускает его оттуда — а каталог, который для
    # процесса текущий, средствами системы не удаляется: снос отказал бы
    # на последнем шаге, уже всё проверив.
    try:
        here = Path.cwd()
        if _inside(here, target) or (data and _inside(here, data)):
            os.chdir(Path.home())
    except OSError:
        pass

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = Log(state_dir("cosmos-uninstall") / ("journal-%s.txt" % stamp)
              if a.apply else None)

    # Язык: явный ключ важнее, иначе — язык, выбранный при установке.
    set_lang(a.lang or (install.lang if install and install.lang else "ru"))

    log.say("%s — %s" % (T("un.title"),
                         T("un.mode.apply") if a.apply else T("un.mode.dry")))
    log.say(T("un.target", target))
    log.say(T("un.data", data))
    if install is not None:
        log.say(T("un.journal_found", install.journal.name,
                  install.finished or T("un.journal_interrupted")))
    else:
        log.say(T("un.journal_missing"))
    if not a.apply:
        log.say(T("un.dry_notice"))

    try:
        # --- условия ---------------------------------------------------
        log.step(T("un.section.conditions"))
        port = None
        if install is not None and install.port:
            port = install.port
        if port is None and target.exists():
            port = read_port(target / ("run-cosmos.cmd" if IS_WIN else "run-cosmos.sh"),
                             r"--port\s+(\d+)")
        log.say("    " + T("un.port", port if port else T("un.port_unknown")))
        log.say("    " + T("un.admin", T("plan.val.present") if is_admin()
                           else T("plan.val.absent")))
        log.counted(T("gate.conditions"), 2)

        # --- что удаляем ------------------------------------------------
        log.step(T("un.section.scope"))
        if install is not None:
            roots = install.roots()
            for kind, p in roots:
                log.say("    %s: %s%s" % (kind_name(kind), p,
                                          "" if p.exists() else T("un.already_gone")))
            for p in install.preexisting:
                log.say("    " + T("un.keep_preexisting", p))
            if install.ssh_key:
                log.say("    " + T("un.keep_ssh", install.ssh_key))
            log.counted(T("gate.scope_journal"), len(roots))
        else:
            roots = []
            if target.exists():
                if not has_marker(target):
                    raise Stop(T("stop.no_marker", target))
                roots.append(("каталог цели", target))
            if data.exists():
                roots.append(("каталог данных", data))
            log.counted(T("gate.scope_signs"), len(roots))

        # --- гейты по каждому корню ------------------------------------
        manifest = load_manifest(install.manifest_path) if install else None
        if manifest is not None:
            log.say("    " + T("un.manifest_found", len(manifest)))
        elif install is not None:
            log.say("    " + T("un.manifest_missing"))
        contents: dict[Path, list[Path]] = {}
        for kind, p in roots:
            if not p.exists() or p.is_file():
                continue
            log.step(T("un.section.checks", p))
            if p == data:
                contents[p] = gate_data_dir(
                    p, log,
                    {row["путь"]: row.get("sha256") for row in install.created}
                    if install else {})
            else:
                known = ({row["путь"] for row in install.created} if install
                         else known_without_journal(p))
                if manifest is not None:
                    # Опись установки перечисляет ВСЁ созданное, поэтому
                    # слепых зон не остаётся: сверка идёт по каждому
                    # объекту, включая клон, окружение и набор
                    # инструментов. Прочие гейты остаются как есть —
                    # они судят по другим признакам.
                    contents[p] = gate_by_manifest(p, manifest, log, known)
                    if install is not None and install.started is not None:
                        gate_foreign_by_time(p, install.started, log)
                else:
                    by_git = gate_foreign_by_git(p, log, known)
                    if install is not None and install.started is not None:
                        by_time = gate_foreign_by_time(p, install.started, log)
                    elif by_git is None:
                        raise Stop(T("stop.no_evidence", p))
                    else:
                        by_time = []
                    contents[p] = by_git if by_git is not None else by_time
            gate_reparse(contents[p], [p], log)
            gate_hardlinks(contents[p], log)

        # Что вообще предстоит сделать. Список нужен для вопроса в конце:
        # каталогов может уже не быть, а правило брандмауэра или задача
        # остаться — тогда вопрос обязан прозвучать, иначе человек увидит
        # «сделать нечего» при живом следе установки.
        actions: list[str] = [str(p) for _, p in roots if p.exists()]

        # --- процессы ---------------------------------------------------
        log.step(T("un.section.processes"))
        snap = snapshot_processes()
        by_pid = {int(p["ProcessId"]): p for p in snap if p.get("ProcessId") is not None}
        launchers = [str(target / n) for n in LAUNCHERS]
        groups = classify_processes(snap, target, launchers)
        log.counted(T("gate.processes"), len(snap),
                    **{T("cnt.ours"): len(groups["наши"]),
                       T("cnt.mentions_path"): len(groups["упоминают"]),
                       T("cnt.image_unreadable"): groups["путь_нечитаем"]})
        for pr in groups["наши"]:
            log.say("    " + T("un.ours", pr["ProcessId"],
                               (pr.get("CommandLine") or "").strip()[:160]))
        for pr in groups["упоминают"]:
            log.say("    " + T("un.mentions", pr["ProcessId"]))
        own = {int(p["ProcessId"]) for p in ancestors_of(os.getpid(), by_pid)} | {os.getpid()}
        clash = [pr for pr in groups["наши"] if int(pr["ProcessId"]) in own]
        if clash:
            raise Stop(T("stop.own_console",
                         ", ".join(str(p["ProcessId"]) for p in clash)))

        stopped = 0
        for pr in groups["наши"]:
            pid = int(pr["ProcessId"])
            with PinnedProcess(pid) as pinned:
                if not pinned.ok:
                    log.say("    " + T("un.pid_gone", pid))
                    continue
                if IS_WIN:
                    created = pinned.creation_filetime()
                    expected = int(pr.get("Created") or -1)
                    # Допуск 1 мс: снимок отдаёт время с точностью до
                    # микросекунды, ядро — до 100 нс. Переиспользованный
                    # номер расходится на порядки больше.
                    if created is None or abs(created - expected) > 10_000:
                        raise Stop(T("stop.pid_reused", pid))
                if not a.apply:
                    log.say("    " + T("un.would_stop", pid))
                    continue
                if pinned.has_exited():
                    # Обычное дело: останов предыдущего процесса унёс с
                    # собой этого. Останавливать некого — и это успех,
                    # а не отказ: итог тот же, процесс не работает.
                    log.say("    " + T("un.stopped", pid))
                    stopped += 1
                    continue
                if not pinned.terminate():
                    raise Stop(T("stop.terminate_failed", pid))
                # Дождаться фактического завершения: только что убитый
                # процесс ещё держит файлы, и удаление каталога сразу
                # после команды натыкалось бы на занятые файлы.
                for _ in range(50):
                    if pinned.has_exited():
                        break
                    time.sleep(0.2)
                stopped += 1
                log.record("остановлен процесс", pid=pid,
                           командная_строка=pr.get("CommandLine"))
                log.say("    " + T("un.stopped", pid))
        log.counted(T("gate.stop"), len(groups["наши"]),
                    **{T("cnt.stopped"): stopped})

        # --- задача и правило -------------------------------------------
        log.step(T("un.section.task_rule"))
        task_name = ((install.task or {}).get("имя") if install else None) or (
            "cosmos-%s" % port if port else None)
        if task_name is None:
            log.say("    " + T("un.task_no_name"))
        else:
            state, cmdline = task_state(task_name)
            log.counted(T("gate.task"), 1,
                        **{T("cnt.name"): task_name, T("cnt.state"): state})
            if state == PRESENT:
                actions.append(task_name)
                if str(target).lower() not in cmdline.lower():
                    raise Stop(T("stop.task_elsewhere", task_name, target, cmdline))
                if not is_admin():
                    raise Stop(T("stop.need_admin", task_name))
                if a.apply:
                    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"],
                                   capture_output=True)
                    log.record("удалена задача", имя=task_name)
                    # Молча сделанное действие для человека не отличимо от
                    # несделанного: в прошлом прогоне правило удалилось, а
                    # на экране об этом не было ни строки.
                    log.say("    " + T("un.removed", task_name))
                else:
                    log.say("    " + T("un.would_remove_task", task_name))

        fw = install.firewall if install else None
        fw_name = fw.get("имя") if fw else ("COSMOS %s (local subnet)" % port
                                            if port else None)
        if fw is not None and fw.get("было_до_установки"):
            log.say("    " + T("un.rule_preexisting", fw_name))
        elif fw_name is None:
            log.say("    " + T("un.rule_no_name"))
        else:
            state = firewall_state(fw_name)
            log.counted(T("gate.rule"), 1,
                        **{T("cnt.name"): fw_name, T("cnt.state"): state})
            if state == PRESENT:
                actions.append(fw_name)
                if not is_admin():
                    raise Stop(T("stop.need_admin", fw_name))
                if a.apply:
                    subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                    "-Command", "Remove-NetFirewallRule -DisplayName %s"
                                    % json.dumps(fw_name)], capture_output=True)
                    log.record("удалено правило", имя=fw_name)
                    log.say("    " + T("un.removed", fw_name))
                else:
                    log.say("    " + T("un.would_remove_rule", fw_name))

        unit = (install.unit or {}) if install else {}
        if unit.get("путь"):
            log.say("    " + T("un.systemd_unit", unit["путь"]))
            if a.apply:
                subprocess.run(["systemctl", "disable", unit.get("имя", "")],
                               capture_output=True)
                remove_tree(Path(unit["путь"]), log, a.apply)

        # --- удаление ----------------------------------------------------
        log.step(T("un.section.removal"))
        if a.apply:
            log.record("снос начат", цель=str(target),
                       перечень=[str(p) for _, p in roots])
        for kind, p in roots:
            remove_tree(p, log, a.apply)
        if a.apply:
            log.record("снос завершён", цель=str(target))

        # --- пост-проверка ------------------------------------------------
        log.step(T("un.section.postcheck"))
        problems = []
        if a.apply:
            for kind, p in roots:
                if p.exists():
                    # Пустой остаток — не расхождение: удалять в нём
                    # нечего, запись снимется по выходе программы, и он
                    # уже назван владельцу шагом удаления.
                    rest_f, rest_d, _ = walk_tree(p) if p.is_dir() else ([p], [], [])
                    if rest_f or rest_d:
                        problems.append(T("un.left.exists", kind_name(kind), p))
            left = classify_processes(snapshot_processes(), target, launchers)["наши"]
            log.counted(T("gate.processes_after"), 1,
                        **{T("cnt.ours_left"): len(left)})
            if left:
                problems.append(T("un.left.processes",
                                  ", ".join(str(p["ProcessId"]) for p in left)))
            # Объекты системы проверяются ЗАНОВО. Прежде пост-проверка
            # смотрела только каталоги и процессы, поэтому неудавшееся
            # удаление правила или задачи прошло бы под словами «снос
            # выполнен» — успех объявлялся без единой проверки того, что
            # эти объекты действительно исчезли.
            # Счётчик ведётся явно. Прежде остаток считался поиском слов
            # «ещё существует» в уже собранных строках — на любом другом
            # языке такой поиск не нашёл бы ничего и печатал бы «0»
            # при живых объектах системы.
            checked_system, system_left = 0, 0
            if task_name is not None:
                checked_system += 1
                if task_state(task_name)[0] == PRESENT:
                    system_left += 1
                    problems.append(T("un.left.task", task_name))
            if fw_name is not None and not (fw and fw.get("было_до_установки")):
                checked_system += 1
                if firewall_state(fw_name) == PRESENT:
                    system_left += 1
                    problems.append(T("un.left.rule", fw_name))
            log.counted(T("gate.system_after"), checked_system,
                        **{T("cnt.left"): system_left})
            if install is not None:
                log.say("    " + T("un.journal_kept", install.journal))
        else:
            log.say("    " + T("un.dry_ok"))
        for p in problems:
            log.say("    ! %s" % p)
        if problems:
            raise Stop(T("stop.postcheck"))

        log.say("")
        log.say(T("un.result.done") if a.apply else T("un.result.dry"))

        # Холостой прогон, а человек за клавиатурой — спросить и сделать,
        # а не заставлять набирать команду с ключом. Снос идёт вторым
        # проходом целиком: все гейты пересчитываются непосредственно
        # перед удалением, а не берутся из уже устаревшего вердикта.
        if (not a.apply and not a.list and sys.stdin.isatty()
                and (actions or groups["наши"])):
            log.say("")
            try:
                answer = input("  %s %s\n  %s"
                               % (T("un.confirm"), T("ui.yes_no"),
                                  T("ui.prompt"))).strip().lower()
            except (EOFError, KeyboardInterrupt):
                # Поток ввода бывает закрыт даже там, где он выглядит
                # как консоль (запуск из другой программы, обрыв связи).
                # Отсутствие ответа — это «нет», а не авария с трассировкой.
                log.say("")
                log.say(T("un.cancelled"))
                return 0
            yes_words = {"да", "д", "yes", "y", "1", "ha", "иә", "ия",
                         "نعم", T("ui.yes").lower()}
            if answer in yes_words:
                log.close()
                return main(argv_list + ["--apply"])
            log.say("")
            log.say(T("un.cancelled"))
        return 0

    except Stop as exc:
        log.say("")
        log.say(T("un.stop", exc))
        log.say(T("un.stop_tail"))
        log.record("стоп", причина=str(exc))
        return 2
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
