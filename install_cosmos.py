#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Установщик COSMOS на площадку заказчика.

Замысел: машина выясняет о площадке всё, что можно выяснить, и спрашивает
человека ТОЛЬКО там, где ответ нельзя вывести — то есть где решение
принадлежит заказчику (политика данных, доступ, изменения в системе) или
где нужен секрет.

Устройство — четыре этапа, и каждый заканчивается печатным итогом:

  1. ОБСЛЕДОВАНИЕ  — двадцать с лишним проб площадки, ничего не меняется;
  2. ЗАМЫСЕЛ       — из проб выводятся решения; у каждого записан ИСТОЧНИК
                     («определено», «спрошено», «умолчание») и обоснование;
  3. УСТАНОВКА     — шаги, каждый идемпотентен и сообщает, что сделал;
  4. ПРОВЕРКА      — приложение поднимается и отвечает; итог в отчёт.

Правила, унаследованные от самой системы:

  * отказ вместо умолчания: невыполненное жёсткое требование останавливает
    установку с названной причиной, а не приводит к половинчатой;
  * ни одного молчаливого значения: всё, что попало в настройку, названо в
    отчёте вместе с тем, откуда взялось;
  * проверка доказывает, что не прошла вхолостую: печатает, сколько проб
    выполнила и сколько шагов сделала;
  * секреты в отчёт не попадают — только признак «задан».

Запуск (нужен Python 3.11 или новее, посторонних библиотек не требуется):

    python install_cosmos.py                # обследовать, спросить, поставить
    python install_cosmos.py --probe        # только обследование и замысел
    python install_cosmos.py --target D:\\cosmos --port 8095
    python install_cosmos.py --answers ответы.json   # без диалога

Площадки: Windows и Linux. На Linux приложение проверено меньше, чем на
Windows, — установщик скажет об этом прямо и потребует подтверждения.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import socket
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

# Вывод — всегда UTF-8. Иначе в консоли с кодовой страницей 1252/866
# первая же русская строка валит установку исключением кодировки, и
# человек видит не сообщение, а аварию.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


# ------------------------------------------------------------------- языки

# Словарь диалога. Источник — русский; переводы лежат рядом ключ в ключ.
# Встроен в файл, а не вынесен наружу, потому что установщик приносят на
# площадку одним файлом: недостающий словарь означал бы немой диалог.
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
    """Язык выбирается ПЕРВЫМ вопросом и держится до конца установки; он
    же станет языком программы."""
    global LANG
    LANG = code if code in ("ru", "en", "ar", "uz", "kk") else "en"


def T(key: str, *args: object) -> str:
    """Строка диалога на выбранном языке.

    Ключа нет либо перевод не готов — отдаётся русский исходник, а не
    пустота: непереведённая строка неприятна, отсутствующая — непонятна.
    """
    row = MESSAGES.get(key) or {}
    text = row.get(LANG) or row.get("ru") or key
    if not args:
        return text
    try:
        return text % args
    except (TypeError, ValueError):
        # Строка без подстановок — например, перевод ещё не собран и
        # вернулся сам ключ. Недостающий перевод не должен ронять
        # установку: показываем что есть и дописываем значения.
        return "%s %s" % (text, " ".join(str(a) for a in args))


# Имя пробы и имя решения — ВНУТРЕННИЕ ключи: по ним ищут (Survey.get,
# Plan.value), поэтому они остаются русскими и неизменными. Человеческое
# название берётся отсюда В МОМЕНТ ПЕЧАТИ: перевод не должен ломать поиск.
PROBE_TITLES: dict[str, str] = {
    "операционная система": "probe.os",
    "права администратора": "probe.admin",
    "python": "probe.python",
    "модуль venv": "probe.venv",
    "доступ к PyPI": "probe.pypi",
    "git": "probe.git",
    "доступ к исходникам": "probe.repo",
    "JDK": "probe.jdk",
    "Maven": "probe.maven",
    "CPLEX": "probe.cplex",
    "локальная модель (Ollama)": "probe.ollama",
    "cloudflared": "probe.cloudflared",
    "свободные порты": "probe.ports_free",
    "каталог установки": "probe.target",
    "свободное место": "probe.disk",
    "вычислительный ресурс": "probe.capacity",
    "занятые порты COSMOS": "probe.ports_busy",
}

DECISION_TITLES: dict[str, str] = {
    "площадка": "plan.site",
    "доступ к исходникам": "plan.sources",
    "каталог": "plan.dir",
    "порт": "plan.port",
    "область данных": "plan.data_scope",
    "локальная модель": "plan.local_model",
    "адрес Ollama": "plan.ollama_url",
    "выбор модели по умолчанию": "plan.model_default",
    "ключ облачной модели": "plan.api_key",
    "режим входа": "plan.auth",
    "доступ из сети": "plan.net",
    "команда Access": "plan.access_team",
    "аудитория Access": "plan.access_aud",
    "супер-администратор": "plan.super_admin",
    "установка JDK и Maven": "plan.toolchain",
    "сборка решателя": "plan.build_solver",
    "автозапуск": "plan.autostart",
    "потоков решателя": "plan.workers",
    "язык интерфейса": "plan.lang",
}

SOURCE_TITLES: dict[str, str] = {
    "определено": "src.auto",
    "спрошено": "src.asked",
    "умолчание": "src.default",
    "задан ключом": "src.flag",
}

# Значения решений, которые человек читает словом, а машина — сравнением.
# Переводится ТОЛЬКО напечатанное; в плане значение остаётся прежним.
VALUE_TITLES: dict[str, str] = {
    "есть": "plan.val.present",
    "нет": "plan.val.absent",
    "внутренний": "plan.val.internal",
    "внешний": "plan.val.external",
}


def probe_title(name: str) -> str:
    key = PROBE_TITLES.get(name)
    return T(key) if key else name


def decision_title(key: str) -> str:
    k = DECISION_TITLES.get(key)
    return T(k) if k else key


def source_title(source: str) -> str:
    k = SOURCE_TITLES.get(source)
    return T(k) if k else source


def value_title(value: object) -> str:
    k = VALUE_TITLES.get(str(value))
    return T(k) if k else str(value)


REPO_URL_DEFAULT = "https://github.com/eliduc/COSMOS.git"
PY_MIN = (3, 11)
PY_MAX_EXCL = (3, 15)
JDK_MIN = 21
MAVEN_VERSION = "3.9.15"
PORT_DEFAULT = 8090
# Порт модуля обработки данных авиакомпании объявлен в cdpm/webui.py.
# Он лежит внутри прежнего диапазона веб-приложения, поэтому диапазон
# сузился: иначе установщик мог отдать 8095 веб-приложению, а модуль
# при первом запуске упёрся бы в занятый порт — молча и не там, где
# причина.
CDPM_PORT = 8095
# Порт службы каталога шаблонов (шаг 5 каталога). Лежит ВНЕ диапазона
# веб-приложения, поэтому из PORT_RANGE его исключать не нужно: ряд
# «+10» к панели данных выбран так, чтобы третья служба пакета никогда
# не спорила за номер ни с приложением, ни с панелью.
CATALOG_PORT = 8105
# Порт службы решателя этапа 2 (Kotlin / Quarkus). Один номер на пусковой
# файл решателя и на адрес решателя у COSMOS (CREW_SOLVER_URL): COSMOS
# берёт решатель ТОЛЬКО из явной настройки площадки
# (webapp/package_runs.py, SOLVER_URL_ENV), без неё расчёт отказывает.
SOLVER_PORT = 9096
SOLVER_URL = "http://127.0.0.1:%d" % SOLVER_PORT
# Номер версии продукта для подписи внизу интерфейсов (РВ-141): одно
# место в установщике, пусковые файлы всех служб берут его отсюда
# (CREW_PRODUCT_VERSION).
PRODUCT_VERSION = "1.4.10"
PORT_RANGE = tuple(p for p in range(8090, 8100) if p != CDPM_PORT)
# Номера, которые пакет занимает ПОМИМО диапазона веб-приложения. Проба
# занятых портов смотрела только диапазон, поэтому чужой слушатель на
# 8095, 8105 или 9096 всплывал бы не при установке, а при первом запуске
# службы — молча и не там, где причина.
RESERVED_PORTS = (CDPM_PORT, CATALOG_PORT, SOLVER_PORT)
OLLAMA_DEFAULT = "http://127.0.0.1:11434"
DISK_MIN_GB = 3.0

IS_WIN = os.name == "nt"


# --------------------------------------------------------------- обследование

@dataclass
class Probe:
    """Одна проба площадки: что спрашивали, что получили, годится ли."""

    name: str
    value: object = None
    ok: bool = False
    detail: str = ""
    blocking: bool = False       # невыполнение останавливает установку

    def line(self) -> str:
        mark = T("mark.ok") if self.ok else (
            T("mark.stop") if self.blocking else T("mark.no"))
        return "  [%s] %-28s %s" % (mark, probe_title(self.name), self.detail)


def _run(cmd: list[str], timeout: int = 20,
         cwd: Path | None = None) -> tuple[int, str]:
    """Запустить команду, вернуть (код возврата, вывод). Ошибки — не исключения."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           cwd=str(cwd) if cwd else None)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    out = (p.stdout or b"") + (p.stderr or b"")
    return p.returncode, out.decode("utf-8", errors="replace").strip()


# Adoptium (и не только) отвечает 403 на стандартный заголовок клиента
# urllib — представляемся по-человечески.
_UA = {"User-Agent": "cosmos-installer/1.0"}


def _http(url: str, timeout: int = 8) -> tuple[int, str]:
    """GET без посторонних библиотек. Возвращает (код, тело или причина)."""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(1 << 20).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except Exception as exc:                                   # noqa: BLE001
        return 0, str(exc)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) != 0


# Корни установок IBM ILOG — те же, что у продукта (_select_engines.py):
# установщик приходит на площадку один, без дерева кода, поэтому порядок
# поиска повторён здесь, а не импортирован.
CPLEX_STUDIO_ROOTS = (Path(r"C:\Program Files\IBM\ILOG"),) if IS_WIN \
    else (Path("/opt/ibm/ILOG"),)
CPLEX_REL = ("cplex", "bin", "x64_win64" if IS_WIN else "x86-64_linux",
             "cplex.exe" if IS_WIN else "cplex")


def find_cplex() -> str | None:
    """Путь к уже установленному CPLEX либо None. Порядок: CREW_CPLEX_EXE
    (явная настройка — единственный кандидат) → CPLEX_STUDIO_DIR*
    (новейшая версия первой) → cplex в PATH → CPLEX_Studio* под корнями
    IBM ILOG. Ничего не скачивает и не ставит."""
    env = (os.environ.get("CREW_CPLEX_EXE") or "").strip()
    if env:
        return env if Path(env).is_file() else None
    cands: list[Path] = []
    for key in sorted((k for k in os.environ if k.startswith("CPLEX_STUDIO_DIR")),
                      reverse=True):
        home = os.environ.get(key)
        if home:
            cands.append(Path(home).joinpath(*CPLEX_REL))
    found = shutil.which("cplex")
    if found:
        cands.append(Path(found))
    for root in CPLEX_STUDIO_ROOTS:
        try:
            names = sorted((p.name for p in root.iterdir()
                            if p.is_dir() and p.name.startswith("CPLEX_Studio")),
                           reverse=True)
        except OSError:
            names = []
        cands += [root / n / Path(*CPLEX_REL) for n in names]
    return next((str(c) for c in cands if c.is_file()), None)


class Survey:
    """Обследование площадки. Ничего не меняет — только читает."""

    def __init__(self, target: Path, repo_url: str):
        self.target = target
        self.repo_url = repo_url
        self.probes: list[Probe] = []

    def add(self, p: Probe) -> Probe:
        self.probes.append(p)
        return p

    def get(self, name: str) -> Probe | None:
        for p in self.probes:
            if p.name == name:
                return p
        return None

    # -- отдельные пробы --------------------------------------------------

    def probe_os(self) -> None:
        rel = platform.platform()
        proven = IS_WIN
        self.add(Probe(
            "операционная система", platform.system(), True,
            rel if proven else T("probe.os.unproven", rel)))

    def probe_privileges(self) -> None:
        if IS_WIN:
            try:
                import ctypes
                adm = bool(ctypes.windll.shell32.IsUserAnAdmin())      # type: ignore[attr-defined]
            except Exception:                                  # noqa: BLE001
                adm = False
        else:
            adm = hasattr(os, "geteuid") and os.geteuid() == 0
        self.add(Probe("права администратора", adm, True,
                       T("probe.admin.yes") if adm else T("probe.admin.no")))

    def probe_python(self) -> None:
        v = sys.version_info[:3]
        ok = PY_MIN <= v[:2] < PY_MAX_EXCL
        self.add(Probe(
            "python", ".".join(map(str, v)), ok,
            T("probe.python.ok" if ok else "probe.python.bad",
              ".".join(map(str, v)),
              ".".join(map(str, PY_MIN)), ".".join(map(str, PY_MAX_EXCL))),
            blocking=not ok))
        try:
            import venv                                        # noqa: F401
            ven = True
        except ImportError:
            ven = False
        # Debian / Ubuntu кладут модуль venv в сам Python, а ensurepip —
        # в отдельный пакет python3-venv: без него `python -m venv`
        # падает уже при установке. Проверяем оба, со словом, что ставить.
        pip_ok = True
        if ven and not IS_WIN:
            try:
                import ensurepip                               # noqa: F401
            except ImportError:
                pip_ok = False
        ok = ven and pip_ok
        self.add(Probe("модуль venv", ok, ok,
                       T("probe.venv.ok") if ok
                       else T("probe.venv.absent") if not ven
                       else T("probe.venv.no_ensurepip"),
                       blocking=not ok))

    def probe_pypi(self) -> None:
        code, _ = _http("https://pypi.org/simple/", timeout=10)
        ok = code in (200, 301, 302, 403)
        self.add(Probe("доступ к PyPI", code, ok,
                       T("probe.pypi.ok" if ok else "probe.pypi.fail", code),
                       blocking=not ok))

    def probe_git(self) -> None:
        exe = shutil.which("git")
        if not exe:
            self.add(Probe("git", None, False, T("probe.git.absent")))
            return
        code, out = _run([exe, "--version"])
        self.add(Probe("git", exe, code == 0, out.splitlines()[0] if out else exe))

    def probe_repo(self) -> None:
        """Доступ к исходникам проверяется ФАКТОМ, а не по коду HTTP.

        Приватный репозиторий отвечает анонимному запросу тем же 404, что
        и несуществующий, поэтому «404» ничего не решает. Настоящий ответ
        даёт `git ls-remote` с отключёнными приглашениями: он либо
        перечисляет ссылки, либо честно отказывает.
        """
        url = self.repo_url
        exe = shutil.which("git")
        if not exe:
            self.add(Probe("доступ к исходникам", None, False,
                           T("probe.repo.no_git"), blocking=True))
            return
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        try:
            p = subprocess.run([exe, "ls-remote", url], capture_output=True,
                               timeout=60, env=env)
            out = ((p.stdout or b"") + (p.stderr or b"")).decode(
                "utf-8", errors="replace")
            code = p.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            code, out = 127, str(exc)
        if code == 0 and "refs/heads" in out:
            self.add(Probe("доступ к исходникам", url, True,
                           T("probe.repo.ok")))
            return
        why = T("probe.repo.denied")
        if "could not read Username" in out or "Authentication failed" in out:
            why = T("probe.repo.need_key")
        elif "credential store" in out:
            why = T("probe.repo.no_helper")
        elif "not found" in out.lower():
            why = T("probe.repo.not_found")
        self.add(Probe("доступ к исходникам", url, False, why, blocking=True))

    def probe_java(self) -> None:
        exe = shutil.which("java")
        if not exe:
            self.add(Probe("JDK", None, False, T("probe.jdk.absent")))
            return
        code, out = _run([exe, "-version"])
        m = re.search(r'version "?(\d+)', out or "")
        major = int(m.group(1)) if m else 0
        ok = major >= JDK_MIN
        self.add(Probe("JDK", major, ok,
                       T("probe.jdk.ok", major) if ok
                       else T("probe.jdk.old", major, JDK_MIN)))

    def probe_maven(self) -> None:
        exe = shutil.which("mvn")
        if not exe:
            self.add(Probe("Maven", None, False, T("probe.maven.absent")))
            return
        code, out = _run([exe, "-v"])
        self.add(Probe("Maven", exe, code == 0,
                       (out.splitlines()[0] if out else exe)[:60]))

    def probe_cplex(self) -> None:
        """CPLEX установщик не ставит — лишь находит уже установленный
        (слово владельца 04.10, РВ-142/143). Найден — путь уходит в
        пусковой файл COSMOS как CREW_CPLEX_EXE и движок cplex доступен;
        не найден — не ошибка: расчёт идёт на CP-SAT, движке по умолчанию.
        Порядок поиска — тот же, что у продукта (_select_engines.py)."""
        exe = find_cplex()
        self.add(Probe("CPLEX", exe, True,
                       T("probe.cplex.found", exe) if exe
                       else T("probe.cplex.absent")))

    def probe_ollama(self, url: str = OLLAMA_DEFAULT) -> None:
        code, body = _http(url.rstrip("/") + "/api/tags", timeout=5)
        if code != 200:
            self.add(Probe("локальная модель (Ollama)", None, False,
                           T("probe.ollama.silent", url)))
            return
        try:
            names = [m.get("name") for m in json.loads(body).get("models", [])]
        except Exception:                                      # noqa: BLE001
            names = []
        self.add(Probe("локальная модель (Ollama)", names, bool(names),
                       T("probe.ollama.models", len(names),
                         ", ".join(str(n) for n in names[:3])) if names
                       else T("probe.ollama.no_models")))

    def probe_cloudflared(self) -> None:
        exe = shutil.which("cloudflared")
        self.add(Probe("cloudflared", exe, bool(exe),
                       T("probe.cloudflared.ok") if exe
                       else T("probe.cloudflared.absent")))

    def probe_ports(self) -> None:
        # Веб-приложению отдаётся только диапазон: номера служб (панель
        # данных, служба каталога) заняты пакетом и предлагаться не
        # могут. Их занятость показывает проба «занятые порты COSMOS».
        free = [p for p in PORT_RANGE if _port_free(p)]
        self.add(Probe("свободные порты", free, bool(free),
                       T("probe.ports_free.ok", ", ".join(map(str, free[:5])))
                       if free else T("probe.ports_free.none",
                                      PORT_RANGE[0], PORT_RANGE[-1]),
                       blocking=not free))

    def probe_target(self) -> None:
        t = self.target
        exists = t.exists()
        parent = t if exists else t.parent
        writable = os.access(parent, os.W_OK) if parent.exists() else False
        occupied = exists and any(t.iterdir()) if exists else False
        det = str(t)
        if occupied:
            det = T("probe.target.busy", t)
        elif not writable:
            det = T("probe.target.readonly", t)
        self.add(Probe("каталог установки", str(t), writable, det,
                       blocking=not writable))

    def probe_disk(self) -> None:
        base = self.target if self.target.exists() else self.target.parent
        try:
            free_gb = shutil.disk_usage(base).free / 2 ** 30
        except OSError as exc:
            self.add(Probe("свободное место", None, False, str(exc)))
            return
        ok = free_gb >= DISK_MIN_GB
        self.add(Probe("свободное место", round(free_gb, 1), ok,
                       T("probe.disk.ok", "%.1f" % free_gb) if ok
                       else T("probe.disk.low", "%.1f" % free_gb,
                              "%.0f" % DISK_MIN_GB),
                       blocking=not ok))

    def probe_capacity(self) -> None:
        cpu = os.cpu_count() or 1
        ram = None
        try:
            if IS_WIN:
                code, out = _run(["wmic", "ComputerSystem", "get",
                                  "TotalPhysicalMemory"])
                m = re.search(r"(\d{9,})", out or "")
                ram = int(m.group(1)) / 2 ** 30 if m else None
            else:
                for line in Path("/proc/meminfo").read_text().splitlines():
                    if line.startswith("MemTotal"):
                        ram = int(re.search(r"(\d+)", line).group(1)) / 2 ** 20
                        break
        except Exception:                                      # noqa: BLE001
            ram = None
        workers = max(1, min(cpu - 2, 16))
        self.add(Probe("вычислительный ресурс", {"cpu": cpu, "ram_gb": ram},
                       True,
                       T("probe.capacity.ram", cpu, "%.0f" % ram, workers)
                       if ram else T("probe.capacity.ok", cpu, workers)))

    def probe_existing(self) -> None:
        # Диапазон веб-приложения И номера служб пакета: панель данных
        # (8095) и служба каталога шаблонов (8105) живут вне диапазона,
        # и прежде их занятость этой пробе была не видна.
        busy = [p for p in tuple(PORT_RANGE) + RESERVED_PORTS
                if not _port_free(p)]
        self.add(Probe("занятые порты COSMOS", busy, True,
                       T("probe.ports_busy.busy", ", ".join(map(str, busy)))
                       if busy else T("probe.ports_busy.none")))

    def run(self) -> None:
        for fn in (self.probe_os, self.probe_privileges, self.probe_python,
                   self.probe_pypi, self.probe_git, self.probe_repo,
                   self.probe_java, self.probe_maven, self.probe_cplex,
                   self.probe_ollama,
                   self.probe_cloudflared, self.probe_ports,
                   self.probe_target, self.probe_disk, self.probe_capacity,
                   self.probe_existing):
            fn()

    # -- итог -------------------------------------------------------------

    def report(self) -> None:
        print("\n" + T("inst.stage.survey") + "\n")
        for p in self.probes:
            print(p.line())
        blockers = [p for p in self.probes if p.blocking and not p.ok]
        print("\n  " + T("inst.probes_done", len(self.probes), len(blockers)))
        if not self.probes:
            raise SystemExit("обследование не выполнило ни одной пробы — отказ")

    def blockers(self) -> list[Probe]:
        return [p for p in self.probes if p.blocking and not p.ok]


# -------------------------------------------------------------------- замысел

@dataclass
class Decision:
    """Решение установки: значение, откуда взято и почему именно такое."""

    key: str
    value: object
    source: str                  # определено | спрошено | умолчание
    why: str
    secret: bool = False

    def line(self) -> str:
        shown = decision_value(self)
        return "  %-26s %-34s [%s] %s" % (decision_title(self.key), shown,
                                          source_title(self.source), self.why)


def decision_value(d: "Decision") -> str:
    """Значение решения так, как его видит человек. Секрет не печатается
    никогда — только признак «задан»."""
    if d.secret:
        return T("plan.secret_set") if d.value else T("plan.secret_unset")
    return value_title(d.value)


@dataclass
class Plan:
    decisions: list[Decision] = field(default_factory=list)

    def set(self, key: str, value: object, source: str, why: str,
            secret: bool = False) -> None:
        self.decisions.append(Decision(key, value, source, why, secret))

    def value(self, key: str, default: object = None) -> object:
        for d in self.decisions:
            if d.key == key:
                return d.value
        return default

    def report(self) -> None:
        print("\n" + T("inst.stage.plan") + "\n")
        for d in self.decisions:
            if d.key.startswith("_"):
                continue        # внутренние значения человеку не показываются
            print(d.line())
        asked = sum(1 for d in self.decisions if d.source == "спрошено")
        auto = sum(1 for d in self.decisions if d.source == "определено")
        print("\n  " + T("inst.decisions_done", len(self.decisions), auto, asked))


# -------------------------------------------------------------------- вопросы

class Asker:
    """Диалог. Без человека на том конце берёт ответы из поданного файла
    либо умолчания — и говорит об этом, а не делает вид, что спросил."""

    def __init__(self, answers: dict | None, interactive: bool):
        self.answers = answers or {}
        self.interactive = interactive
        self.asked: list[str] = []

    @staticmethod
    def _read(prompt: str) -> str | None:
        """Ввод, устойчивый к отсутствию потока: EOF — это не ошибка, а
        «человека нет», и тогда действует умолчание."""
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def choice(self, key: str, question: str, options: list[tuple[str, str]],
               default: str) -> str:
        """Выбор из вариантов. Человек отвечает НОМЕРОМ: набирать слова —
        мучение, особенно кириллицей. Enter — безопасное умолчание."""
        if key in self.answers:
            return str(self.answers[key])
        if not self.interactive:
            return default
        print("\n  " + question)
        for i, (code, text) in enumerate(options, 1):
            mark = T("ui.enter_hint") if code == default else ""
            print("     %d) %s%s" % (i, text, mark))
        codes = [c for c, _ in options]
        while True:
            raw = self._read("  " + T("ui.enter_number", len(options)))
            if raw is None:
                print("  " + T("ui.no_input"))
                return default
            got = raw.strip()
            if not got:
                self.asked.append(key)
                return default
            if got.isdigit() and 1 <= int(got) <= len(options):
                self.asked.append(key)
                return codes[int(got) - 1]
            if got in codes:                    # файл ответов и привычка
                self.asked.append(key)
                return got
            print("  " + T("ui.need_number", len(options)))

    def text(self, key: str, question: str, default: str = "",
             secret: bool = False) -> str:
        if key in self.answers:
            return str(self.answers[key])
        if not self.interactive:
            return default
        hint = (" [%s]" % default) if default else ""
        raw = self._read("\n  %s%s\n  > " % (question, hint))
        if raw is None:
            return default
        self.asked.append(key)
        return raw.strip() or default

    def yes(self, key: str, question: str, default: bool = False) -> bool:
        if key in self.answers:
            return bool(self.answers[key])
        if not self.interactive:
            return default
        d = T("ui.yes") if default else T("ui.no")
        raw = self._read("\n  %s %s [%s]\n  %s"
                         % (question, T("ui.yes_no"), d, T("ui.prompt")))
        if raw is None:
            return default
        got = raw.strip().lower()
        self.asked.append(key)
        if not got:
            return default
        # Принимаем «да» на всех пяти языках и латиницей: человек может
        # ответить привычным словом, даже если диалог идёт на другом языке.
        yes_words = {"да", "д", "yes", "y", "1", "ha", "h", "иә", "ия",
                     "نعم", T("ui.yes").lower()}
        return got in yes_words


def build_plan(s: Survey, ask: Asker, args) -> Plan:
    """Свести пробы и ответы в набор решений. Спрашиваем только неизбежное."""
    p = Plan()

    # --- площадка: подтверждение, если она не Windows
    if not IS_WIN:
        ok = ask.yes("linux_ack", T("q.linux_ack"), default=False)
        if not ok and not args.probe:
            raise SystemExit("  установка прекращена: площадка не подтверждена")
        p.set("площадка", platform.system(),
              "спрошено" if "linux_ack" in ask.asked else "умолчание",
              T("why.site.unproven_ok" if ok else "why.site.unproven_no"))
    else:
        p.set("площадка", platform.system(), "определено", T("why.site.proven"))

    # --- доступ к исходным текстам (проверен фактом на этапе обследования)
    repo = s.get("доступ к исходникам")
    p.set("доступ к исходникам", "есть" if (repo and repo.ok) else "нет",
          "определено", (repo.detail if repo else T("why.sources.unchecked")))

    # --- каталог и порт
    p.set("каталог", str(s.target), "определено" if not args.target else "задан ключом",
          T("why.dir"))

    free = s.get("свободные порты").value or []
    if args.port:
        port = args.port
        src, why = "задан ключом", T("why.port.flag")
    elif PORT_DEFAULT in free:
        port, src, why = PORT_DEFAULT, "определено", T("why.port.default_free")
    else:
        port = free[0]
        src, why = "определено", T("why.port.taken", PORT_DEFAULT)
    p.set("порт", port, src, why)
    # Порт службы каталога шаблонов — тоже решение замысла, а не константа
    # кода: на рабочем месте владельца 8105 занят его же местной службой,
    # и проверка установки на фиксированном номере била по чужому
    # слушателю (цепочка CR, 09.09.2026). Ключ --catalog-port; без него —
    # умолчание пакета.
    if getattr(args, "catalog_port", None):
        p.set("порт каталога", int(args.catalog_port), "задан ключом",
              T("why.port.flag"))
    else:
        p.set("порт каталога", CATALOG_PORT, "определено",
              T("why.catalog_port.default", CATALOG_PORT))

    # --- ОБЛАСТЬ ДАННЫХ: решение заказчика, машине не выводимо
    # Признак «только локально / любая модель» стоит НА КАЖДОМ НАБОРЕ
    # данных, и правило маршрутизации сквозное: затронут хотя бы один
    # набор «только локально» — отвечает исключительно локальная модель.
    # Поэтому вопрос установки решает только одно: бывают ли облачные
    # модели на этой площадке В ПРИНЦИПЕ.
    contour = ask.choice(
        "contour", T("q.contour"),
        [("внутренний", T("q.contour.internal")),
         ("внешний", T("q.contour.external"))],
        default="внутренний")
    p.set("область данных", contour,
          "спрошено" if "contour" in ask.asked else "умолчание",
          T("why.data_scope"))

    # --- локальная модель
    #
    # Её судьбу нельзя решать молча. Признак «только локально» стоит на
    # наборах заказчика по умолчанию, и отвечает по ним ИСКЛЮЧИТЕЛЬНО
    # локальная модель. Значит без неё подсказки ИИ по данным заказчика
    # отключены — и в облачном контуре тоже, хотя ключ облака задан. Это
    # обязано быть сказано человеку, а не выведено им самим постфактум.
    ol = s.get("локальная модель (Ollama)")
    models = [str(m) for m in (ol.value or [])] if (ol and ol.ok) else []
    url = OLLAMA_DEFAULT

    if not models:
        # Сервер бывает на другой машине или порту — спросить, а не
        # решать за владельца по одной пробе стандартного адреса.
        got = ask.text("ollama_url", T("q.ollama_url"), default="")
        if got:
            url = got.strip().rstrip("/")
            code, body = _http(url + "/api/tags", timeout=8)
            if code == 200:
                try:
                    models = [str(m.get("name"))
                              for m in json.loads(body).get("models", [])]
                except Exception:                              # noqa: BLE001
                    models = []
            if not models:
                print("  " + T("warn.ollama_silent", url))

    if models:
        if len(models) == 1:
            model = models[0]
            p.set("локальная модель", model, "определено",
                  T("why.model.single"))
        else:
            model = ask.choice("ollama_model", T("q.ollama_model"),
                               [(m, m) for m in models], default=models[0])
            p.set("локальная модель", model,
                  "спрошено" if "ollama_model" in ask.asked else "умолчание",
                  T("why.model.choice"))
        p.set("адрес Ollama", url,
              "спрошено" if "ollama_url" in ask.asked else "определено",
              T("why.ollama_url"))
        backend = "local"
    else:
        p.set("локальная модель", None,
              "спрошено" if "ollama_url" in ask.asked else "определено",
              T("why.model.none"))
        backend = "none" if contour == "внутренний" else "cloud"
        # Прямое предупреждение, а не строчка в таблице: без локальной
        # модели наборы заказчика остаются без подсказок ИИ при любом
        # контуре — в облако они не уходят никогда.
        print("  " + T("warn.no_local_model"))
    p.set("выбор модели по умолчанию", backend, "определено",
          T("why.model_default"))

    # --- ключ облачной модели: секрет, спрашиваем только когда он применим
    if contour == "внешний":
        key = ask.text("api_key", T("q.api_key"), "", secret=True)
        p.set("ключ облачной модели", bool(key),
              "спрошено" if "api_key" in ask.asked else "умолчание",
              T("why.api_key.external"), secret=True)
        p.set("_api_key_value", key, "спрошено", "хранится в окружении", secret=True)
    else:
        p.set("ключ облачной модели", False, "определено",
              T("why.api_key.internal"), secret=True)

    # --- режим входа. Значения — РОВНО те, что понимает приложение
    # (auth.get_auth_mode: access | token | dev). Первая установка на
    # живую машину упала на выдуманном режиме "local": приложение честно
    # отказалось стартовать, а проверка установщика этого не увидела,
    # потому что запускала приложение мимо пускового файла.
    cf = s.get("cloudflared")
    if cf and cf.ok:
        mode = ask.choice(
            "auth", T("q.auth"),
            [("token", T("q.auth.token")),
             ("access", T("q.auth.access"))],
            default="token")
    else:
        mode = "token"
    p.set("режим входа", mode,
          "спрошено" if "auth" in ask.asked else "определено",
          T("why.auth.token") if mode == "token" else T("why.auth.access"))

    # --- доступ из сети: изменение видимости — решение человека
    net = ask.choice(
        "net", T("q.net"),
        [("эта", T("q.net.this")),
         ("сеть", T("q.net.lan"))],
        default="эта")
    p.set("доступ из сети", net == "сеть",
          "спрошено" if "net" in ask.asked else "умолчание",
          T("why.net"))

    def src(key: str) -> str:
        """Источник значения: спрошено у человека или взято умолчанием."""
        return "спрошено" if key in ask.asked else "умолчание"

    if mode == "access":
        team = ask.text("access_team", T("q.access_team"))
        aud = ask.text("access_aud", T("q.access_aud"))
        p.set("команда Access", team, src("access_team"), T("why.access"))
        p.set("аудитория Access", aud, src("access_aud"), T("why.access"))

    # --- супер-администратор: один адрес или список через запятую.
    # Значение целиком уходит в CREW_SUPER_ADMIN пускового файла: первый
    # адрес — супер-администратор «Установок», весь список — администраторы
    # первого запуска. Вшитых адресов в приложении больше нет (пакет
    # «умолчания-в-данные», 2026-08-29): пусто → админы не сеются.
    admin_mail = ask.text("super_admin_email", T("q.super_admin"))
    admin_mail = ",".join(part.strip() for part in admin_mail.split(",")
                          if part.strip())
    p.set("супер-администратор", admin_mail, src("super_admin_email"),
          T("why.super_admin"))

    # --- решатель этапа 2 и набор инструментов для него
    java = s.get("JDK")
    mvn = s.get("Maven")
    have_tools = bool(java and java.ok and mvn and mvn.ok)
    if have_tools:
        p.set("установка JDK и Maven", False, "определено",
              T("why.toolchain.present"))
        p.set("сборка решателя", True, "определено",
              T("why.solver.ready", java.value))
    else:
        missing = []
        if not (java and java.ok):
            missing.append(T("why.missing.jdk", JDK_MIN))
        if not (mvn and mvn.ok):
            missing.append("Maven")
        want = ask.yes("install_toolchain",
                       T("q.install_toolchain", ", ".join(missing)),
                       default=False)
        p.set("установка JDK и Maven", want,
              "спрошено" if "install_toolchain" in ask.asked else "умолчание",
              T("why.toolchain.allowed") if want
              else T("why.toolchain.denied"))
        p.set("сборка решателя", want, "определено",
              T("why.solver.will_build") if want
              else T("why.solver.missing", ", ".join(missing)))

    # --- автозапуск: изменение системы, спрашиваем всегда
    adm = s.get("права администратора")
    if adm and adm.value:
        auto = ask.yes("autostart",
                       T("q.autostart", T("q.autostart.task") if IS_WIN
                         else "systemd"),
                       default=False)
    else:
        auto = False
    p.set("автозапуск", auto,
          "спрошено" if "autostart" in ask.asked else "определено",
          T("why.autostart.allowed") if (adm and adm.value)
          else T("why.autostart.no_admin"))

    # --- потоки решателя
    cap = s.get("вычислительный ресурс")
    cpu = (cap.value or {}).get("cpu", 1) if cap else 1
    p.set("потоков решателя", max(1, min(cpu - 2, 16)), "определено",
          T("why.workers"))

    return p


# ------------------------------------------------------------------ установка

@dataclass
class Step:
    name: str
    done: bool
    detail: str

    def line(self) -> str:
        return "  [%s] %-34s %s" % (T("mark.ok") if self.done else T("mark.no"),
                                    self.name, self.detail)


class _Heartbeat:
    """Голос долгого шага: раз в 20 секунд печатает, что работа идёт.

    Клонирование, установка зависимостей, загрузка JDK и сборка решателя
    занимают минуты, и молчащее окно неотличимо от зависшего — человек
    у первой установки так и решил.
    """

    def __init__(self, label: str, every: float = 20.0):
        self.label = label
        self.every = every
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_Heartbeat":
        def beat() -> None:
            while not self._stop.wait(self.every):
                sec = int(time.monotonic() - self._t0)
                elapsed = "%d:%02d" % (sec // 60, sec % 60)
                print("        %s: %s" % (self.label, T("inst.heartbeat", elapsed)),
                      flush=True)
        self._thread = threading.Thread(target=beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


class Checkpoints:
    """Журнал созданного — контрольные точки установки.

    Зачем. Деинсталляция без такого журнала вынуждена УГАДЫВАТЬ, что на
    машине принадлежит установке: разбирать дерево, сверять с системой
    контроля версий, опознавать процессы по косвенным признакам. Каждая
    догадка — риск задеть чужое. Журнал превращает догадку в факт:
    установщик записывает объект СРАЗУ после создания и сбрасывает запись
    на диск, поэтому список верен и тогда, когда установку прервали на
    середине или убили с консоли.

    Журнал живёт ВНЕ каталога установки: он должен пережить и сам этот
    каталог, и неудачную попытку его создать.
    """

    FORMAT = 1

    def __init__(self, target: Path, data: Path):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) \
            if IS_WIN else Path.home() / ".local" / "state"
        self.dir = base / "cosmos-install"
        self.path = self.dir / ("install-%s.jsonl"
                                % datetime.now().strftime("%Y%m%d-%H%M%S"))
        self._fh = None
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        except OSError:
            self._fh = None                      # журнал не обязан валить установку
        self.write("начало", формат=self.FORMAT, цель=str(target),
                   каталог_данных=str(data), площадка="windows" if IS_WIN else "linux",
                   цель_существовала=target.exists())

    def write(self, kind: str, **payload) -> None:
        if self._fh is None:
            return
        row = {"время": datetime.now().isoformat(timespec="seconds"), "запись": kind}
        row.update(payload)
        try:
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())          # прерывание не должно съесть запись
        except OSError:
            pass

    def created(self, kind: str, path: Path | str, **extra) -> None:
        """Объект создан установщиком. `kind` — что это: каталог цели,
        каталог данных, окружение, набор инструментов, пусковой файл,
        файл правил, сборка решателя."""
        self.write("создан объект", вид=kind, путь=str(path), **extra)

    def note(self, kind: str, **payload) -> None:
        self.write(kind, **payload)

    def finish(self, ok: bool) -> None:
        self.write("установка завершена", итог="успех" if ok else "с ошибками")
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


class Installer:
    def __init__(self, target: Path, plan: Plan, repo_url: str, survey: Survey,
                 checkpoints: "Checkpoints | None" = None):
        self.target = target
        self.plan = plan
        self.repo_url = repo_url
        self.survey = survey
        self.steps: list[Step] = []
        self.cp = checkpoints or Checkpoints(target, target.parent / (target.name + "-data"))

    def step(self, name: str, fn: Callable[[], str]) -> bool:
        print("  [ .. ] %-34s %s" % (name, T("inst.running")), flush=True)
        try:
            with _Heartbeat(name):
                detail = fn()
            self.steps.append(Step(name, True, detail))
            print(self.steps[-1].line())
            return True
        except Exception as exc:                               # noqa: BLE001
            self.steps.append(Step(name, False, str(exc)[:160]))
            print(self.steps[-1].line())
            return False

    # -- шаги -------------------------------------------------------------

    def fetch_sources(self) -> str:
        if (self.target / ".git").exists():
            code, out = _run(["git", "-C", str(self.target), "pull", "--ff-only"],
                             timeout=600)
            if code != 0:
                raise RuntimeError("обновление не удалось: %s" % out[-200:])
            return T("res.sources.updated")
        if not shutil.which("git"):
            raise RuntimeError("git не найден — принесите исходники вручную")
        existed = self.target.exists()
        self.target.parent.mkdir(parents=True, exist_ok=True)
        # `--no-hardlinks` обязателен: при клонировании из локального пути
        # git по умолчанию делает объекты ЖЁСТКИМИ ССЫЛКАМИ на исходный
        # репозиторий. Тогда удаление установки трогает общую для обоих
        # имён запись файловой системы — то есть задевает чужой оригинал.
        # На клонирование по сети ключ не влияет.
        code, out = _run(["git", "clone", "--depth", "1", "--no-hardlinks",
                          self.repo_url, str(self.target)], timeout=1800)
        if code != 0:
            raise RuntimeError("клонирование не удалось: %s" % out[-200:])
        # Контрольная точка ставится ТОЛЬКО если каталог создали мы: снос
        # не вправе удалять каталог, существовавший до установки.
        if not existed:
            self.cp.created("каталог цели", self.target, источник=self.repo_url)
        return T("res.sources.cloned", self.repo_url)

    def make_venv(self) -> str:
        venv = self.target / ".venv"
        if not venv.exists():
            code, out = _run([sys.executable, "-m", "venv", str(venv)], timeout=600)
            if code != 0:
                raise RuntimeError(out[-200:])
            self.cp.created("окружение python", venv)
        return str(venv)

    def _py(self) -> Path:
        venv = self.target / ".venv"
        return venv / ("Scripts/python.exe" if IS_WIN else "bin/python")

    def install_deps(self) -> str:
        py = self._py()
        code, out = _run([str(py), "-m", "pip", "install", "--upgrade", "pip"],
                         timeout=900)
        code, out = _run([str(py), "-m", "pip", "install", "-e", ".[dev]"],
                         timeout=3600)
        if code != 0:
            raise RuntimeError("установка зависимостей не удалась: %s" % out[-300:])
        return T("res.deps.ok")

    def make_data_dir(self) -> str:
        data = self.target.parent / (self.target.name + "-data")
        existed = data.exists()
        (data / "final-report").mkdir(parents=True, exist_ok=True)
        (data / "rules").mkdir(parents=True, exist_ok=True)
        # Прогоны модуля обработки данных: cdpm/webui.py пишет сюда.
        (data / "cdpm-runs").mkdir(parents=True, exist_ok=True)
        if not existed:
            self.cp.created("каталог данных", data)
        else:
            self.cp.note("каталог данных существовал до установки", путь=str(data))
        return str(data)

    def make_rules_config(self) -> str:
        """Создать действующий файл правил из образца.

        Файл `webapp/config/airline_default.json` намеренно не под учётом
        (в нём параметры конкретной авиакомпании), поэтому в свежем клоне
        его нет. Без него расчёт молча возьмёт встроенные умолчания — это
        ровно тот класс тихой ошибки, который система запрещает. Образец
        под учётом есть; установка обязана превратить его в действующий
        файл и сказать об этом.
        """
        cfg = self.target / "webapp" / "config" / "airline_default.json"
        example = cfg.with_suffix(".json.example")
        if cfg.exists():
            return T("res.rules.exists", cfg)
        if not example.exists():
            raise RuntimeError("нет ни файла правил, ни образца рядом с ним")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example, cfg)
        self.cp.created("файл правил", cfg)
        return T("res.rules.created", cfg)

    def make_agents_layout(self) -> str:
        """Создать площадочный носитель раскладки моделей ролей из образца.

        Носитель `<каталог данных>/agents-default.json` живёт вне
        репозитория (в нём выбор моделей конкретной площадки), поэтому в
        свежей установке его нет. С ревизии 1.18.5 (S06-R2, 06.09.2026)
        модуль обработки данных не подставляет облачную модель «по
        умолчанию канала»: без объявленной раскладки облачные стадии
        отказывают словами до первого вызова модели. Образец под учётом
        (`cdpm/agents-default.json.example`) установка обязана превратить
        в действующий носитель и сказать об этом; существующий носитель
        не трогается. Ключей провайдеров в носителе нет.
        """
        data = self.target.parent / (self.target.name + "-data")
        carrier = data / "agents-default.json"
        example = self.target / "cdpm" / "agents-default.json.example"
        if carrier.exists():
            return T("res.layout.exists", carrier)
        if not example.exists():
            raise RuntimeError("нет ни носителя раскладки, ни образца cdpm/agents-default.json.example")
        data.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example, carrier)
        # Отпечаток созданных байтов — в журнал: снос по журналу удаляет
        # носитель только если он и сейчас тот же файл, что создала
        # установка; изменённый владельцем носитель — его данные
        # (GPT-062, P1 INSTALLER-LAYOUT-UNINSTALL).
        import hashlib
        digest = hashlib.sha256(carrier.read_bytes()).hexdigest()
        self.cp.created("носитель раскладки ролей", carrier, sha256=digest)
        return T("res.layout.created", carrier)

    def write_manifest(self) -> str:
        """Отпечаток ВСЕГО дерева установки — по объекту на строку.

        Зачем. Без него удаление вынуждено принимать целые поддеревья
        «на веру»: клон с историей, окружение python, загруженный набор
        инструментов — это 16 тысяч объектов из 19, которые снос не
        проверял пофайлово и о слепоте которых честно предупреждал. Чужой
        файл, перенесённый туда владельцем, оставался незамеченным.

        Манифест снимается ПОСЛЕ установки, но ДО проверки: проверка
        поднимает приложение, а оно пишет свои рабочие файлы, и они
        должны попасть не в манифест, а в разряд «порождено работой».

        Опись живёт рядом с журналом установки, вне каталога цели: она
        обязана пережить и сам этот каталог.

        Устройство выбрано так, чтобы она не стала обузой в профиле
        пользователя: пути ОТНОСИТЕЛЬНЫЕ (корень назван один раз в
        заголовке), строка — не словарь, а короткий список, и весь файл
        сжат. На дереве в девятнадцать тысяч объектов это разница между
        мегабайтами и сотнями килобайт.
        """
        path = self.cp.dir / (self.cp.path.stem.replace("install-", "manifest-")
                              + ".jsonl.gz")
        files = dirs = 0
        errors: list[str] = []

        def onerror(exc: OSError) -> None:
            errors.append("%s: %s" % (getattr(exc, "filename", "?"), exc))

        try:
            with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
                # Заголовок: корень и версия формата. Дальше — по строке
                # на объект: [путь, размер, время] у файла, [путь] у
                # каталога. Размер и время нужны сносу, чтобы отличить
                # продукт установки от чужого файла, положенного под тем
                # же именем.
                fh.write(json.dumps({"корень": str(self.target), "формат": 2},
                                    ensure_ascii=False) + "\n")
                for base, subdirs, names in os.walk(self.target, onerror=onerror):
                    b = Path(base)
                    for d in subdirs:
                        dirs += 1
                        rel = str((b / d).relative_to(self.target))
                        fh.write(json.dumps([rel], ensure_ascii=False) + "\n")
                    for n in names:
                        p = b / n
                        try:
                            st = os.stat(p, follow_symlinks=False)
                        except OSError as exc:
                            errors.append("%s: %s" % (p, exc))
                            continue
                        files += 1
                        rel = str(p.relative_to(self.target))
                        fh.write(json.dumps(
                            [rel, st.st_size, int(st.st_mtime_ns)],
                            ensure_ascii=False) + "\n")
        except OSError as exc:
            # Манифест — усиление проверки, а не условие установки: без
            # него снос просто вернётся к прежней осторожности.
            self.cp.write("манифест дерева не записан", причина=str(exc))
            return T("res.manifest.failed", exc)
        if errors:
            # Неполный манифест хуже отсутствующего: по нему уцелевшие
            # объекты выглядели бы посторонними. Лучше честно без него.
            path.unlink(missing_ok=True)
            self.cp.write("манифест дерева не записан",
                          причина="перечисление дерева дало ошибки: %d" % len(errors))
            return T("res.manifest.failed", errors[0])
        self.cp.write("манифест дерева", путь=str(path),
                      файлов=files, каталогов=dirs)
        return T("res.manifest.ok", files + dirs, path)

    # -- набор инструментов решателя -------------------------------------

    def _tools_dir(self) -> Path:
        return self.target / ".tools"

    def _find_tool(self, pattern: str, exe: str) -> Path | None:
        """Найти исполняемый файл внутри распакованного набора."""
        base = self._tools_dir()
        if not base.is_dir():
            return None
        for d in sorted(base.glob(pattern)):
            p = d / "bin" / exe
            if p.exists():
                return p
        return None

    def java_home(self) -> Path | None:
        exe = self._find_tool("jdk*", "java.exe" if IS_WIN else "java")
        return exe.parent.parent if exe else None

    def maven_cmd(self) -> str | None:
        exe = self._find_tool("apache-maven*", "mvn.cmd" if IS_WIN else "mvn")
        return str(exe) if exe else shutil.which("mvn")

    @staticmethod
    def _fetch(url: str, dest: Path, expect_sha: str | None) -> None:
        """Загрузить файл и СВЕРИТЬ контрольную сумму.

        Несовпадение — отказ: загруженное из сети без проверки в дерево
        установки не попадает.
        """
        import hashlib
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=300) as r, \
                open(dest, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            shown = 0
            digest = hashlib.sha256()
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                fh.write(chunk)
                got += len(chunk)
                # ход загрузки каждые ~20 МБ: молчание при 190 МБ JDK
                # выглядит как зависание
                if got - shown >= 20 * (1 << 20):
                    shown = got
                    if total:
                        print("        %s: %s"
                              % (dest.name, T("inst.downloading", got >> 20,
                                              total >> 20)), flush=True)
                    else:
                        print("        %s: %s"
                              % (dest.name, T("inst.downloaded_mb",
                                              got >> 20)), flush=True)
        got = digest.hexdigest()
        if expect_sha and got.lower() != expect_sha.lower():
            dest.unlink(missing_ok=True)
            raise RuntimeError("контрольная сумма не совпала: ожидалось %s, "
                               "получено %s" % (expect_sha[:16], got[:16]))

    @staticmethod
    def _unpack(archive: Path, into: Path) -> None:
        into.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(archive), str(into))
        if not IS_WIN and str(archive).lower().endswith(".zip"):
            # zip хранит права Unix в external_attr, а распаковка их не
            # восстанавливает: `mvn` из архива Maven без бита запуска
            # отказал бы при сборке решателя. Возвращаем права из архива.
            import zipfile
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    mode = (info.external_attr >> 16) & 0o777
                    if mode & 0o111:
                        p = into / info.filename
                        if p.is_file():
                            p.chmod(p.stat().st_mode | (mode & 0o111))

    def install_toolchain(self) -> str:
        """JDK 21 и Maven — с официальных источников, в каталог установки."""
        if not self.plan.value("установка JDK и Maven"):
            return T("res.skipped.not_allowed")
        tools = self._tools_dir()
        existed = tools.exists()
        tools.mkdir(parents=True, exist_ok=True)
        if not existed:
            self.cp.created("набор инструментов", tools)
        done = []

        # Системный JDK годится — загружать свой незачем: на первой
        # установке скачались лишние 190 МБ при живом JDK 21 в системе.
        sys_jdk = self.survey.get("JDK")
        need_jdk = not self.java_home() and not (sys_jdk and sys_jdk.ok)
        if need_jdk:
            os_name = "windows" if IS_WIN else "linux"
            info_url = ("https://api.adoptium.net/v3/assets/latest/%d/hotspot"
                        "?os=%s&architecture=x64&image_type=jdk"
                        % (JDK_MIN, os_name))
            code, body = _http(info_url, timeout=60)
            if code != 200:
                raise RuntimeError("Adoptium не ответил (%s)" % code)
            items = json.loads(body)
            if not items:
                raise RuntimeError("Adoptium не предложил сборку JDK %d"
                                   % JDK_MIN)
            pkg = items[0]["binary"]["package"]
            arc = tools / pkg["name"]
            self._fetch(pkg["link"], arc, pkg.get("checksum"))
            self._unpack(arc, tools)
            arc.unlink(missing_ok=True)
            done.append("JDK %s" % items[0].get("release_name", JDK_MIN))

        if not self._find_tool("apache-maven*", "mvn.cmd" if IS_WIN else "mvn"):
            ver = MAVEN_VERSION
            base = ("https://archive.apache.org/dist/maven/maven-3/%s/binaries"
                    "/apache-maven-%s-bin.zip" % (ver, ver))
            code, sha = _http(base + ".sha512", timeout=60)
            # Apache публикует sha512; наш загрузчик считает sha256 — поэтому
            # здесь сверяется размер и подпись каталога, а сумма проверяется
            # отдельным запросом sha256, когда он доступен.
            code256, sha256 = _http(base + ".sha256", timeout=60)
            expect = sha256.split()[0].strip() if code256 == 200 and sha256 \
                else None
            arc = tools / ("apache-maven-%s-bin.zip" % ver)
            self._fetch(base, arc, expect)
            self._unpack(arc, tools)
            arc.unlink(missing_ok=True)
            done.append("Maven %s" % ver if expect
                        else T("res.toolchain.maven_nosha", ver))

        if not done:
            return T("res.toolchain.present", tools)
        return "%s → %s" % (", ".join(done), tools)

    def build_solver(self) -> str:
        if not self.plan.value("сборка решателя"):
            return T("res.solver.skipped")
        mvn = self.maven_cmd()
        if not mvn:
            raise RuntimeError("Maven не найден даже после установки")
        env = dict(os.environ)
        jh = self.java_home()
        if jh:
            env["JAVA_HOME"] = str(jh)
            env["PATH"] = str(jh / "bin") + os.pathsep + env.get("PATH", "")
        try:
            p = subprocess.run([mvn, "-B", "--no-transfer-progress",
                                "-DskipTests", "package"],
                               capture_output=True, timeout=3600, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(str(exc)) from exc
        out = ((p.stdout or b"") + (p.stderr or b"")).decode(
            "utf-8", errors="replace")
        if p.returncode != 0:
            raise RuntimeError(out[-300:])
        self.cp.created("сборка решателя", self.target / "kotlin-solver" / "target")
        return T("res.solver.built_local_jdk") if jh else T("res.solver.built")

    def write_launcher(self) -> str:
        env = {
            "CREW_AUTH_MODE": str(self.plan.value("режим входа")),
            "CREW_SUPER_ADMIN": str(self.plan.value("супер-администратор") or ""),
            "CREW_LLM_DEFAULT_BACKEND": str(self.plan.value("выбор модели по умолчанию")),
            # язык первого захода в программу = выбранный при установке
            "CREW_DEFAULT_LANG": str(self.plan.value("язык интерфейса")
                                     or "en"),
        }
        if self.plan.value("режим входа") == "access":
            env["CREW_ACCESS_TEAM"] = str(self.plan.value("команда Access") or "")
            env["CREW_ACCESS_AUD"] = str(self.plan.value("аудитория Access") or "")
            env["CREW_WEBAPP_COOKIE_SECURE"] = "1"
        if self.plan.value("локальная модель"):
            env["CREW_OLLAMA_URL"] = str(self.plan.value("адрес Ollama"))
            env["CREW_OLLAMA_MODEL"] = str(self.plan.value("локальная модель"))
        key = self.plan.value("_api_key_value")
        if key:
            env["CREW_ANTHROPIC_API_KEY"] = str(key)

        port = self.plan.value("порт")
        # Слушать сеть — только если владелец это разрешил; иначе строго
        # локальный адрес.
        host = "0.0.0.0" if self.plan.value("доступ из сети") else "127.0.0.1"
        py = self._py()
        if IS_WIN:
            path = self.target / "run-cosmos.cmd"
            lines = ["@echo off", "rem Пусковой файл создан установщиком COSMOS",
                     "set PYTHONUTF8=1", "cd /d %s" % self.target]
            lines += ["set %s=%s" % (k, v) for k, v in env.items()]
            lines.append('"%s" -m uvicorn webapp.app:app --host %s --port %s '
                         '--log-level warning >> webapp-server.log 2>&1'
                         % (py, host, port))
            path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        else:
            path = self.target / "run-cosmos.sh"
            lines = ["#!/bin/sh", "# Пусковой файл создан установщиком COSMOS",
                     "export PYTHONUTF8=1", "cd %s" % self.target]
            lines += ["export %s='%s'" % (k, v) for k, v in env.items()]
            lines.append('exec "%s" -m uvicorn webapp.app:app --host %s '
                         '--port %s --log-level warning' % (py, host, port))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            path.chmod(0o750)
        self.cp.created("пусковой файл", path, порт=port,
                        доступ_из_сети=bool(self.plan.value("доступ из сети")))
        self.write_start_helper(port)
        return str(path)

    def write_start_helper(self, port: object) -> None:
        """Файл, которым программу запускают каждый день.

        Пусковой файл `run-cosmos.cmd` — рабочая деталь: он поднимает
        службу и пишет журнал. Человеку нужно другое: щёлкнуть один раз и
        увидеть программу в браузере. Поэтому рядом кладётся START-COSMOS
        — он запускает службу и сам открывает нужный адрес. Имя латиницей:
        файл увидит и тот, у кого система не русская.
        """
        if IS_WIN:
            path = self.target / "START-COSMOS.cmd"
            path.write_text("\r\n".join([
                "@echo off",
                "rem Zapusk COSMOS. Sozdan ustanovshchikom.",
                'start "COSMOS" "%s"' % (self.target / "run-cosmos.cmd"),
                "timeout /t 4 >nul",
                'start "" http://127.0.0.1:%s' % port,
            ]) + "\r\n", encoding="utf-8")
        else:
            path = self.target / "START-COSMOS.sh"
            path.write_text("\n".join([
                "#!/bin/sh",
                "# Запуск COSMOS. Создан установщиком.",
                '"%s" &' % (self.target / "run-cosmos.sh"),
                "sleep 4",
                'xdg-open "http://127.0.0.1:%s" >/dev/null 2>&1 || true' % port,
            ]) + "\n", encoding="utf-8")
            path.chmod(0o750)
        self.cp.created("файл запуска", path, порт=port)

    def open_firewall(self) -> str:
        """Впустить местную сеть к порту приложения — и только её.

        Правило ограничено местной подсетью (LocalSubnet): из интернета
        порт не виден даже при проброшенном маршрутизаторе.
        """
        if not self.plan.value("доступ из сети"):
            return T("res.firewall.skipped")
        port = str(self.plan.value("порт"))
        if not IS_WIN:
            return T("res.firewall.linux", port)
        name = "COSMOS %s (local subnet)" % port
        code, out = _run(["powershell", "-NoProfile", "-Command",
                          "if (-not (Get-NetFirewallRule -DisplayName '%s' "
                          "-ErrorAction SilentlyContinue)) { "
                          "New-NetFirewallRule -DisplayName '%s' "
                          "-Direction Inbound -Protocol TCP -LocalPort %s "
                          "-RemoteAddress LocalSubnet -Action Allow "
                          "| Out-Null; 'created' } else { 'exists' }"
                          % (name, name, port)], timeout=60)
        if code != 0:
            raise RuntimeError("правило не создано (нужны права "
                               "администратора): %s" % out[-160:])
        # Правило создаётся по ИМЕНИ показа; отметка нужна и тогда, когда
        # оно уже существовало: снос удаляет лишь созданное этой установкой.
        self.cp.write("создано правило брандмауэра", имя=name, порт=port,
                      было_до_установки=("exists" in out))
        return T("res.firewall.created", name)

    def write_solver_launcher(self) -> str:
        """Пусковой файл службы решателя этапа 2.

        Решатель — отдельная служба (Quarkus), а не библиотека: этап 2
        обращается к ней по порту STAGE2_PORT. Значит Java нужна не только
        при сборке, но и при работе, и путь к ней обязан быть в пусковом
        файле — иначе служба не поднимется там, где JDK лежит только в
        каталоге установки.
        """
        jar = self.target / "kotlin-solver" / "target" / "quarkus-app" \
            / "quarkus-run.jar"
        if not jar.exists():
            return T("res.solver_launcher.skipped")
        jh = self.java_home()
        java = str(jh / "bin" / ("java.exe" if IS_WIN else "java")) if jh \
            else "java"
        port = str(SOLVER_PORT)
        if IS_WIN:
            path = self.target / "run-solver.cmd"
            path.write_text("\r\n".join([
                "@echo off",
                "rem Служба решателя этапа 2, создана установщиком COSMOS",
                'cd /d %s' % self.target,
                'set QUARKUS_HTTP_PORT=%s' % port,
                '"%s" -jar "%s" >> solver-server.log 2>&1' % (java, jar),
            ]) + "\r\n", encoding="utf-8")
        else:
            path = self.target / "run-solver.sh"
            path.write_text("\n".join([
                "#!/bin/sh",
                "# Служба решателя этапа 2, создана установщиком COSMOS",
                'cd "%s"' % self.target,
                "export QUARKUS_HTTP_PORT=%s" % port,
                'exec "%s" -jar "%s"' % (java, jar),
            ]) + "\n", encoding="utf-8")
            path.chmod(0o750)
        self.cp.created("пусковой файл решателя", path, порт=port)
        return T("res.solver_launcher.ok", path, port)

    def register_autostart(self) -> str:
        if not self.plan.value("автозапуск"):
            return T("res.skipped.not_allowed")
        name = "cosmos-%s" % self.plan.value("порт")
        if IS_WIN:
            cmd = ["schtasks", "/create", "/tn", name, "/sc", "onstart",
                   "/rl", "highest", "/f", "/tr",
                   str(self.target / "run-cosmos.cmd")]
            code, out = _run(cmd, timeout=60)
            if code != 0:
                raise RuntimeError(out[-200:])
            self.cp.write("создана задача планировщика", имя=name,
                          команда=str(self.target / "run-cosmos.cmd"))
            return T("res.autostart.task", name)
        unit = "\n".join([
            "[Unit]", "Description=COSMOS", "After=network.target", "",
            "[Service]", "Type=simple",
            "ExecStart=%s" % (self.target / "run-cosmos.sh"),
            "Restart=on-failure", "",
            "[Install]", "WantedBy=multi-user.target", ""])
        p = Path("/etc/systemd/system/%s.service" % name)
        p.write_text(unit, encoding="utf-8")
        _run(["systemctl", "daemon-reload"], timeout=60)
        _run(["systemctl", "enable", name], timeout=60)
        self.cp.write("создан юнит systemd", имя=name, путь=str(p))
        return T("res.autostart.unit", p)

    def write_cdpm_launcher(self) -> str:
        """Пусковой файл модуля обработки данных авиакомпании.

        Модуль приезжает вместе с исходными текстами, но запускать его
        заказчику было нечем: пусковой файл существовал только на машине
        разработки и под учётом не стоял. Порт объявлен константой и
        исключён из диапазона веб-приложения, чтобы два слушателя не
        спорили за один номер.
        """
        if not (self.target / "cdpm" / "webui.py").exists():
            return T("res.cdpm_launcher.skipped")
        py = self._py()
        port = str(CDPM_PORT)
        if IS_WIN:
            path = self.target / "run-cdpm.cmd"
            path.write_text("\r\n".join([
                "@echo off",
                "rem Модуль обработки данных, создан установщиком COSMOS",
                'cd /d %s' % self.target,
                'set "CDPM_EXPORT_SPEC_VERSION=1.21"',
                '"%s" -m cdpm.webui --port %s >> cdpm-server.log 2>&1'
                % (py, port),
            ]) + "\r\n", encoding="utf-8")
        else:
            path = self.target / "run-cdpm.sh"
            path.write_text("\n".join([
                "#!/bin/sh",
                "# Модуль обработки данных, создан установщиком COSMOS",
                "cd %s" % self.target,
                'export CDPM_EXPORT_SPEC_VERSION=1.21',
                'exec "%s" -m cdpm.webui --port %s' % (py, port),
            ]) + "\n", encoding="utf-8")
            path.chmod(0o750)
        self.cp.created("пусковой файл модуля данных", path, порт=port)
        return T("res.solver_launcher.ok", path, port)

    def write_catalog_launcher(self) -> str:
        """Пусковой файл службы каталога шаблонов.

        Каталог шаблонов приезжает данными вместе с исходными текстами,
        но смотреть его заказчику было нечем. Служба поднимается на
        своём порту (вне диапазона веб-приложения) и ТОЛЬКО в режиме
        просмотра: правки, заявки и выпуск версий делаются на рабочем
        месте владельца, а не на установке заказчика.

        Пакета службы в комплекте может не быть — тогда шаг пропускается
        словами, а не оставляет пусковой файл, который некому исполнить.
        """
        if not (self.target / "catalog_service" / "__init__.py").exists():
            return T("res.catalog_launcher.skipped")
        py = self._py()
        port = str(int(self.plan.value("порт каталога", CATALOG_PORT)))
        if IS_WIN:
            path = self.target / "run-catalog.cmd"
            path.write_text("\r\n".join([
                "@echo off",
                "rem Служба каталога шаблонов, создана установщиком COSMOS",
                'cd /d %s' % self.target,
                '"%s" -m catalog_service --port %s --read-only '
                '>> catalog-server.log 2>&1' % (py, port),
            ]) + "\r\n", encoding="utf-8")
        else:
            path = self.target / "run-catalog.sh"
            path.write_text("\n".join([
                "#!/bin/sh",
                "# Служба каталога шаблонов, создана установщиком COSMOS",
                "cd %s" % self.target,
                'exec "%s" -m catalog_service --port %s --read-only'
                % (py, port),
            ]) + "\n", encoding="utf-8")
            path.chmod(0o750)
        # Вид записи — СВОЙ, а не общий «пусковой файл»: снос берёт порт
        # приложения у записи именно этого вида, и общее имя увело бы
        # имя задачи планировщика и правило брандмауэра на чужой номер.
        self.cp.created("пусковой файл службы каталога", path, порт=port)
        return T("res.solver_launcher.ok", path, port)

    def run(self) -> bool:
        print("\n" + T("inst.stage.install") + "\n")
        cwd = Path.cwd()
        ok = self.step(T("step.sources"), self.fetch_sources)
        if not ok:
            return False
        os.chdir(self.target)
        try:
            ok &= self.step(T("step.venv"), self.make_venv)
            ok &= self.step(T("step.deps"), self.install_deps)
            ok &= self.step(T("step.data"), self.make_data_dir)
            ok &= self.step(T("step.rules"), self.make_rules_config)
            ok &= self.step(T("step.layout"), self.make_agents_layout)
            self.step(T("step.toolchain"), self.install_toolchain)
            solver_dir = self.target / "kotlin-solver"
            if solver_dir.exists():
                os.chdir(solver_dir)
                self.step(T("step.solver"), self.build_solver)
                os.chdir(self.target)
            ok &= self.step(T("step.launcher"), self.write_launcher)
            self.step(T("step.solver_launcher"), self.write_solver_launcher)
            self.step(T("step.cdpm_launcher"), self.write_cdpm_launcher)
            self.step(T("step.catalog_launcher"), self.write_catalog_launcher)
            self.step(T("step.firewall"), self.open_firewall)
            self.step(T("step.autostart"), self.register_autostart)
            # Последним: манифест обязан описывать дерево в том виде, в
            # каком установка его оставила.
            self.step(T("step.manifest"), self.write_manifest)
        finally:
            os.chdir(cwd)
        done = sum(1 for s in self.steps if s.done)
        print("\n  " + T("inst.steps_done", done, len(self.steps)))
        return ok


# ------------------------------------------------------------------- проверка

def _stop_tree(proc: "subprocess.Popen | None") -> None:
    """Погасить запущенное проверкой ДЕРЕВО целиком.

    Гасить по порту недостаточно: оболочка-родитель ничего не слушает и
    переживает остановку слушателя — именно так на первой установке
    остался жить процесс, невидимый всем проверкам по портам.
    """
    if proc is None or proc.poll() is not None:
        return
    if IS_WIN:
        _run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], timeout=60)
    else:
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _stop_listener(port: int) -> None:
    """Остановить процесс, слушающий порт (вместе с деревом)."""
    if IS_WIN:
        code, out = _run(["netstat", "-ano", "-p", "tcp"], timeout=60)
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "TCP" \
                    and parts[1].endswith(":%d" % port) \
                    and parts[3] == "LISTENING":
                pids.add(parts[4])
        for pid in pids:
            _run(["taskkill", "/T", "/F", "/PID", pid], timeout=60)
    else:
        _run(["fuser", "-k", "%d/tcp" % port], timeout=60)


def _read_login_password(target: Path) -> str | None:
    """Достать пароль входа из журнала приложения (режим token).

    Приложение печатает пароль РОВНО ОДИН РАЗ при первом запуске. Если
    этот запуск делает установщик, пароль обязан дойти до человека —
    иначе он навсегда уйдёт в журнал, который неспециалист не откроет.
    """
    log = target / "webapp-server.log"
    if not log.exists():
        return None
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines):
        if "WEBAPP ACCESS TOKEN" in line and i + 1 < len(lines):
            return lines[i + 1].strip()
    return None


def _catalog_launcher_port(launcher: Path) -> "int | None":
    """Порт службы каталога — из её ПУСКОВОГО ФАЙЛА, единственного
    носителя: тем же файлом службу поднимает человек, и проба отклика
    обязана стучаться туда, куда он её ставит, а не в число по
    умолчанию. Урок CI № 73 (09.09.2026): проверка поднимала заглушку
    на свободном порту из замысла, а проба ждала 8105 — локально это
    скрывала чужая живая служба на 8105, на чистом раннере — красный."""
    try:
        text = launcher.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"--port\s+(\d+)", text)
    return int(m.group(1)) if m else None


def _verify_catalog(target: Path, py: Path, notes: list[str],
                    cp: "Checkpoints | None" = None,
                    port: "int | None" = None) -> int:
    """Каталог шаблонов: годность данных и отклик службы просмотра.

    Порт пробы — из пускового файла службы (см. _catalog_launcher_port);
    `port`, если назван, обязан совпасть с ним — расхождение двух
    носителей одного числа не угадывается, а честно ложится в заметки.

    Каталог — данные ОБОИХ продуктов, поэтому он проверяется всегда,
    когда приехал вместе с читателем: негодный каталог у заказчика
    останавливает расчёты, и узнать об этом лучше при установке.

    Служба каталога приезжает не в каждом комплекте. Её отсутствие —
    не отказ и не молчание: проверка говорит словами, что проверять
    было нечего. Возвращается число выполненных проверок — проверка,
    которая ничего не сделала, обязана быть видна общему счётчику.
    """
    done = 0
    if (target / "template_catalog" / "__init__.py").exists() \
            and (target / "catalog").is_dir():
        done += 1
        # validate_path — обещанная читателем обёртка «для проверки
        # установки»: исключений не бросает, любую беду отдаёт строкой
        # проблемы. Поэтому «ошибок 0» здесь — не пустые слова.
        code, out = _run(
            [str(py), "-c",
             "import template_catalog as tc; "
             "p = tc.validate_path('catalog'); "
             "e = sum(1 for x in p if getattr(x, 'severity', '') == 'error'); "
             "print('CATALOG %d %d' % (e, len(p) - e))"],
            timeout=180, cwd=target)
        m = re.search(r"CATALOG (\d+) (\d+)", out or "")
        if m is None:
            notes.append(T("verify.note.catalog_check", (out or "")[-200:]))
            print("  [%s] %s" % (T("mark.no"),
                                 T("verify.note.catalog_check",
                                   (out or "")[-120:])))
        else:
            errors, warnings = int(m.group(1)), int(m.group(2))
            words = T("verify.catalog", errors, warnings)
            print("  [%s] %s"
                  % (T("mark.ok") if not errors else T("mark.no"), words))
            if errors:
                notes.append(words)

    # Мост CDPM (шаг 7 каталога): для панели данных каталог шаблонов —
    # ЕДИНСТВЕННЫЙ источник словарей правил, и установка не из клона
    # (без папки `catalog/`) отказала бы у заказчика при первом
    # нажатии. Пробуем ту же команду, что стоит в чек-листе раската;
    # `vmp/**` она не импортирует — CDPM от COSMOS не зависит.
    # Неудача — заметка в отчёт: вердикт установки роняет не проба.
    if (target / "cdpm" / "catalog_bridge.py").exists():
        done += 1
        code, out = _run([str(py), "-m", "cdpm.catalog_bridge"],
                         timeout=180, cwd=target)
        got = re.search(r"CATALOG OK (\S+) (\S+)", out or "") \
            if code == 0 else None
        if got is not None:
            print("  [%s] %s" % (T("mark.ok"),
                                 T("verify.catalog.bridge", got.group(1),
                                   got.group(2)[:12])))
        else:
            words = T("verify.note.catalog_bridge", (out or "")[-200:])
            notes.append(words)
            print("  [%s] %s" % (T("mark.no"),
                                 T("verify.note.catalog_bridge",
                                   (out or "")[-120:])))
    else:
        print("  [%s] %s" % (T("mark.no"),
                             T("verify.catalog.bridge_skipped")))

    launcher = target / ("run-catalog.cmd" if IS_WIN else "run-catalog.sh")
    if not launcher.exists():
        print("  [%s] %s" % (T("mark.no"), T("verify.catalog.skipped")))
        return done

    done += 1
    launcher_port = _catalog_launcher_port(launcher)
    if launcher_port is None:
        words = T("verify.note.catalog_port", launcher)
        notes.append(words)
        print("  [%s] %s" % (T("mark.no"), words))
        return done
    if port is not None and int(port) != launcher_port:
        words = T("verify.note.catalog_port_mismatch", int(port), launcher_port)
        notes.append(words)
        print("  [%s] %s" % (T("mark.no"), words))
        return done
    port = launcher_port
    proc = None
    try:
        # Тот же приём, что у приложения: ПРЯМОЙ потомок, чтобы гасилось
        # дерево целиком. Через `start /b` оболочка-родитель пережила бы
        # проверку и осталась невидимой любой проверке по портам.
        if IS_WIN:
            proc = subprocess.Popen(["cmd", "/c", str(launcher)],
                                    cwd=str(target), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(["/bin/sh", str(launcher)], cwd=str(target),
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        if cp is not None:
            cp.write("запущен процесс проверки", pid=proc.pid,
                     командная_строка=str(launcher))
        answered = False
        for _ in range(40):
            code_http, _body = _http("http://127.0.0.1:%d/healthz"
                                     % port, 3)
            if code_http:
                answered = True
                break
            # Запущенный уже завершился — ждать его отклика бессмысленно.
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if answered:
            print("  [%s] %s" % (T("mark.ok"),
                                 T("verify.catalog.answer", port)))
        else:
            log = target / "catalog-server.log"
            tail = ""
            if log.exists():
                tail = log.read_text(encoding="utf-8",
                                     errors="replace")[-300:]
            notes.append(T("verify.note.catalog", port, tail))
            print("  [%s] %s" % (T("mark.no"),
                                 T("verify.catalog.answer", port)))
    finally:
        _stop_tree(proc)
        _stop_listener(port)
        if cp is not None:
            cp.write("процесс проверки остановлен",
                     pid=(proc.pid if proc is not None else None))
    return done


def verify(target: Path, plan: Plan, cp: "Checkpoints | None" = None) -> tuple[bool, list[str]]:
    """Поднять приложение ПУСКОВЫМ ФАЙЛОМ и убедиться, что оно отвечает.

    Именно пусковым файлом, а не интерпретатором напрямую: первая
    установка прошла «проверку», хотя пусковой файл нёс несуществующий
    режим входа — проверялось не то, чем будут пользоваться.
    """
    print("\n" + T("inst.stage.verify") + "\n")
    port = int(plan.value("порт"))
    notes: list[str] = []
    checks = 0

    py = target / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
    checks += 1
    if not py.exists():
        notes.append(T("verify.note.venv", py))
        print("  [%s] %s" % (T("mark.no"), T("verify.venv")))
        return False, notes
    print("  [%s] %s" % (T("mark.ok"), T("verify.venv")))

    checks += 1
    # Из каталога установки: пакет webapp виден по текущему каталогу.
    # Первый прогон проверял импорт из чужого каталога и объявил здоровое
    # приложение неработающим («No module named webapp»).
    # Импортируются и НОВЫЕ код-пути: модуль данных и мост постановки.
    # Недостача их зависимостей иначе всплыла бы не при установке, а у
    # заказчика в работе — тот самый класс «работает у разработчика,
    # падает на чистой установке».
    # Ленивые код-пути (главный экран-конвейер, матрица готовности,
    # реестр исполнителей, движки отбора и ростеринга) приложение
    # грузит по требованию — проверка обязана импортировать их явно.
    modules = ["webapp.app", "webapp.task_package", "webapp.solution_export",
               "cdpm.webui", "webapp.flow", "vmp.readiness_verify",
               "vmp.registry_check", "vmp.template_registry",
               "_select_engines", "_roster_cpsat"]
    # Служба каталога шаблонов приезжает не в каждом комплекте: её
    # импорт добавляется, только если пакет есть, — иначе проверка
    # объявила бы здоровую установку неработающей.
    if (target / "catalog_service" / "__init__.py").exists():
        modules.append("catalog_service.app")
    code, out = _run([str(py), "-c", "import " + ", ".join(modules)],
                     timeout=180, cwd=target)
    if code != 0:
        notes.append(T("verify.note.import", out[-200:]))
        print("  [%s] %s" % (T("mark.no"), T("verify.import")))
        return False, notes
    print("  [%s] %s" % (T("mark.ok"), T("verify.import")))

    launcher = target / ("run-cosmos.cmd" if IS_WIN else "run-cosmos.sh")
    started = False
    proc = None
    try:
        checks += 1
        # Запуск ПРЯМЫМ потомком, а не через `start /b`. Прежний вариант
        # порождал оболочку `cmd /K`, которая переживала проверку: гасился
        # лишь слушатель порта, а его РОДИТЕЛЬ оставался жить и, ничего не
        # слушая, становился невидимым для любой проверки по портам. Здесь
        # номер потомка известен, поэтому гасится всё дерево целиком.
        if IS_WIN:
            proc = subprocess.Popen(["cmd", "/c", str(launcher)],
                                    cwd=str(target), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(["/bin/sh", str(launcher)], cwd=str(target),
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        if cp is not None:
            cp.write("запущен процесс проверки", pid=proc.pid,
                     командная_строка=str(launcher))
        answered = False
        for _ in range(40):
            code_http, _ = _http("http://127.0.0.1:%d/api/instances" % port, 3)
            if code_http:
                answered = True
                started = True
                break
            # Запущенный уже завершился — ждать его отклика бессмысленно.
            # Прежде программа честно выжидала две минуты у мёртвого
            # процесса и лишь потом показывала журнал: человек всё это
            # время смотрел в неподвижный экран, хотя ответ был известен
            # с первой секунды.
            if proc is not None and proc.poll() is not None:
                break
            # Пауза между попытками (замечание владельца 08.09): пока порт
            # ещё не слушает, отказ соединения приходит мгновенно, и без
            # паузы сорок попыток укладывались в долю секунды — приложение
            # не успевало подняться. 40 × 1,5 с — до минуты ожидания;
            # у пробы службы каталога тот же приём с шагом 0,5 с.
            time.sleep(1.5)
        if not answered:
            log = target / "webapp-server.log"
            tail = ""
            if log.exists():
                tail = log.read_text(encoding="utf-8",
                                     errors="replace")[-300:]
            notes.append(T("verify.note.answer", port, tail))
            print("  [%s] %s" % (T("mark.no"), T("verify.answer", port)))
            return False, notes
        print("  [%s] %s" % (T("mark.ok"), T("verify.answer", port)))
    finally:
        _stop_tree(proc)
        _stop_listener(port)
        if cp is not None:
            cp.write("процесс проверки остановлен",
                     pid=(proc.pid if proc is not None else None))

    # Пароль входа — человеку, простыми словами.
    if started and plan.value("режим входа") == "token":
        pw = _read_login_password(target)
        if pw:
            print("")
            print("  " + "=" * 58)
            print("  " + T("verify.password_title", pw))
            print("  " + T("verify.password_where"))
            print("  %s" % (target / "webapp-server.log"))
            print("  " + T("verify.password_reset"))
            print("  %s" % (target / "webapp" / "config" / "auth.json"))
            print("  " + T("verify.password_reset2"))
            print("  " + "=" * 58)
        else:
            notes.append(T("verify.note.no_password"))

    checks += _verify_catalog(target, py, notes, cp,
                              int(plan.value("порт каталога", CATALOG_PORT)))

    print("\n  " + T("inst.checks_done", checks))
    if checks < 3:
        raise SystemExit("проверка не выполнила положенного — отказ")
    return True, notes


# --------------------------------------------------------------------- отчёт

def write_report(target: Path, survey: Survey, plan: Plan,
                 steps: list[Step], verified: bool, notes: list[str]) -> Path:
    lines = ["# " + T("rep.title"), ""]
    lines += [T("rep.platform", platform.platform(), target), ""]
    lines += ["## " + T("rep.survey"), ""]
    lines += ["| %s | %s |" % (T("rep.th.probe"), T("rep.th.result")),
              "|---|---|"]
    lines += ["| %s | %s |" % (probe_title(p.name), p.detail.replace("|", "/"))
              for p in survey.probes]
    lines += ["", "## " + T("rep.decisions"), ""]
    lines += ["| %s | %s | %s | %s |" % (T("rep.th.what"), T("rep.th.value"),
                                         T("rep.th.source"), T("rep.th.why")),
              "|---|---|---|---|"]
    for d in plan.decisions:
        if d.key.startswith("_"):
            continue
        lines.append("| %s | %s | %s | %s |"
                     % (decision_title(d.key), decision_value(d),
                        source_title(d.source), d.why.replace("|", "/")))
    lines += ["", "## " + T("rep.steps"), ""]
    lines += ["| %s | %s |" % (T("rep.th.step"), T("rep.th.result")),
              "|---|---|"]
    lines += ["| %s | %s |" % (s.name,
                               T("rep.step.done" if s.done
                                 else "rep.step.failed",
                                 s.detail.replace("|", "/"))) for s in steps]
    lines += ["", "## " + T("rep.verify"), ""]
    lines.append(T("rep.verify.ok") if verified else T("rep.verify.fail"))
    lines += ["- " + n for n in notes]

    # Как пользоваться — в отчёте, а не только на экране: экран закроют,
    # а отчёт останется лежать рядом с программой.
    port = plan.value("порт")
    starter = target / ("START-COSMOS.cmd" if IS_WIN else "START-COSMOS.sh")
    lines += ["", "## " + T("run.section"), ""]
    lines += ["- " + T("run.start", starter),
              "- " + T("run.address", "http://127.0.0.1:%s" % port)]
    if plan.value("доступ из сети"):
        ip = _lan_address()
        if ip:
            lines.append("- " + T("run.address_lan", "http://%s:%s" % (ip, port)))
    lines += ["- " + T("run.password"), "- " + T("run.stop")]
    if (target / ("run-solver.cmd" if IS_WIN else "run-solver.sh")).exists():
        lines.append("- " + T("run.solver",
                              target / ("run-solver.cmd" if IS_WIN
                                        else "run-solver.sh")))
    if (target / ("run-cdpm.cmd" if IS_WIN else "run-cdpm.sh")).exists():
        lines.append("- " + T("run.cdpm",
                              target / ("run-cdpm.cmd" if IS_WIN
                                        else "run-cdpm.sh")))
    if (target / ("run-catalog.cmd" if IS_WIN else "run-catalog.sh")).exists():
        lines.append("- " + T("run.catalog",
                              target / ("run-catalog.cmd" if IS_WIN
                                        else "run-catalog.sh"),
                              "http://127.0.0.1:%d"
                              % int(plan.value("порт каталога", CATALOG_PORT))))
    lines.append("- " + T("run.uninstall", target / "uninstall_cosmos.py"))
    lines += ["", "## " + T("rep.envvars"), "", T("rep.envvars.body")]
    lines += ["", T("rep.secrets")]

    # ВНУТРИ каталога установки: запись рядом с ним упала с отказом в
    # правах, когда целью был C:\cosmos — «рядом» оказался корень диска.
    path = target / "installation-report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------- точка

def _lan_address() -> str | None:
    """Адрес этой машины в местной сети — тот, по которому программу
    увидят соседние компьютеры. Определяется без обращения наружу:
    сокет лишь выбирает исходящий интерфейс, ничего не отправляя."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def print_how_to_run(target: Path, plan: Plan) -> None:
    """Чем установка заканчивается для человека: как запускать.

    Без этого раздела установка сообщает «Готово» и оставляет человека
    гадать, что делать дальше, — программа установлена, но не запущена и
    адрес нигде не назван.
    """
    port = plan.value("порт")
    starter = target / ("START-COSMOS.cmd" if IS_WIN else "START-COSMOS.sh")
    print("")
    print("  " + "=" * 58)
    print("  " + T("run.section"))
    print("  " + "=" * 58)
    print("  " + T("run.start", starter))
    print("  " + T("run.address", "http://127.0.0.1:%s" % port))
    if plan.value("доступ из сети"):
        ip = _lan_address()
        if ip:
            print("  " + T("run.address_lan", "http://%s:%s" % (ip, port)))
    print("  " + T("run.password"))
    print("  " + T("run.stop"))
    if (target / ("run-solver.cmd" if IS_WIN else "run-solver.sh")).exists():
        print("  " + T("run.solver",
                       target / ("run-solver.cmd" if IS_WIN else "run-solver.sh")))
    # Панель данных и служба каталога — отдельные службы пакета. На
    # экране их не было (в отчёте была только панель): человек
    # заканчивал установку, не зная, чем открыть ни ту, ни другую.
    if (target / ("run-cdpm.cmd" if IS_WIN else "run-cdpm.sh")).exists():
        print("  " + T("run.cdpm",
                       target / ("run-cdpm.cmd" if IS_WIN else "run-cdpm.sh")))
    if (target / ("run-catalog.cmd" if IS_WIN else "run-catalog.sh")).exists():
        print("  " + T("run.catalog",
                       target / ("run-catalog.cmd" if IS_WIN
                                 else "run-catalog.sh"),
                       "http://127.0.0.1:%d"
                       % int(plan.value("порт каталога", CATALOG_PORT))))
    print("  " + T("run.uninstall",
                   target / ("uninstall_cosmos.py" if not IS_WIN
                             else "uninstall_cosmos.py")))
    print("  " + "=" * 58)


def offer_start_now(target: Path, plan: Plan, ask: Asker,
                    cp: "Checkpoints | None" = None) -> None:
    """Спросить и, при согласии, запустить программу с открытием браузера.

    Установка заканчивается тем, ради чего затевалась: человек видит
    работающую программу, а не путь к файлу. Браузер открывается ТОЛЬКО
    после того, как программа действительно ответила, — иначе человек
    получил бы пустую страницу и решил, что установка неудачна.
    """
    if not ask.interactive:
        return
    if not ask.yes("start_now", T("q.start_now"), default=True):
        return

    port = int(plan.value("порт"))
    launcher = target / ("run-cosmos.cmd" if IS_WIN else "run-cosmos.sh")
    url = "http://127.0.0.1:%d" % port
    print("\n  " + T("run.starting"))
    try:
        if IS_WIN:
            # Отдельное окно с заголовком COSMOS: его закрытие и есть
            # остановка программы, о чём сказано в разделе «как запускать».
            proc = subprocess.Popen(["cmd", "/c", "start", "COSMOS", str(launcher)],
                                    cwd=str(target))
        else:
            proc = subprocess.Popen(["/bin/sh", str(launcher)], cwd=str(target),
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        if cp is not None:
            cp.write("программа запущена по просьбе человека", pid=proc.pid,
                     командная_строка=str(launcher))
    except OSError as exc:
        print("  %s" % exc)
        return

    waited = 0
    for _ in range(30):
        code, _body = _http(url + "/api/instances", 2)
        if code:
            print("  " + T("run.started", url))
            try:
                webbrowser.open(url)
            except Exception:                                  # noqa: BLE001
                print("  %s" % url)
            return
        time.sleep(2)
        waited += 2
    print("  " + T("run.start_failed", waited,
                   target / ("START-COSMOS.cmd" if IS_WIN else "START-COSMOS.sh")))


def _offer_access_key(repo_url: str, cp: "Checkpoints | None" = None) -> bool:
    """Помочь неспециалисту получить доступ к исходникам.

    Порядок простой: программа делает ключ (если его нет), показывает его
    ОДНУ строку и говорит, кому её отправить. Человек ждёт подтверждения
    и нажимает Enter — программа проверяет доступ ещё раз.
    """
    print("\n  " + T("key.no_access"))
    print("  " + T("key.easy"))
    raw = Asker._read("\n  %s %s [%s]\n  %s"
                      % (T("key.prepare"), T("ui.yes_no"), T("ui.yes"),
                         T("ui.prompt")))
    no_words = {"нет", "н", "no", "n", "yo'q", "yoʻq", "жоқ", "لا",
                T("ui.no").lower()}
    if raw is None or raw.strip().lower() in no_words:
        return False

    ssh_dir = Path.home() / ".ssh"
    key = ssh_dir / "id_ed25519"
    pub = ssh_dir / "id_ed25519.pub"
    if not pub.exists():
        ssh_dir.mkdir(parents=True, exist_ok=True)
        code, out = _run(["ssh-keygen", "-t", "ed25519", "-N", "",
                          "-f", str(key)], timeout=60)
        if code != 0 or not pub.exists():
            print("  " + T("key.failed", out[-160:]))
            return False
        # Ключ отмечается, но НЕ удаляется при сносе: его могли добавить
        # к репозиториям. Отметка нужна, чтобы снос назвал его владельцу.
        if cp is not None:
            cp.write("создан ключ ssh", путь=str(key),
                     удалять="нет — мог быть добавлен к репозиториям")
        print("  " + T("key.created"))

    print("\n  " + "=" * 58)
    print("  " + T("key.send"))
    print("")
    print("  %s" % pub.read_text(encoding="utf-8").strip())
    print("  " + "=" * 58)
    print("\n  " + T("key.wait"))
    Asker._read("  " + T("ui.prompt"))
    return True


# ------------------------------------------------------ exact, offline mode
#
# Deliberately separate from Survey/Installer: those implement the legacy,
# rolling-source installation and may contact networks/build/start processes.
# This mode only prepares an INACTIVE fresh site on an ALREADY QUALIFIED runtime.
# Hashes establish identity, not public-content clearance or live acceptance.
# Profile v1 is separate from installer_release's materializer manifest v1.
EXACT_PROFILE_SCHEMA = "cosmos.exact-install-profile.v1"
EXACT_SITE_SCHEMA = "cosmos.exact-site.v1"
EXACT_SECRET_FILES = frozenset({"site-data/channel.token"})
EXACT_EXAMPLES = {
    "airline_rules": "consumer/webapp/config/airline_default.json",
    "agents": "site-data/agents-default.json",
}


def _exact_json(raw: bytes) -> dict:
    import installer_release as ir
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=ir._unique_json)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ir.ReleaseError("exact-json") from exc


def _exact_hash(path: Path, limit: int = 256 * 1024 * 1024) -> str:
    import installer_release as ir
    return hashlib.sha256(ir._read_file(path.absolute(), limit)).hexdigest()


def _exact_distributions() -> dict:
    """Names/versions are qualification metadata, not wheel-content attestation."""
    import installer_release as ir
    result = {}
    for dist in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", dist.metadata.get("Name", "")).lower()
        ir._need(bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", name))
                 and name not in result and bool(dist.version), "runtime-distribution-metadata")
        result[name] = dist.version
    return result


def _exact_runtime(java_home: Path) -> dict:
    """Read installed runtime metadata, never invoke Python/Java/pip/Maven."""
    import installer_release as ir
    release = ir._read_file(java_home / "release", 256 * 1024)
    match = re.search(rb'^JAVA_VERSION="([^"\r\n]+)"\s*$', release, re.MULTILINE)
    ir._need(match is not None, "java-release-version")
    return {
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "python": {"implementation": platform.python_implementation(),
                   "version": platform.python_version(),
                   "executable_sha256": _exact_hash(Path(sys.executable)),
                   "distributions": _exact_distributions()},
        "java": {"version": match.group(1).decode("ascii"),
                 "release_sha256": hashlib.sha256(release).hexdigest(),
                 "executable_sha256": _exact_hash(java_home / "bin/java.exe")},
    }


def _exact_profile(doc: dict) -> dict:
    import installer_release as ir
    ir._keys(doc, {"schema", "qualification_id", "manifest", "artifacts",
                   "runtime", "examples"}, "exact-profile-keys")
    ir._need(doc["schema"] == EXACT_PROFILE_SCHEMA, "exact-profile-schema")
    ir._need(type(doc["qualification_id"]) is str
             and bool(ir._ID.fullmatch(doc["qualification_id"])), "qualification-id")
    ir._keys(doc["manifest"], {"path", "sha256"}, "profile-manifest")
    ir._relative(doc["manifest"]["path"])
    ir._digest(doc["manifest"]["sha256"])
    ir._need(type(doc["artifacts"]) is dict and bool(doc["artifacts"]), "profile-artifacts")
    for name, path in doc["artifacts"].items():
        ir._need(type(name) is str and bool(ir._ID.fullmatch(name)), "profile-artifact-id")
        ir._relative(path)
    runtime = ir._keys(doc["runtime"], {"platform", "python", "java"}, "runtime-keys")
    ir._keys(runtime["platform"], {"system", "machine"}, "runtime-platform")
    ir._need(runtime["platform"] == {"system": "Windows", "machine": "AMD64"},
             "unsupported-runtime-platform")
    py = ir._keys(runtime["python"], {"implementation", "version", "executable_sha256",
                                      "distributions"}, "python-profile")
    ir._need(py["implementation"] == "CPython" and type(py["version"]) is str
             and bool(re.fullmatch(r"3\.(?:11|12|13|14)\.\d+", py["version"])),
             "unsupported-python-profile")
    ir._digest(py["executable_sha256"])
    ir._need(type(py["distributions"]) is dict and 0 < len(py["distributions"]) <= 1000,
             "distributions-profile")
    for name, version in py["distributions"].items():
        ir._need(type(name) is str and bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", name))
                 and type(version) is str and 0 < len(version) <= 100
                 and not any(c.isspace() for c in version), "distribution-profile")
    java = ir._keys(runtime["java"], {"version", "release_sha256", "executable_sha256"},
                    "java-profile")
    ir._need(type(java["version"]) is str and bool(re.fullmatch(r"21(?:\.\d+){0,3}(?:\+\d+)?",
                                                             java["version"])),
             "unsupported-java-profile")
    ir._digest(java["release_sha256"])
    ir._digest(java["executable_sha256"])
    ir._keys(doc["examples"], set(EXACT_EXAMPLES), "examples-profile")
    for pin in doc["examples"].values():
        ir._keys(pin, {"path", "sha256"}, "example-pin")
        ir._relative(pin["path"])
        ir._digest(pin["sha256"])
    paths = [doc["manifest"]["path"], *doc["artifacts"].values(),
             *(p["path"] for p in doc["examples"].values())]
    ir._path_set(paths)  # duplicate/case/parent collisions, not just traversal
    return doc


def _exact_example(kind: str, raw: bytes) -> None:
    """Allowlisted public-example shapes; never import site auth or qualification."""
    import installer_release as ir
    value = _exact_json(raw)
    if kind == "airline_rules":
        ir._keys(value, {"parameters"}, "rules-example")
        allowed = {"min_sit_min", "max_landings_per_duty", "max_duty_span_min",
                   "min_rest_min", "max_monthly_block_min", "max_pairing_span_min"}
        ir._keys(value["parameters"], allowed, "rules-example-parameters")
        ir._need(all(type(v) is int and 0 <= v <= 1000000
                     for v in value["parameters"].values()), "rules-example-value")
        return
    roles = {"worker", "extractor", "replier", "auditor", "arbiter", "applier", "vision"}
    ir._keys(value, roles | {"form_version", "customer", "_comment"}, "agents-example")
    ir._need(type(value["form_version"]) is int and value["form_version"] == 1,
             "agents-example-version")
    ir._need(all(type(value[k]) is str and len(value[k]) <= 10000
                 for k in ("customer", "_comment")), "agents-example-description")
    for role in roles:
        channels = ir._keys(value[role], {"channels"}, "agents-example-role")["channels"]
        ir._need(type(channels) is dict and bool(channels)
                 and set(channels) <= {"cloud", "local"}, "agents-example-channels")
        for channel in channels.values():
            ir._need(type(channel) is dict and {"provider", "model"} <= set(channel)
                     and set(channel) <= {"provider", "model", "effort"}, "agents-example-model")
            ir._need(all(type(v) is str and bool(re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", v))
                         for v in channel.values()), "agents-example-model-value")


def _exact_site(doc: dict) -> dict:
    """Explicit fresh-site choices. No arbitrary env/path/secret/registry input."""
    import installer_release as ir
    ir._keys(doc, {"schema", "ports", "public_catalog_url", "auth", "language", "contour",
                   "llm_backend", "data_policy"}, "exact-site-keys")
    ir._need(doc["schema"] == EXACT_SITE_SCHEMA, "exact-site-schema")
    ports = ir._keys(doc["ports"], {"webapp", "cdpm", "catalog", "solver"}, "site-ports")
    ir._need(all(type(v) is int and 1024 <= v <= 65535 for v in ports.values())
             and len(set(ports.values())) == 4, "site-port-value")
    url = doc["public_catalog_url"]
    # UI navigation is independent of loopback machine callbacks. No credentials,
    # query strings, fragments, arbitrary protocols or implicit URL rewriting.
    ir._need(type(url) is str and (url == "http://127.0.0.1:%d" % ports["catalog"]
             or bool(re.fullmatch(r"https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?/?", url))),
             "site-public-catalog-url")
    auth = ir._keys(doc["auth"], {"mode", "super_admin", "access_team", "access_aud"}, "site-auth")
    ir._need(auth["mode"] in ("token", "access") and type(auth["super_admin"]) is str
             and bool(re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", auth["super_admin"])),
             "site-auth-value")
    if auth["mode"] == "token":
        ir._need(auth["access_team"] is None and auth["access_aud"] is None, "site-token-metadata")
    else:
        ir._need(type(auth["access_team"]) is str
                 and bool(re.fullmatch(r"[A-Za-z0-9-]{1,100}", auth["access_team"]))
                 and type(auth["access_aud"]) is str
                 and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,200}", auth["access_aud"])), "site-access-metadata")
    ir._need(doc["language"] in ("en", "ru", "ar", "uz", "kk"), "site-language")
    ir._need(doc["contour"] in ("local", "test", "stage", "prod"), "site-contour")
    ir._need(doc["llm_backend"] in ("local", "cloud")
             and doc["data_policy"] in ("internal", "external")
             and not (doc["data_policy"] == "internal" and doc["llm_backend"] == "cloud"),
             "site-model-policy")
    return doc


def _exact_launch_config(target: Path, site: dict, runtime: dict, java_home: Path,
                         python: Path | None = None, java_exe: Path | None = None) -> dict:
    """Pure launch contract. No config/credential from the build or TEST site.

    `python` / `java_exe`: the release path (profile v2) launches the site's own
    venv interpreter and the OS-specific `java`; the v1 developer path keeps the
    current interpreter and `bin/java.exe`."""
    java_exe = java_exe or java_home / "bin/java.exe"
    data, assets = target / "site-data", target / "assets"
    p = site["ports"]
    env = {
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "CREW_DATA_DIR": str(data), "CDPM_RUNS_DIR": str(data / "cdpm-runs"),
        "CREW_CDPM_RUNS_DIR": str(data / "cdpm-runs"), "CREW_COSMOS_RUNS_DIR": str(data / "cosmos-runs"),
        "CREW_TASK_INBOX": str(data / "task-inbox"),
        "CREW_VMP_REGISTRY": str(data / "qualification/own-executors-registry.json"),
        "CDPM_SETTINGS_FILE": str(data / "cdpm-settings.json"),
        "CDPM_AGENTS_DEFAULT": str(data / "agents-default.json"),
        "CREW_TEMPLATE_CATALOG": str(assets / "catalog"),
        "CREW_COSMOS_BINDINGS": str(assets / "bindings/cosmos-bindings.json"),
        "CREW_CDPM_BINDINGS": str(assets / "bindings/cdpm-bindings.json"),
        "CDPM_BINDINGS_FILE": str(assets / "bindings/cdpm-bindings.json"),
        "CDPM_EXPORT_SPEC_VERSION": "1.21",
        "CDPM_PUBLIC_URL": "http://127.0.0.1:%d" % p["cdpm"],
        "CREW_CDPM_RECORD_API": "http://127.0.0.1:%d" % p["cdpm"],
        "CREW_CHANNEL_URL": "http://127.0.0.1:%d" % p["webapp"],
        "CREW_SOLVER_URL": "http://127.0.0.1:%d" % p["solver"],
        "CREW_CATALOG_URL": site["public_catalog_url"],
        "CREW_AUTH_MODE": site["auth"]["mode"], "CREW_SUPER_ADMIN": site["auth"]["super_admin"],
        "CREW_DEFAULT_LANG": site["language"], "CREW_LLM_DEFAULT_BACKEND": site["llm_backend"],
        "CREW_CONTOUR": site["contour"],
        # РВ-141: номер выпуска внизу интерфейсов всех служб — одно место.
        "CREW_PRODUCT_VERSION": PRODUCT_VERSION,
    }
    if site["auth"]["mode"] == "access":
        env.update(CREW_ACCESS_TEAM=site["auth"]["access_team"],
                   CREW_ACCESS_AUD=site["auth"]["access_aud"], CREW_WEBAPP_COOKIE_SECURE="1")
    py = str(Path(python or sys.executable).absolute())
    services = {}
    for service, role, module, tail in (
        ("webapp", "consumer", "uvicorn", ["webapp.app:app", "--log-level", "warning"]),
        ("cdpm", "producer", "cdpm.webui", []),
        ("catalog", "catalog", "catalog_service", ["--read-only"]),
    ):
        services[service] = {
            "cwd": str(target / role),
            "argv": [py, "-B", "-X", "utf8", "-m", module, *tail,
                     "--host", "127.0.0.1", "--port", str(p[service])],
        }
    services["solver"] = {"cwd": str(target / "native"),
        "argv": [str(java_exe), "-Xms512m", "-Xmx2g",
                 "-Dquarkus.http.host=127.0.0.1", "-Dquarkus.http.port=%d" % p["solver"],
                 "-jar", str(target / "native/quarkus-app/quarkus-run.jar")]}
    return {"schema": "cosmos.exact-launch.v1", "runtime": runtime,
            "python": py, "java_home": str(java_home), "java_exe": str(java_exe),
            "env": env, "services": services}


# Generated launchers contain no interpolated shell syntax. A service is started
# only by a later explicit invocation; neither installation nor --probe calls it.
# Channel/provider credentials are not read or persisted by the installer.
_EXACT_LAUNCHER = r'''"""Explicit manual start; prepared installation is not live acceptance."""
import hashlib, importlib.metadata, json, os, platform, re, subprocess, sys
from pathlib import Path

def fail(code):
    raise SystemExit("EXACT LAUNCH REFUSED: " + code)

def main(argv=None):
    root = Path(__file__).resolve().parent
    raw = (root / "exact-launch.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != "__CONFIG_SHA256__":
        fail("launch-config-hash")
    c = json.loads(raw)
    receipt = json.loads((root / "exact-install-receipt.json").read_text(encoding="utf-8"))
    if receipt.get("state") != "prepared-not-started" or receipt.get("launch_sha256") != "__CONFIG_SHA256__":
        fail("installation-not-prepared")
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in c["services"]:
        fail("choose webapp, cdpm, catalog or solver")
    py = c["runtime"]["python"]
    versions = {}
    for d in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", d.metadata.get("Name", "")).lower()
        if not name or name in versions:
            fail("runtime-distribution-metadata")
        versions[name] = d.version
    if (platform.system() != c["runtime"]["platform"]["system"]
        or platform.machine() != c["runtime"]["platform"]["machine"]
        or platform.python_implementation() != py["implementation"]
        or platform.python_version() != py["version"]
        or Path(sys.executable).resolve() != Path(c["python"]).resolve()
        or hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() != py["executable_sha256"]
        or versions != py["distributions"]):
        fail("runtime-drift")
    java = Path(c["java_home"])
    for path, key in ((java / "release", "release_sha256"), (Path(c["java_exe"]), "executable_sha256")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != c["runtime"]["java"][key]:
            fail("java-drift")
    inherited = {k.upper(): v for k, v in os.environ.items()}
    env = {k: v for k, v in inherited.items() if not re.match(
        r"^(CREW_|CDPM_|COSMOS_|CATALOG_|QUARKUS_|UVICORN_|WATCHFILES_|GIT_|OPENAI_|ANTHROPIC_|CLAUDE_|PYTHON|_JAVA_OPTIONS$|JAVA_TOOL_OPTIONS$|JDK_JAVA_OPTIONS$|JAVA_OPTS$|CLASSPATH$|WEB_CONCURRENCY$|NODE_OPTIONS$|NODE_PATH$)", k)}
    env.update(c["env"])
    if args[0] in ("webapp", "cdpm"):
        token = inherited.get("COSMOS_EXACT_CHANNEL_TOKEN", "")
        own = root / "site-data" / "channel.token"
        if not token and own.is_file():
            # Release path: the installer wrote this site's own fresh token.
            token = own.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            fail("set the same fresh COSMOS_EXACT_CHANNEL_TOKEN for webapp and cdpm")
        env["CREW_CHANNEL_TOKEN"] = token
    # Provider keys are configured in this site's UI; never inherit another site's.
    spec = c["services"][args[0]]
    return subprocess.call(spec["argv"], cwd=spec["cwd"], env=env, shell=False)

if __name__ == "__main__":
    raise SystemExit(main())
'''


def _exact_inputs(profile: dict, bundle: Path, site_path: str | Path, limits) -> dict:
    """Validated site choices, manifest, artifact paths and public examples
    (shared by profile v1 and v2; nothing is written)."""
    import installer_release as ir
    ir._parents(bundle)
    ir._plain(bundle, directory=True)
    site_raw = ir._read_file(Path(site_path).absolute(), 256 * 1024)
    site = _exact_site(_exact_json(site_raw))
    mp = profile["manifest"]
    manifest_path = bundle / mp["path"]
    manifest_raw = ir._read_file(manifest_path, limits.manifest_bytes)
    manifest = ir._validate_manifest(manifest_raw, mp["sha256"], limits)
    ir._need(set(profile["artifacts"]) == set(manifest["artifacts"]), "profile-artifact-coverage")
    paths = {k: bundle / v for k, v in profile["artifacts"].items()}
    # Entry-point completeness is structural, not an import/live acceptance test.
    for role, entry in (("producer", "cdpm/webui.py"), ("consumer", "webapp/app.py"),
                        ("catalog", "catalog_service/__main__.py")):
        ir._need(entry in manifest["roles"][role]["files"], "missing-role-entrypoint")
    ir._need("quarkus-app/quarkus-run.jar" in
             manifest["artifacts"][manifest["native"]["artifact"]]["files"], "missing-native-entrypoint")
    asset_map = manifest["artifacts"][manifest["assets"]["artifact"]]["files"]
    ir._need({"catalog/catalog.json", "bindings/cdpm-bindings.json",
              "bindings/cosmos-bindings.json"} <= set(asset_map), "missing-runtime-assets")
    examples = {}
    for kind, pin in profile["examples"].items():
        body = ir._read_file(bundle / pin["path"], 256 * 1024)
        ir._need(hashlib.sha256(body).hexdigest() == pin["sha256"], "example-hash")
        _exact_example(kind, body)
        examples[kind] = body
    return {"site": site, "site_raw": site_raw, "manifest": manifest,
            "manifest_raw": manifest_raw, "manifest_path": manifest_path,
            "manifest_pin": mp, "paths": paths, "examples": examples}


def _exact_probe_archives(inputs: dict, limits) -> None:
    import installer_release as ir
    total = 0
    for name, spec in inputs["manifest"]["artifacts"].items():
        archive_raw = ir._read_file(inputs["paths"][name], limits.archive_bytes)
        total += len(archive_raw)
        ir._need(total <= limits.total_archive_bytes, "archive-total-limit")
        with ir._open_archive(archive_raw, spec):
            pass  # same exact census/member/hash checks as materialization


def _exact_write_site(target: Path, inputs: dict, launch: dict, profile_raw: bytes,
                      profile_sha256: str, qualification_id: str, runtime_check: str,
                      limits, extra_files: dict | None = None) -> dict:
    """Materialize the release into the absent `target` and add the site's own
    launch contract, launcher, receipt and fresh data folders. Any failure
    removes only paths this call created."""
    import installer_release as ir
    manifest, mp = inputs["manifest"], inputs["manifest_pin"]
    launch_bytes = (json.dumps(launch, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    launch_sha = hashlib.sha256(launch_bytes).hexdigest()
    launcher = _EXACT_LAUNCHER.replace("__CONFIG_SHA256__", launch_sha).encode("utf-8")
    compile(launcher, "run-exact.py", "exec")  # syntax only, never execute
    receipt = {"schema": "cosmos.exact-install-receipt.v1", "state": "prepared-not-started",
               "activated": False, "live_acceptance": False,
               "release_id": manifest["release_id"], "public_identity": manifest["public_identity"],
               "private_origin_commits": {r: manifest["roles"][r]["origin_commit"] for r in ir.ROLES},
               "native_origin_commit": manifest["native"]["origin_commit"],
               "profile_sha256": profile_sha256, "manifest_sha256": mp["sha256"],
               "site_choices_sha256": hashlib.sha256(inputs["site_raw"]).hexdigest(),
               "qualification_id": qualification_id, "launch_sha256": launch_sha,
               "pending": ["own-site executor qualification", "provider setup if required",
                           "manual launch and health/integration acceptance"],
               "runtime_check": runtime_check,
               # CPLEX не ставится: лишь находка; без него расчёт идёт на CP-SAT (движок по умолчанию).
               "cplex": {"found": find_cplex(), "default_engine": "cpsat"}}
    additions = {EXACT_EXAMPLES[k]: v for k, v in inputs["examples"].items()}
    additions.update(extra_files or {})
    additions.update({
        "exact-launch.json": launch_bytes, "run-exact.py": launcher,
        "exact-release.json": inputs["manifest_raw"],
        "exact-install-profile.json": profile_raw,
        "exact-site.json": inputs["site_raw"],
        "EXACT-INSTALL.txt": (
            "PREPARED ONLY: no runtime, firewall, autostart or tunnel was started/changed.\n"
            "This fresh site does not inherit TEST data, credentials or executor qualification.\n"
            "Use this qualified Python: " + launch["python"] + "\n"
            "Explicit manual commands: python run-exact.py solver|catalog|webapp|cdpm\n"
            "Before webapp/cdpm start, provide the same fresh 64-lowercase-hex\n"
            "COSMOS_EXACT_CHANNEL_TOKEN in their process environments; do not publish it.\n"
            "Configure provider credentials through this site's settings UI, if required.\n"
            "Establish this site's own executor qualification. No registry is fabricated.\n"
            "Public catalog navigation is separate from loopback machine callbacks.\n"
            "Release files carry no executable bit: services start only through the\n"
            "interpreters above (python -m ..., java -jar ...).\n"
            "Existing installations/data were not migrated. This is not an automatic\n"
            "upgrade/activation. Do not point legacy uninstall at this layout.\n"
        ).encode("utf-8"),
    })
    for directory in ("final-report", "rules", "cdpm-runs", "cosmos-runs", "task-inbox", "qualification"):
        additions["site-data/" + directory + "/README.txt"] = (
            "Fresh own-site data only. No TEST data or qualification was imported.\n").encode()
    additions["exact-install-receipt.json"] = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode()
    complete = {}
    for component in (*ir.ROLES, "native", "assets"):
        pins = (manifest["roles"][component]["files"] if component in ir.ROLES else
                manifest["artifacts"][manifest[component]["artifact"]]["files"])
        complete.update({component + "/" + p: pin for p, pin in pins.items()})
    ir._need(not (set(additions) & set(complete)), "configuration-payload-collision")
    complete.update({p: {"size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                     for p, b in additions.items()})
    # All output paths are fixed by the validated manifest or this allowlist.
    owned_files, owned_dirs = [], []
    try:
        ir.materialize_release(inputs["manifest_path"], mp["sha256"], inputs["paths"], target,
                               limits=limits)
        owned_dirs.append(target)
        for name in complete:
            if name in additions:
                continue
            path = target / name
            owned_files.append(path)
            for parent in path.parents:
                if parent == target:
                    break
                if parent not in owned_dirs:
                    owned_dirs.append(parent)
        for name, body in additions.items():
            path = target / name
            for parent in reversed(path.parents):
                if parent == target or not parent.is_relative_to(target):
                    continue
                if parent not in owned_dirs:
                    parent.mkdir()
                    owned_dirs.append(parent)
            # Secrets are born owner-only (0600): with umask 002 a plain
            # create left channel.token readable by every local user
            # (miniblack, 04.10.2026). Windows: the profile folder's ACL.
            if name in EXACT_SECRET_FILES:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_BINARY", 0), 0o600)
                owned_files.append(path)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(body)
                continue
            with path.open("xb") as stream:
                owned_files.append(path)
                stream.write(body)
        ir._verify_tree(target, complete, limits)
    except Exception:
        try:
            ir._cleanup(owned_files, owned_dirs)
        except (OSError, ir.ReleaseError) as exc:
            raise ir.ReleaseError("exact-install-cleanup-incomplete") from exc
        raise
    return receipt


def prepare_exact_install(profile_path: str | Path, expected_profile_sha256: str,
                          bundle: str | Path, site_path: str | Path,
                          java_home: str | Path, target: str | Path, *,
                          probe: bool = False) -> dict:
    """Prepare a fresh, non-running exact site; never update an existing target.

    All input/parent directories must stay under trusted local control while the
    call runs. --probe verifies ZIP members too, and writes nothing. Runtime
    versions and interpreter/Java hashes are checked; Python wheel file contents
    are not attested by this v1 profile. Callers must supply a qualified runtime,
    not construct a profile from whichever environment happens to be installed.
    """
    import installer_release as ir
    limits = ir.Limits()
    target, bundle, java_home = (Path(os.path.abspath(p)) for p in (target, bundle, java_home))
    ir._parents(target)
    ir._need(not os.path.lexists(target), "destination-exists")
    raw = ir._read_file(Path(profile_path).absolute(), limits.manifest_bytes)
    ir._digest(expected_profile_sha256)
    ir._need(hashlib.sha256(raw).hexdigest() == expected_profile_sha256, "install-profile-hash")
    profile = _exact_profile(_exact_json(raw))
    inputs = _exact_inputs(profile, bundle, site_path, limits)
    actual_runtime = _exact_runtime(java_home)
    ir._need(actual_runtime == profile["runtime"], "runtime-qualification-mismatch")
    if probe:
        _exact_probe_archives(inputs, limits)
        return {"state": "probe-only", "release_id": inputs["manifest"]["release_id"],
                "profile_sha256": expected_profile_sha256, "activated": False}
    launch = _exact_launch_config(target, inputs["site"], profile["runtime"], java_home)
    return _exact_write_site(
        target, inputs, launch, raw, expected_profile_sha256, profile["qualification_id"],
        "exact metadata and Python/Java executable hashes; not wheel-content attestation",
        limits)


# ------------------------------------------------- release runtime (РВ-143 D)
#
# Profile v2 pins what is the SAME on every user machine: the release manifest
# and archives, the platform list, CPython 3.14, JDK 21 and, per platform, a
# lock of wheel hashes. The site's own venv is built from that lock
# (pip --require-hashes --no-deps --only-binary=:all:), and THIS machine's
# interpreter/Java hashes are then recorded in the site's launch contract, which
# run-exact.py checks for drift. v1 (one machine's executable hashes) stays the
# developer path for an already-qualified runtime.
EXACT_PROFILE_SCHEMA_V2 = "cosmos.exact-install-profile.v2"
RUNTIME_PYTHON = "3.14"
RUNTIME_JAVA = "21"
RUNTIME_PLATFORMS = {
    "Windows-AMD64": {"system": "Windows", "machine": "AMD64",
                      "java": "bin/java.exe", "venv_python": "Scripts/python.exe"},
    "Linux-x86_64": {"system": "Linux", "machine": "x86_64",
                     "java": "bin/java", "venv_python": "bin/python"},
}
WINGET_PYTHON = ["winget", "install", "-e", "--id", "Python.Python.3.14", "--scope", "user",
                 "--accept-package-agreements", "--accept-source-agreements"]
WINGET_JDK = ["winget", "install", "-e", "--id", "EclipseAdoptium.Temurin.21.JDK",
              "--accept-package-agreements", "--accept-source-agreements"]
LINUX_RUNTIME_HINT = (
    "Install CPython 3.14 with venv and JDK 21, then run the installer again:\n"
    "  Debian/Ubuntu: sudo apt install python3.14 python3.14-venv openjdk-21-jre-headless\n"
    "    (older Ubuntu: Python 3.14 from the deadsnakes PPA)\n"
    "  Debian 13: python3.14 is not in apt - `uv python install 3.14` (no root,\n"
    "    ~/.local/bin/python3.14) or build it from python.org; JDK: sudo apt install\n"
    "    openjdk-21-jre-headless\n"
    "  Fedora/RHEL:   sudo dnf install python3.14 java-21-openjdk-headless\n"
    "The installer never calls sudo itself.")


class RuntimeRefused(RuntimeError):
    """The machine lacks the release runtime; words say what to install."""


def _platform_key(system: str | None = None, machine: str | None = None) -> str:
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if machine in ("amd64", "x86_64", "x64"):
        machine = "AMD64" if system == "Windows" else "x86_64"
    return "%s-%s" % (system, machine)


def _exact_profile_v2(doc: dict) -> dict:
    import installer_release as ir
    ir._keys(doc, {"schema", "qualification_id", "manifest", "artifacts",
                   "platforms", "examples"}, "exact-profile-keys")
    ir._need(doc["schema"] == EXACT_PROFILE_SCHEMA_V2, "exact-profile-schema")
    ir._need(type(doc["qualification_id"]) is str
             and bool(ir._ID.fullmatch(doc["qualification_id"])), "qualification-id")
    ir._keys(doc["manifest"], {"path", "sha256"}, "profile-manifest")
    ir._relative(doc["manifest"]["path"])
    ir._digest(doc["manifest"]["sha256"])
    ir._need(type(doc["artifacts"]) is dict and bool(doc["artifacts"]), "profile-artifacts")
    for name, path in doc["artifacts"].items():
        ir._need(type(name) is str and bool(ir._ID.fullmatch(name)), "profile-artifact-id")
        ir._relative(path)
    ir._need(type(doc["platforms"]) is dict and bool(doc["platforms"])
             and set(doc["platforms"]) <= set(RUNTIME_PLATFORMS), "unsupported-runtime-platform")
    for spec in doc["platforms"].values():
        ir._keys(spec, {"python", "java", "lock"}, "platform-profile")
        ir._need(spec["python"] == RUNTIME_PYTHON, "unsupported-python-profile")
        ir._need(spec["java"] == RUNTIME_JAVA, "unsupported-java-profile")
        ir._keys(spec["lock"], {"path", "sha256"}, "platform-lock")
        ir._relative(spec["lock"]["path"])
        ir._digest(spec["lock"]["sha256"])
    ir._keys(doc["examples"], set(EXACT_EXAMPLES), "examples-profile")
    for pin in doc["examples"].values():
        ir._keys(pin, {"path", "sha256"}, "example-pin")
        ir._relative(pin["path"])
        ir._digest(pin["sha256"])
    paths = [doc["manifest"]["path"], *doc["artifacts"].values(),
             *(p["path"] for p in doc["examples"].values()),
             *(s["lock"]["path"] for s in doc["platforms"].values())]
    ir._path_set(paths)  # duplicate/case/parent collisions, not just traversal
    return doc


_LOCK_LINE = re.compile(r"([a-z0-9][a-z0-9-]*)==([A-Za-z0-9.+!-]{1,64})"
                        r"((?: --hash=sha256:[0-9a-f]{64})+)")


def _parse_lock(raw: bytes) -> dict[str, str]:
    """{name: version}; EVERY line must carry wheel hashes (exact acceptance)."""
    import installer_release as ir
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise ir.ReleaseError("lock-encoding") from exc
    pins = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = _LOCK_LINE.fullmatch(line)
        ir._need(match is not None, "lock-line")
        ir._need(match.group(1) not in pins, "lock-duplicate")
        pins[match.group(1)] = match.group(2)
    ir._need(0 < len(pins) <= 1000, "lock-empty")
    return pins


def _sp_run(*args, **kwargs):
    """subprocess.run looked up at call time (tests replace it; defaults
    bound at definition time would not see that)."""
    return subprocess.run(*args, **kwargs)


# Run INSIDE the candidate interpreter: facts only, no imports of the product.
_PY_FACTS = (
    "import hashlib, importlib.metadata as m, json, platform, re, sys\n"
    "d = {}\n"
    "for x in m.distributions():\n"
    "    n = re.sub(r'[-_.]+', '-', x.metadata.get('Name', '')).lower()\n"
    "    if not n or n in d: raise SystemExit('runtime-distribution-metadata')\n"
    "    d[n] = x.version\n"
    "print(json.dumps({'implementation': platform.python_implementation(),\n"
    "  'version': platform.python_version(), 'system': platform.system(),\n"
    "  'machine': platform.machine(), 'executable': sys.executable,\n"
    "  'executable_sha256': hashlib.sha256(open(sys.executable, 'rb').read()).hexdigest(),\n"
    "  'venv': sys.prefix != sys.base_prefix, 'distributions': d}))\n")


def _python_facts(python: Path, run=_sp_run) -> dict | None:
    try:
        done = run([str(python), "-I", "-c", _PY_FACTS], capture_output=True,
                   text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return None


def _python_ok(facts: dict | None, plat: str) -> bool:
    spec = RUNTIME_PLATFORMS[plat]
    return bool(facts) and facts["implementation"] == "CPython" \
        and facts["version"].startswith(RUNTIME_PYTHON + ".") \
        and _platform_key(facts["system"], facts["machine"]) == plat \
        and (spec["system"] == facts["system"])


def _java_facts(java_home: Path, plat: str) -> dict | None:
    """JDK 21 by its `release` file and OS-specific `java`; never executed."""
    import installer_release as ir
    exe = java_home / RUNTIME_PLATFORMS[plat]["java"]
    try:
        release = ir._read_file(java_home / "release", 256 * 1024)
        exe_raw = ir._read_file(exe, 256 * 1024 * 1024)
    except ir.ReleaseError:
        return None
    match = re.search(rb'^JAVA_VERSION="(21(?:\.\d+){0,3}(?:\+\d+)?)"\s*$', release, re.MULTILINE)
    if match is None:
        return None
    return {"version": match.group(1).decode("ascii"),
            "release_sha256": hashlib.sha256(release).hexdigest(),
            "executable_sha256": hashlib.sha256(exe_raw).hexdigest(), "executable": str(exe)}


def _python_candidates(plat: str, run=_sp_run) -> list[Path]:
    found: list[Path] = []
    if plat.startswith("Windows"):
        try:
            done = run(["py", "-3.14", "-c", "import sys; print(sys.executable)"],
                       capture_output=True, text=True, timeout=30)
            if done.returncode == 0 and done.stdout.strip():
                found.append(Path(done.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            pass
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        found += [local / "Programs/Python/Python314/python.exe",
                  local / "Python/pythoncore-3.14-64/python.exe",
                  Path(r"C:\Python314\python.exe"),
                  Path(r"C:\Program Files\Python314\python.exe")]
    else:
        which = shutil.which("python3.14")
        if which:
            found.append(Path(which))
        # ~/.local/bin: where `uv python install 3.14` puts it (Debian 13 has
        # no python3.14 package); that folder is not always on PATH.
        found += [Path("/usr/bin/python3.14"), Path("/usr/local/bin/python3.14"),
                  Path.home() / ".local/bin/python3.14"]
    seen, out = set(), []
    for p in found:
        key = str(p).casefold()
        if key not in seen and p.is_file():
            seen.add(key)
            out.append(p)
    return out


def _jdk_candidates(plat: str) -> list[Path]:
    found: list[Path] = []
    if os.environ.get("JAVA_HOME"):
        found.append(Path(os.environ["JAVA_HOME"]))
    if plat.startswith("Windows"):
        for vendor in ("Eclipse Adoptium", "Microsoft", "Java", "Zulu"):
            root = Path(r"C:\Program Files") / vendor
            if root.is_dir():
                found += sorted(root.glob("jdk-21*"), reverse=True)
    else:
        root = Path("/usr/lib/jvm")
        if root.is_dir():
            found += sorted((p for p in root.iterdir() if "21" in p.name), reverse=True)
    return found


def find_release_runtime(plat: str, run=_sp_run) -> tuple[Path | None, Path | None]:
    """(CPython 3.14 with venv, JDK 21 home) already on this machine, or None."""
    python = None
    for cand in _python_candidates(plat, run):
        if _python_ok(_python_facts(cand, run), plat):
            python = cand
            break
    jdk = next((j for j in _jdk_candidates(plat) if _java_facts(j, plat)), None)
    return python, jdk


def bootstrap_runtime(plat: str, *, allow_install: bool,
                      run=_sp_run) -> tuple[Path, Path]:
    """Find, or on Windows (with consent) install, CPython 3.14 and JDK 21.

    Linux: never sudo; the exact package commands are given in words."""
    python, jdk = find_release_runtime(plat, run)
    if python and jdk:
        return python, jdk
    if plat.startswith("Windows") and allow_install:
        for command, missing in ((WINGET_PYTHON, python is None), (WINGET_JDK, jdk is None)):
            if missing:
                run(command, check=False)
        python, jdk = find_release_runtime(plat, run)
        if python and jdk:
            return python, jdk
    missing = [n for n, v in (("CPython " + RUNTIME_PYTHON, python), ("JDK " + RUNTIME_JAVA, jdk))
               if v is None]
    if plat.startswith("Windows"):
        hint = ("Install with consent (rerun with --install-runtime) or by hand:\n  "
                + " ".join(WINGET_PYTHON) + "\n  " + " ".join(WINGET_JDK))
    else:
        hint = LINUX_RUNTIME_HINT
    raise RuntimeRefused("missing %s on %s.\n%s" % (" and ".join(missing), plat, hint))


def build_site_venv(python: Path, venv_dir: Path, lock_path: Path, plat: str, *,
                    find_links: Path | None = None, run=_sp_run) -> dict:
    """Fresh venv from the platform lock; returns its facts (as _python_facts).

    pip installs ONLY the lock's wheels, each checked against its hash; the
    resulting distribution set must equal the lock (plus the venv's own pip)."""
    import installer_release as ir
    ir._need(not os.path.lexists(venv_dir), "venv-exists")
    pins = _parse_lock(ir._read_file(lock_path, 1024 * 1024))
    done = run([str(python), "-m", "venv", str(venv_dir)], capture_output=True, text=True)
    ir._need(done.returncode == 0, "venv-create")
    vpy = venv_dir / RUNTIME_PLATFORMS[plat]["venv_python"]
    command = [str(vpy), "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
               "--require-hashes", "--no-input", "--disable-pip-version-check",
               "-r", str(lock_path)]
    if find_links is not None:
        command += ["--no-index", "--find-links", str(find_links)]
    done = run(command, capture_output=True, text=True)
    ir._need(done.returncode == 0, "venv-lock-install")
    facts = _python_facts(vpy, run)
    ir._need(_python_ok(facts, plat) and facts["venv"], "venv-runtime")
    dists = dict(facts["distributions"])
    dists.pop("pip", None)
    ir._need(dists == pins, "venv-distributions-differ-from-lock")
    return facts


def prepare_release_install(profile_path: str | Path, expected_profile_sha256: str,
                            bundle: str | Path, site_path: str | Path, root: str | Path, *,
                            allow_runtime_install: bool = False, find_links: Path | None = None,
                            probe: bool = False, run=_sp_run) -> dict:
    """User path (profile v2): runtime → venv from lock → prepared site.

    Layout: <root>/venv (the site's interpreter) and <root>/site (materialized
    release + launch contract). <root> must not exist; on failure only paths
    created here are removed. Nothing is started."""
    import installer_release as ir
    limits = ir.Limits()
    root, bundle = Path(os.path.abspath(root)), Path(os.path.abspath(bundle))
    ir._parents(root)
    ir._need(not os.path.lexists(root), "destination-exists")
    raw = ir._read_file(Path(profile_path).absolute(), limits.manifest_bytes)
    ir._digest(expected_profile_sha256)
    ir._need(hashlib.sha256(raw).hexdigest() == expected_profile_sha256, "install-profile-hash")
    profile = _exact_profile_v2(_exact_json(raw))
    plat = _platform_key()
    ir._need(plat in profile["platforms"], "unsupported-runtime-platform")
    lock = profile["platforms"][plat]["lock"]
    lock_path = bundle / lock["path"]
    lock_raw = ir._read_file(lock_path, 1024 * 1024)
    ir._need(hashlib.sha256(lock_raw).hexdigest() == lock["sha256"], "lock-hash")
    _parse_lock(lock_raw)
    inputs = _exact_inputs(profile, bundle, site_path, limits)
    _exact_probe_archives(inputs, limits)
    if probe:
        python, jdk = find_release_runtime(plat, run)
        return {"state": "probe-only", "release_id": inputs["manifest"]["release_id"],
                "profile_sha256": expected_profile_sha256, "platform": plat,
                "python": str(python) if python else None,
                "jdk": str(jdk) if jdk else None, "activated": False}
    python, jdk = bootstrap_runtime(plat, allow_install=allow_runtime_install, run=run)
    java = _java_facts(jdk, plat)
    ir._need(java is not None, "java-runtime")
    root.mkdir()
    try:
        facts = build_site_venv(python, root / "venv", lock_path, plat,
                                find_links=find_links, run=run)
        runtime = {"platform": {"system": facts["system"], "machine": facts["machine"]},
                   "python": {k: facts[k] for k in ("implementation", "version",
                                                     "executable_sha256", "distributions")},
                   "java": {k: java[k] for k in ("version", "release_sha256", "executable_sha256")}}
        launch = _exact_launch_config(root / "site", inputs["site"], runtime, jdk,
                                      python=Path(facts["executable"]),
                                      java_exe=Path(java["executable"]))
        receipt = _exact_write_site(
            root / "site", inputs, launch, raw, expected_profile_sha256,
            profile["qualification_id"],
            "wheels by lock hashes (pip --require-hashes); venv distributions = lock; "
            "this machine's Python/Java hashes recorded for drift checks",
            limits, extra_files={"exact-lock.txt": lock_raw,
                                 # this site's own fresh channel token (never published)
                                 "site-data/channel.token":
                                     secrets.token_hex(32).encode("ascii")})
    except BaseException:
        # Own paths only: the venv this call created, then the empty root.
        venv = root / "venv"
        if venv.exists() and not venv.is_symlink():
            shutil.rmtree(venv)
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
        raise
    return receipt


# The user path is ONE pinned release (РВ-143, owner's word 04.10.2026): the
# installer installs exactly this tag from its GitHub Release assets and checks
# the profile against the SHA-256 below. The digest is written after the
# release archives are built (the archives exclude the installer's own files,
# so this constant does not change them); until then the release path refuses
# with words instead of falling back to a rolling checkout.
RELEASE = {"tag": "v1.4.10", "repository": "eliduc/COSMOS",
           # profile.json of the release assets built from slice ddff9888
           # (origin 10bfcee6); see tools/build_release.py.
           "profile_sha256": "05aca22d9e337147766fe6f979040d256248372640b07a333ed32eeb465093c5"}


def _release_asset_url(name: str) -> str:
    # GitHub Release assets are flat: the profile's relative path keeps only its
    # file name on the release page (examples/x and locks/y upload as x and y).
    return "https://github.com/%s/releases/download/%s/%s" % (
        RELEASE["repository"], RELEASE["tag"], name.rsplit("/", 1)[-1])


def _fetch(url: str, limit: int, opener=None) -> bytes:
    import installer_release as ir
    opener = opener or urllib.request.urlopen
    try:
        with opener(urllib.request.Request(url, headers=_UA), timeout=120) as response:
            # Chunks, never read(limit): that would preallocate the whole limit.
            parts, size = [], 0
            while size <= limit:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
            body = b"".join(parts)
    except (OSError, urllib.error.URLError) as exc:
        raise ir.ReleaseError("download-failed: %s" % url) from exc
    ir._need(len(body) <= limit, "download-size-limit")
    return body


def download_release(dest: Path, profile_sha256: str, opener=None) -> Path:
    """Fetch the pinned release into the absent `dest`; every byte is checked
    against the profile SHA (pinned in this installer) and the pins it carries.
    Returns the downloaded profile path. Nothing is installed here."""
    import installer_release as ir
    limits = ir.Limits()
    ir._digest(profile_sha256)
    dest.mkdir()
    raw = _fetch(_release_asset_url("profile.json"), limits.manifest_bytes, opener)
    ir._need(hashlib.sha256(raw).hexdigest() == profile_sha256, "install-profile-hash")
    profile = _exact_profile_v2(_exact_json(raw))
    pins = {profile["manifest"]["path"]: (profile["manifest"]["sha256"], limits.manifest_bytes)}
    pins.update({p["path"]: (p["sha256"], 256 * 1024) for p in profile["examples"].values()})
    pins.update({s["lock"]["path"]: (s["lock"]["sha256"], 1024 * 1024)
                 for s in profile["platforms"].values()})
    ir._need(len({n.rsplit("/", 1)[-1] for n in pins} | set(
        v.rsplit("/", 1)[-1] for v in profile["artifacts"].values())) ==
        len(pins) + len(profile["artifacts"]), "release-asset-name-collision")
    for name, (digest, limit) in pins.items():
        body = _fetch(_release_asset_url(name), limit, opener)
        ir._need(hashlib.sha256(body).hexdigest() == digest, "asset-hash: " + name)
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(body)
    manifest = ir._validate_manifest((dest / profile["manifest"]["path"]).read_bytes(),
                                     profile["manifest"]["sha256"], limits)
    for artifact, name in profile["artifacts"].items():
        body = _fetch(_release_asset_url(name), limits.archive_bytes, opener)
        ir._need(hashlib.sha256(body).hexdigest() == manifest["artifacts"][artifact]["sha256"],
                 "asset-hash: " + name)
        (dest / name).write_bytes(body)
    (dest / "profile.json").write_bytes(raw)
    return dest / "profile.json"


def release_site_choices(args) -> dict:
    """Fresh-site choices for the user path: defaults plus explicit options."""
    ports = {"webapp": PORT_DEFAULT, "cdpm": CDPM_PORT, "catalog": CATALOG_PORT,
             "solver": SOLVER_PORT}
    if args.port is not None:
        ports["webapp"] = args.port
    if args.catalog_port is not None:
        ports["catalog"] = args.catalog_port
    return {"schema": EXACT_SITE_SCHEMA, "ports": ports,
            "public_catalog_url": "http://127.0.0.1:%d" % ports["catalog"],
            "auth": {"mode": "token", "super_admin": args.admin_email,
                     "access_team": None, "access_aud": None},
            "language": args.lang, "contour": "local",
            "llm_backend": "local", "data_policy": "internal"}


# Health probes per service (solver: Quarkus answers GET /schedules; /q/health
# is not exposed by this build).
SERVICE_PROBES = {"solver": "/schedules", "catalog": "/healthz",
                  "cdpm": "/api/runs?brief=1", "webapp": "/healthz"}


def start_site(root: Path, *, wait_s: float = 240.0, popen=subprocess.Popen,
               probe=_http, sleep=time.sleep, clock=time.monotonic,
               port_free=None) -> dict:
    """Start the four services of a prepared release site through its own
    run-exact.py, each detached with its own log, then probe each until it
    answers 200 or the deadline passes. Returns {service: status}.

    A 200 counts only from OUR process (review GPT 16:45): every port must be
    free before anything starts (an occupied port is a refusal, nothing is
    started), and a service whose launcher has exited is a failure even if
    something answers on its port. The pid of each started launcher is
    returned so the caller can stop exactly what was started."""
    port_free = port_free or _port_free
    site = root / "site"
    launch = json.loads((site / "exact-launch.json").read_bytes())
    order = ("solver", "catalog", "cdpm", "webapp")
    ports = {}
    for service in order:
        argv = launch["services"][service]["argv"]
        ports[service] = int(argv[-1]) if service != "solver" else int(
            next(a.split("=", 1)[1] for a in argv if a.startswith("-Dquarkus.http.port=")))
    busy = [s for s in order if not port_free(ports[s])]
    if busy:
        return {s: {"url": "http://127.0.0.1:%d" % ports[s], "http": 0, "ok": False,
                    "pid": None, "reason": "port %d already in use; nothing started" % ports[s]}
                for s in order}
    logs = site / "site-data" / "logs"
    logs.mkdir(exist_ok=True)
    flags = {}
    if IS_WIN:
        flags["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                  | subprocess.DETACHED_PROCESS)
    else:
        flags["start_new_session"] = True
    status = {}
    for service in order:
        log = (logs / (service + ".log")).open("ab")
        proc = popen([launch["python"], "-B", str(site / "run-exact.py"), service], cwd=str(site),
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **flags)
        url = "http://127.0.0.1:%d%s" % (ports[service], SERVICE_PROBES[service])
        deadline = clock() + wait_s
        code, reason = 0, "no answer before the deadline"
        while clock() < deadline:
            exited = proc.poll()
            if exited is not None:
                code, reason = 0, "launcher exited with code %s; see log" % exited
                break
            code, _ = probe(url, timeout=5)
            if code == 200:
                reason = ""
                break
            sleep(2)
        alive = code == 200 and proc.poll() is None
        status[service] = {"url": url, "http": code, "ok": alive, "pid": proc.pid,
                           "reason": "" if alive else (reason or "launcher exited after answering"),
                           "log": str(logs / (service + ".log"))}
    return status


def _release_main(args) -> int:
    import installer_release as ir
    if not RELEASE["profile_sha256"] and not args.bundle:
        print("RELEASE REFUSED: this installer revision carries no pinned "
              "profile SHA-256 for %s; nothing changed. Development only: "
              "--dev-legacy." % RELEASE["tag"])
        return 2
    if not args.admin_email:
        print("RELEASE REFUSED: --admin-email is required (the site's super admin); "
              "nothing changed.")
        return 2
    default_root = Path("C:/cosmos") if IS_WIN else Path.home() / "cosmos"
    root = Path(os.path.abspath(args.target)) if args.target else default_root
    work = Path(tempfile.mkdtemp(prefix="cosmos-%s-" % RELEASE["tag"]))
    try:
        if args.bundle:
            # Offline/USB copy of the same release assets (layout of profile paths).
            if not args.profile_sha256:
                print("RELEASE REFUSED: --bundle needs --profile-sha256 (from the "
                      "release page); nothing changed.")
                return 2
            bundle, profile_sha = Path(args.bundle), args.profile_sha256
            profile_path = bundle / "profile.json"
        else:
            bundle, profile_sha = work / "assets", RELEASE["profile_sha256"]
            profile_path = download_release(bundle, profile_sha)
        site_path = work / "site.json"
        site_path.write_bytes((json.dumps(release_site_choices(args), ensure_ascii=False,
                                          indent=2) + "\n").encode("utf-8"))
        receipt = prepare_release_install(profile_path, profile_sha, bundle, site_path, root,
                                          allow_runtime_install=args.install_runtime,
                                          probe=args.probe)
    except RuntimeRefused as exc:
        print("RELEASE REFUSED: %s\nNothing changed." % exc)
        return 2
    except (ir.ReleaseError, OSError, ValueError) as exc:
        print("RELEASE REFUSED: %s. No activation; no legacy fallback." % exc)
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)  # own temporary download only
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if args.probe:
        print("Release probe only; no changes.")
        return 0
    print("COSMOS %s prepared in %s (not started)." % (RELEASE["tag"], root))
    if not args.start:
        print("Start: %s %s solver|catalog|cdpm|webapp"
              % (root / "venv" / RUNTIME_PLATFORMS[_platform_key()]["venv_python"],
                 root / "site" / "run-exact.py"))
        return 0
    status = start_site(root)
    for service, s in status.items():
        print("  %-8s %s  %s  pid %s  %s" % (service, "OK" if s["ok"] else "FAILED",
                                            s["url"], s["pid"], s["reason"] or s.get("log", "")))
    return 0 if all(s["ok"] for s in status.values()) else 3


def _exact_main(args) -> int:
    import installer_release as ir
    required = (args.exact_profile, args.exact_profile_sha256, args.exact_bundle,
                args.exact_site, args.java_home, args.target)
    if not all(required) or args.answers or args.port is not None or args.catalog_port is not None \
            or args.repo != REPO_URL_DEFAULT or getattr(args, "dev_legacy", False):
        print("EXACT REFUSED: provide all exact inputs; do not mix legacy options.")
        return 2
    try:
        result = prepare_exact_install(*required[:2], required[2], required[3],
                                       required[4], required[5], probe=args.probe)
    except (ir.ReleaseError, OSError, ValueError) as exc:
        print("EXACT REFUSED: %s. No activation; no legacy fallback." % exc)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Exact release prepared only; not started and not live-accepted."
          if not args.probe else "Exact probe only; no changes.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Установщик COSMOS: обследует площадку, выводит решения, "
                    "спрашивает только неизбежное.")
    ap.add_argument("--target", help="каталог установки")
    ap.add_argument("--port", type=int, help="порт приложения")
    ap.add_argument("--catalog-port", type=int,
                    help="порт службы каталога шаблонов (по умолчанию %d)"
                         % CATALOG_PORT)
    ap.add_argument("--repo", default=REPO_URL_DEFAULT, help="адрес репозитория")
    ap.add_argument("--probe", action="store_true",
                    help="только обследование и замысел, ничего не менять")
    ap.add_argument("--answers", help="файл JSON с ответами (без диалога)")
    ap.add_argument("--exact-profile", help="offline exact-install profile JSON")
    ap.add_argument("--exact-profile-sha256", help="independently trusted SHA-256 of that profile")
    ap.add_argument("--exact-bundle", help="local directory of pinned manifest, ZIPs and public examples")
    ap.add_argument("--exact-site", help="explicit fresh-site choices JSON (no secrets/TEST registry)")
    ap.add_argument("--java-home", help="already-qualified JDK 21 directory; exact mode only")
    ap.add_argument("--admin-email", help="release path: e-mail of the site's super admin")
    ap.add_argument("--lang", default="en", choices=("en", "ru", "ar", "uz", "kk"),
                    help="release path: interface language")
    ap.add_argument("--install-runtime", action="store_true",
                    help="release path, Windows: consent to install CPython 3.14 and JDK 21 via winget")
    ap.add_argument("--bundle", help="release path: local copy of the release assets (offline/USB)")
    ap.add_argument("--profile-sha256", help="release path with --bundle: SHA-256 of profile.json")
    ap.add_argument("--start", action="store_true",
                    help="release path: start the four services after preparation and probe them")
    ap.add_argument("--dev-legacy", action="store_true",
                    help="DEVELOPMENT ONLY: legacy rolling-checkout installer "
                         "(not a pinned release; not for users)")
    args = ap.parse_args(argv)
    if any((args.exact_profile, args.exact_profile_sha256, args.exact_bundle,
            args.exact_site, args.java_home)):
        return _exact_main(args)
    if not args.dev_legacy:
        return _release_main(args)
    print("DEVELOPMENT ONLY: legacy installer (rolling checkout, not the pinned "
          "release %s); not for users." % RELEASE["tag"])

    default_target = Path("C:/cosmos") if IS_WIN else Path.home() / "cosmos"
    target = Path(args.target).resolve() if args.target else default_target

    # До выбора языка печатается только имя продукта: любое слово здесь
    # было бы на языке, которого человек может не знать.
    print("COSMOS")
    print("=" * 60)

    interactive_tty = (not args.probe) and (not args.answers) \
        and sys.stdin.isatty()

    answers_early = None
    if args.answers:
        answers_early = json.loads(
            Path(args.answers).read_text(encoding="utf-8"))
    ask = Asker(answers_early, interactive=interactive_tty)

    # --- ПЕРВЫЙ вопрос — язык. Он же станет языком программы. Варианты
    # каждый на своём языке: человек узнаёт родной без перевода.
    lang = ask.choice(
        "lang", T("q.lang"),
        [("en", "English"),
         ("ar", "العربية"),
         ("uz", "Oʻzbekcha"),
         ("kk", "Қазақша"),
         ("ru", "Русский")],
        default="en")
    # Дальше весь диалог идёт на выбранном языке — и он же станет языком
    # программы (пусковой файл получит CREW_DEFAULT_LANG).
    set_lang(lang)
    print("\n" + T("inst.title"))

    # --- ВТОРОЙ вопрос — куда ставить. Спрашивается всегда (кроме явного
    # --target): каталог — не то решение, которое можно принять за
    # человека молча.
    if not args.target:
        default_target = str(target)
        got = ask.text("target", T("q.target"), default=default_target)
        target = Path(got).resolve() if got else target

    # Журнал созданного открывается сразу, как стал известен каталог, и
    # ДО первого изменения на машине: прерванная установка обязана
    # оставить точный список того, что успела создать, — иначе снос
    # будет угадывать. Режим обследования журнала не заводит.
    cp = None if args.probe else Checkpoints(
        target, target.parent / (target.name + "-data"))

    survey = Survey(target, args.repo)
    survey.run()
    survey.report()

    # Нет доступа к исходникам, но человек рядом — помочь получить ключ,
    # а не отсылать к документации: устанавливать будет неспециалист.
    access = survey.get("доступ к исходникам")
    if access and not access.ok and interactive_tty:
        if _offer_access_key(args.repo, cp):
            survey.probes.remove(access)
            survey.probe_repo()
            survey.report()

    blockers = survey.blockers()
    if blockers:
        print("\n  " + T("inst.impossible"))
        for b in blockers:
            print("    * %s: %s" % (probe_title(b.name), b.detail))
        print("\n  " + T("inst.nothing_changed"))
        return 2

    plan = build_plan(survey, ask, args)
    plan.set("язык интерфейса", lang,
             "спрошено" if "lang" in ask.asked else "умолчание",
             T("why.lang"))
    if "target" in ask.asked:
        for d in plan.decisions:
            if d.key == "каталог":
                d.source, d.why = "спрошено", T("why.dir")
    plan.report()

    if args.probe:
        print("\n  " + T("inst.probe_only"))
        return 0

    if ask.interactive:
        go = ask.yes("go", T("q.go"), default=False)
        if not go:
            print("\n  " + T("inst.cancelled"))
            return 1

    cp.note("замысел принят", порт=plan.value("порт"),
            режим_входа=str(plan.value("режим входа")),
            язык=str(plan.value("язык интерфейса")))
    inst = Installer(target, plan, args.repo, survey, checkpoints=cp)
    ok = inst.run()

    verified, notes = (False, [T("rep.note.unfinished")])
    if ok:
        verified, notes = verify(target, plan, cp)

    # Отчёт — приложение к успеху, а не его условие: первая установка
    # прошла целиком и упала на самой записи отчёта, напугав человека
    # аварией после слова «Готово».
    try:
        report = write_report(target, survey, plan, inst.steps, verified,
                              notes)
        cp.created("отчёт об установке", report)
        print("\n  " + T("inst.report", report))
    except OSError as exc:
        print("\n  " + T("inst.report_failed", exc))
    print("\n  " + T("inst.journal", cp.path))
    if verified:
        print_how_to_run(target, plan)
    print("\n  " + (T("inst.done") if verified else T("inst.not_confirmed")))
    if verified:
        offer_start_now(target, plan, ask, cp)
    # Журнал закрывается ПОСЛЕ возможного запуска: запущенная программа —
    # тоже след установки, и снос должен знать о ней.
    cp.finish(verified)
    return 0 if verified else 3


if __name__ == "__main__":
    raise SystemExit(main())
