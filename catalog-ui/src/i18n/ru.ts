// Словарь подписей интерфейса каталога шаблонов (русский).
//
// Здесь живут ВСЕ слова экранов: в файлах *.tsx русских строк нет
// вовсе — гейт T13 краснеет на первой же. Содержимое самих записей
// каталога (заголовки семей, семантика, слова-носители, состояния
// «есть»/«заявлен»/«нет») приходит данными от службы и не переводится.
//
// Порядок ключей — по экранам: общее, оболочка, строка состояния,
// «Семьи и критерии», карточка записи, «Словари», «Заявки»,
// «Исполнители по продуктам», «Версии и выпуски», служебные таблицы.
export const ru = {
  // ── Общее ───────────────────────────────────────────────────────
  'common.cancel': 'Отмена',
  'common.confirm': 'Подтвердить',
  'common.loading': 'Читаем каталог…',
  'common.empty': 'Ничего не найдено',
  'common.reset': 'Сбросить фильтры',
  'common.copied': 'Скопировано в буфер обмена',
  'common.all': 'все',
  'common.notSet': '— не задано —',
  'common.add': 'Добавить',
  'common.remove': 'Убрать',
  'common.note': 'Заметка',
  'common.changeNote': 'Заметка правки',
  'common.changeNoteHint': 'слова автора правки; без них черновик не '
    + 'сохраняется',
  'common.refusal': 'Отказ {code}: {words}',
  'common.serviceDown': 'Служба каталога не отвечает: {words}',
  'common.retry': 'Повторить',
  'common.saveDraft': 'Сохранить черновик',
  'common.discardDraft': 'Отменить черновик',
  'common.changes': 'Что изменилось',
  'common.open': 'Открыть',
  'common.shown': 'Показано {shown} из {total}',
  'common.no': 'нет',
  'common.closeWindow': 'Закрыть окно',
  'common.closeToast': 'Закрыть уведомление',
  'theme.light': 'Светлая тема',
  'theme.dark': 'Тёмная тема',
  'theme.system': 'Как в системе',

  // ── Оболочка и меню ─────────────────────────────────────────────
  'nav.title': 'Каталог шаблонов',
  'nav.subtitle': 'записи, заявки, исполнители, версии',
  'nav.records': 'Семьи и критерии',
  'nav.recordsTip': 'Записи каталога: семьи правил и критерии — карточка '
    + 'со всеми полями, черновики и заявки',
  'nav.dictionaries': 'Словари',
  'nav.dictionariesTip': 'Закрытые перечни и объекты каталога: единицы, '
    + 'отношения, шаблоны проверок, порядок лексикона',
  'nav.requests': 'Заявки',
  'nav.requestsTip': 'Заявки на записи и виды исполнителей: подача, '
    + 'решение владельца, перевод в записи',
  'nav.executors': 'Исполнители по продуктам',
  'nav.executorsTip': 'Матрица «запись × вид × продукт» из файлов '
    + 'привязок COSMOS и CDPM — только чтение',
  'nav.versions': 'Версии и выпуски',
  'nav.versionsTip': 'Сборка черновика, сравнение версий, выпуск '
    + 'владельцем, выгрузка и загрузка одним файлом',
  'nav.collapse': 'Задвинуть меню',
  'nav.expand': 'Выдвинуть меню',
  'nav.productVersion': 'версия {version}',
  'nav.palettePlaceholder': 'Куда перейти…',
  'nav.paletteSections': 'Разделы',
  'nav.paletteRecords': 'Записи',
  'nav.paletteTitle': 'Палитра команд',
  'nav.dismissAll': 'Скрыть все уведомления',
  'nav.errorTitle': 'Что-то пошло не так',
  'nav.errorWords': 'Интерфейс столкнулся с ошибкой. Попробуйте ещё раз '
    + 'или скопируйте детали для отчёта.',
  'nav.copyDetails': 'Скопировать детали',

  // ── Строка состояния (GET /api/session) ─────────────────────────
  'session.catalog': 'Каталог {version} · {fingerprint}',
  'session.draft': 'черновик',
  'session.release': 'выпуск',
  'session.modeView': 'режим просмотра',
  'session.workplace': 'рабочее место: {actor}',
  'session.drafts': 'черновиков {n}',
  'session.requestsOpen': 'открытых заявок {n}',
  'session.copyFingerprint': 'Скопировать отпечаток целиком',
  // Слов отказа «режим просмотра» и «только владелец» здесь нет
  // намеренно: их говорит служба (GET /api/session → rights[операция].
  // words), а экран показывает подсказкой. Копия слов на экране была бы
  // вторым вычислением прав и разошлась бы со службой при первой же
  // правке её текстов.

  // ── Экран «Семьи и критерии» ────────────────────────────────────
  'records.title': 'Семьи и критерии',
  'records.families': 'Семьи',
  'records.criteria': 'Критерии',
  'records.searchPlaceholder': 'Имя, заголовок, предмет, слова-носители, '
    + 'имя параметра',
  'records.filterKind': 'Вид',
  'records.filterZone': 'Зона',
  'records.filterClass': 'Класс',
  'records.filterStatus': 'Статус',
  'records.filterState': 'Исполнитель',
  'records.filterStateUndeclared': 'не объявлено',
  'records.filterDrafts': 'только с черновиками',
  'records.filterRequests': 'только с заявками',
  'records.colId': 'Имя',
  'records.colTitle': 'Заголовок',
  'records.colClass': 'Класс',
  'records.colKind': 'Вид',
  'records.colZone': 'Зона',
  'records.colNature': 'Природа',
  'records.colViolationUnit': 'Единица нарушения',
  'records.colParams': 'Параметров',
  'records.colStand': 'Стенд',
  'records.colExecutors': 'Исполнители',
  'records.colDraft': 'Черновик',
  'records.colRequest': 'Заявка',
  'records.new': 'Новая запись',
  'records.assemble': 'Собрать черновик',
  'records.validate': 'Проверить каталог',
  'records.filterDropped': 'значение фильтра «{value}» вне словаря '
    + '{dict} — фильтр снят',
  'records.emptyWords': 'ничего не найдено по «{q}»; фильтры: {filters}',
  'records.emptyNoQuery': 'ничего не найдено; фильтры: {filters}',
  'records.newTitle': 'Новая запись каталога',
  'records.newSection': 'Раздел',
  'records.newId': 'Имя записи',
  'records.newIdHint': 'латиницей, по образцу ^[a-z][a-z0-9_]{2,40}$',
  'records.newRecordTitle': 'Заголовок',
  'records.newCreate': 'Завести черновик',
  'records.draftDict': 'словарь',
  'records.stand': 'прошло {passed} · сбоев {failed} · пропущено {skipped}',
  'records.standEmpty': 'задач нет',
  'records.standTasksTip': 'число стендовых задач записи; исходы стенда '
    + '(прошло/сбой/пропущено) — в карточке записи и на экране '
    + '«Исполнители по продуктам»',
  'records.validateOk': 'Каталог годен: ошибок 0, предупреждений {warnings}',
  'records.validateBad': 'Проверка каталога: ошибок {errors}, '
    + 'предупреждений {warnings}',

  // ── Карточка записи ─────────────────────────────────────────────
  'card.groupHeader': 'Шапка',
  'card.groupReadsHistory': 'Чтение истории',
  'card.groupSubject': 'Предмет',
  'card.groupParams': 'Параметры',
  'card.groupParamSets': 'Наборы параметров',
  'card.groupConditions': 'Условия',
  'card.groupRuleForm': 'Форма правила',
  'card.groupSemantics': 'Семантика',
  'card.groupInstanceForm': 'Форма экземпляра',
  'card.groupExecutors': 'Исполнители',
  'card.groupTemplates': 'Шаблоны проверок',
  'card.groupStand': 'Стендовые задачи',
  'card.groupAcceptance': 'Приёмка',
  'card.groupOrigin': 'Происхождение',
  'card.groupHistory': 'История',
  'card.groupNote': 'Заметка',
  'card.fieldId': 'Имя (id)',
  'card.fieldClass': 'Класс',
  'card.fieldTitle': 'Заголовок',
  'card.fieldKind': 'Вид',
  'card.fieldZone': 'Зона',
  'card.fieldNature': 'Природа',
  'card.fieldNatureConfirmedBy': 'Свидетель природы',
  'card.fieldViolationUnit': 'Единица нарушения',
  'card.fieldViolationSize': 'Размер нарушения',
  'card.fieldReadsHistoryKind': 'Вид чтения истории',
  'card.fieldReadsHistoryNeed': 'Что нужно из истории',
  'card.fieldStatus': 'Статус',
  'card.fieldSubject': 'Предмет',
  'card.fieldSubjectWords': 'Слова-носители',
  'card.fieldComplements': 'Дополнения',
  'card.fieldNeeds': 'Что требуется',
  'card.fieldFormula': 'Формула',
  'card.fieldRatesForm': 'Форма ставок',
  'card.fieldRefusal': 'Отказ словами',
  'card.fieldText': 'Смысл словами',
  'card.fieldScope': 'Область',
  'card.fieldUnit': 'Единица',
  'card.fieldMeasure': 'Мера',
  'card.fieldAcceptance': 'Приёмка',
  'card.fieldOriginKind': 'Вид происхождения',
  'card.fieldOriginDate': 'Дата',
  'card.fieldOriginNote': 'Слова источника',
  'card.fieldOriginConfirmedBy': 'Подтвердил',
  'card.subjectWordsHint': 'слова-носители: строчными буквами, каждое с '
    + 'новой строки',
  'card.complementsHint': 'дополнение симметрично: сборка допишет вторую '
    + 'сторону',
  'card.acceptanceHint': 'пусто — действует закон по умолчанию из словаря '
    + 'acceptance_default',
  'card.natureConfirmedByHint': 'кого закон паритета спрашивает о природе '
    + 'семьи: solver_tables — таблицы решателя (природа history), '
    + 'certifier_registry — перечни сертификатора (люди, параметр мира), '
    + 'change_request — вердикт судьи заявки, none — свидетеля нет '
    + '(природа по умолчанию, расписание)',
  'card.violationSizeHint': 'чем растёт штраф: count — числом единиц '
    + 'нарушения, magnitude — величиной превышения; задан ровно у мягких '
    + 'семей (вид SOFT)',
  'card.readsHistoryHint': 'что семья читает из истории сотрудника до '
    + 'горизонта. Вид не задан — семья историю не читает; задан — рядом '
    + 'обязательны слова потребности: по ним CDPM задаёт вопрос анкеты, а '
    + 'COSMOS пишет допущение «история не передана»',
  'card.paramName': 'Имя',
  'card.paramUnit': 'Единица',
  'card.paramRelation': 'Отношение',
  'card.paramQualifier': 'Уточнитель',
  'card.paramNote': 'Заметка',
  'card.paramAdd': 'Добавить параметр',
  'card.paramRemove': 'Снять параметр',
  'card.paramsEmpty': 'параметров нет',
  'card.unbound': 'Взять имя из unbound_params',
  'card.unboundHint': 'имя уйдёт из unbound_params при сборке (правило D03)',
  'card.executorKinds': 'Виды исполнителей',
  'card.computableBy': 'Чем вычисляется',
  'card.executorsLive': 'Состояния по продуктам',
  'card.executorsLink': 'в таблице исполнителей',
  'card.templatesHint': 'шаблоны проверок — из словаря check_templates',
  'card.standEmptyWords': 'запись без стендовых задач — предупреждение '
    + 'X07 до появления задач',
  'card.standTask': 'Задача',
  'card.standWorld': 'Мир',
  'card.standExpected': 'Ожидания',
  'card.standNote': 'Заметка',
  'card.historyEmpty': 'история пуста',
  'card.historyDate': 'Дата',
  'card.historyKind': 'Что',
  'card.historyBy': 'Кто',
  'card.historyNote': 'Словами',
  'card.historyRef': 'Ссылка',
  'card.remove': 'Снять запись',
  'card.removeTitle': 'Снять запись из каталога',
  'card.removeWords': 'Запись получит статус «снята» — это старшая '
    + 'версия. Имя останется разрешимым для истории; из порядка семей, '
    + 'лексикона и дополнений других записей запись уберёт сборка.',
  'card.draftFrom': 'Черновик от {date} ({by}): {note}',
  'card.assembleHint': 'Правки попадут в каталог кнопкой «Собрать '
    + 'черновик» на экране «Версии и выпуски»',
  'card.shown': 'Показана {version} · {fingerprint}',
  'card.requestOpen': 'По записи открыта заявка {id}',
  'card.saved': 'Черновик сохранён: изменений {n}',
  'card.discarded': 'Черновик отменён',
  'card.derived': 'Сборка поддержит производные: {list}',
  'card.jsonInvalid': 'не разбирается как JSON: {words}',
  'card.jsonHint': 'структурное поле: JSON по схеме записи',
  'card.readOnlyRecord': 'запись показана только для чтения',
  'card.notFound': 'Записи {section}/{id} в каталоге нет',
  'card.newRecord': 'новая запись: в каталоге её ещё нет — появится '
    + 'сборкой черновика',
  'card.pinnedOpen': 'открыть версию {version}',

  // ── Экран «Словари» ─────────────────────────────────────────────
  'dicts.title': 'Словари',
  'dicts.count': 'ключей {n}',
  'dicts.colKey': 'Ключ',
  'dicts.colKind': 'Вид',
  'dicts.colSize': 'Размер',
  'dicts.colUsages': 'Использований',
  'dicts.colDraft': 'Черновик',
  'dicts.wordsTitle': 'Код — слово',
  'dicts.colCode': 'Код',
  'dicts.colWord': 'Слово',
  'dicts.wordsHint': 'словарь-карта: членство каталога судится по кодам '
    + '(латиница), значение — слово единицы; второй карты слов у продуктов '
    + 'нет — слово берётся отсюда',
  'dicts.kindList': 'перечень',
  'dicts.kindObject': 'объект',
  'dicts.kindText': 'текст',
  'dicts.kindOrder': 'порядок',
  'dicts.derived': 'вычисляется читателем, не правится: {keys}',
  'dicts.closed': 'служебный перечень: изменение — правка кода читателя',
  'dicts.closedShort': 'служебный',
  'dicts.addValue': 'Добавить значение',
  'dicts.newValue': 'новое значение',
  'dicts.removeValue': 'Убрать значение',
  'dicts.removeHint': 'убрать используемое значение — старшая версия; '
    + 'служба назовёт адреса, где значение живёт',
  'dicts.removedValue': 'снято: {value}',
  'dicts.removedLevel': 'старшая версия',
  'dicts.removedHint': 'значение снято относительно каталога — правка '
    + 'старшего уровня; вернуть можно кнопкой',
  'dicts.restoreValue': 'Вернуть значение',
  'dicts.usages': 'Использования',
  'dicts.usagesEmpty': 'использований в записях нет',
  'dicts.journal': 'Журнал ключа',
  'dicts.journalEmpty': 'записей журнала по ключу нет',
  'dicts.orderHint': 'порядок лексикона: состав держит сборка — только '
    + 'семьи «есть» со словами-носителями',
  'dicts.up': 'Выше',
  'dicts.down': 'Ниже',
  'dicts.saved': 'Черновик словаря {key} сохранён',
  'dicts.notFound': 'Ключа {key} в словарях нет',
  'dicts.objectHint': 'объект словаря правится целиком: JSON по схеме '
    + 'читателя; таблица подключей по схеме — работа шага 5б',
  'dicts.textHint': 'текстовое значение словаря',

  // ── Экран «Заявки» ──────────────────────────────────────────────
  'requests.title': 'Заявки',
  'requests.new': 'Подать заявку',
  'requests.newTitle': 'Новая заявка в каталог',
  'requests.colId': 'Заявка',
  'requests.colState': 'Состояние',
  'requests.colRecord': 'Раздел и запись',
  'requests.colSubmittedBy': 'Кто подал',
  'requests.colSource': 'Откуда',
  'requests.colSubmittedAt': 'Подана',
  'requests.colDecision': 'Решение',
  'requests.colCrKey': 'Ключ CR',
  'requests.newRecord': 'новая запись',
  'requests.filterState': 'Состояние',
  'requests.filterSubmitter': 'Кто подал',
  'requests.filterSource': 'Происхождение',
  'requests.filterSection': 'Раздел',
  'requests.filterRecord': 'Запись',
  'requests.searchPlaceholder': 'Номер заявки, запись, ссылка, слова '
    + 'решения',
  'requests.accept': 'Принять',
  'requests.reject': 'Отклонить',
  'requests.decisionTitle': 'Решение владельца по заявке {id}',
  'requests.decisionWords': 'Слова решения',
  'requests.decisionWordsHint': 'решение владельца без слов не '
    + 'записывается',
  'requests.crKey': 'Заявить исполнителя',
  'requests.crKeyTitle': 'Ключ CR исполнителя',
  'requests.crKeyField': 'Ключ CR',
  'requests.crKeyHint': 'образец cr и восемь шестнадцатеричных цифр, '
    + 'например cr1a2b3c4d',
  'requests.adopt': 'Перевести в записи',
  'requests.adoptTitle': 'Перевод заявки {id} в записи',
  'requests.adoptWords': 'Слова подтверждения владельца',
  'requests.adoptHint': 'пишется только черновик записи; в каталог '
    + 'заявка перейдёт сборкой — той же записью',
  'requests.adoptDone': 'Черновик записи создан; соберите черновик — '
    + 'заявка перейдёт в «есть» той же записью',
  'requests.submitted': 'Заявка {id} подана: решение — за владельцем',
  'requests.decided': 'Заявка {id}: {state}',
  'requests.acceptedWords': 'Заявка принята: следующий шаг — исполнитель '
    + '(CR COSMOS с ключом записи)',
  'requests.sourceKind': 'Происхождение',
  'requests.documentClass': 'Класс документа',
  'requests.runClass': 'Класс прогона',
  'requests.ref': 'Ссылка',
  'requests.refHint': 'без имени авиакомпании и реквизитов документа: '
    + 'только класс документа или прогона',
  'requests.draftRecord': 'Черновик записи',
  'requests.kindsRequested': 'Заявленные виды исполнителей',
  'requests.forKinds': 'заявка на виды исполнителя: {kinds}',
  'requests.submit': 'Подать заявку',
  'requests.draftProblems': 'Замечания к черновику заявки',
  'requests.hasDraft': 'черновик записи уже создан',
  'requests.notFound': 'Заявки {id} нет',
  'requests.section': 'Раздел',
  'requests.recordId': 'Имя записи',
  'requests.kindOfRequest': 'Что заявляется',
  'requests.kindRecord': 'запись целиком',
  'requests.kindExecutors': 'виды исполнителей у существующей записи',

  // ── Шаг 6: конвейер пополнения (машинный канал, CR, кандидат) ───
  'requests.channelOff': 'машинный канал заявок выключен',
  'requests.channelOffHint': 'продукты подают заявки только при заданном '
    + 'CATALOG_MACHINE_TOKEN на рабочем месте владельца; на площадках '
    + 'заявки копятся в очереди продукта и переносятся копией папок',
  'requests.filterCrKey': 'Ключ CR',
  'requests.filterRef': 'Прогон',
  'requests.filterRefHint': 'имя прогона до знака «#» в ссылке заявки',
  'requests.colCandidate': 'Кандидат',
  'requests.candidateHere': 'кандидат есть',
  'requests.crStatusHint': 'ключ CR считается законом читателя от номера '
    + 'заявки; статус самого CR виден в COSMOS, а не здесь',
  'requests.crKeyExpectedHint': 'ключ по закону — исполнитель ещё не '
    + 'заявлен',
  'requests.beforeReleaseTitle': 'До выпуска версии',
  'requests.beforeReleaseCounts': 'заявок в работе: {work}; кандидатов в '
    + 'дереве: {candidates}',
  'requests.beforeCr': 'CR с ключом записи доведён в COSMOS до состояния '
    + '«реализован»',
  'requests.beforeMerge': 'ветка cr-… слита в main рабочего места',
  'requests.beforeAdopt': 'заявка переведена в записи с кандидатом '
    + '(стендовые задачи приходят из него)',
  'requests.beforeAssemble': 'черновик собран: файл кандидата удалён, '
    + 'удаление вошло в коммит',
  'requests.beforeChain': 'пройдена цепочка перегенерации: стенд, '
    + 'генератор привязок, гейт привязок',
  'requests.beforeSeed': 'адрес записи убран из pending_records посева '
    + 'привязок',
  'requests.beforeRelease': 'выпущена младшая версия; предпроверки 9 и 10 '
    + 'зелены',
  'requests.dryRun': 'Проверить',
  'requests.dryRunHint': 'спросить службу до подачи: номер по закону, ключ '
    + 'CR и замечания — без единой записи на диск',
  'requests.previewNumber': 'Номер по закону',
  'requests.previewRecordExists': 'Запись с таким именем уже есть: законна '
    + 'только заявка на вид исполнителя — форма переключена',
  'requests.previewKnown': 'Такая заявка уже подана: {id} (состояние '
    + '«{state}»); повторная подача заведёт не новую заявку, а ту же',
  'requests.previewOpen': 'По этой записи уже открыта заявка {id} '
    + '(состояние «{state}») — подача будет отклонена',
  'requests.previewClean': 'Замечаний нет: заявка пройдёт правила каталога',
  'requests.revise': 'Пересмотреть',
  'requests.reviseHint': 'пересмотр решения словами владельца — '
    + 'единственный выход для того же пробела: номер у него содержательный',
  'requests.rejectHint': 'отклонить можно и принятую заявку — решение '
    + 'владельца перезаписывается, прежнее остаётся в журнале',
  'requests.candidateGates': 'ветка {branch}; стенд {passed}/{failed}/'
    + '{skipped}; тесты {tests}',
  'requests.useCandidate': 'взять запись и стендовые задачи из кандидата',
  'requests.useCandidateHint': 'схема запрещает стендовые задачи у '
    + 'черновика заявки: у новой записи это единственный путь принести их '
    + 'в каталог; сборка удалит файл кандидата',

  // ── Экран «Исполнители по продуктам» ────────────────────────────
  'executors.title': 'Исполнители по продуктам',
  'executors.readOnly': 'экран только для чтения: строки приходят из '
    + 'файлов привязок продуктов, правит их продукт',
  'executors.fileFound': 'файл найден',
  'executors.fileMissing': 'файла нет',
  'executors.productVersion': 'версия продукта',
  'executors.catalogVersion': 'каталог привязок',
  'executors.generatedAt': 'собраны',
  'executors.generatedFrom': 'код',
  'executors.freshMatch': 'сошлось',
  'executors.freshStale': 'черновик отстал',
  'executors.freshStop': 'остановка сторожа: перегенерируйте',
  'executors.freshMissing': 'не объявлены',
  'executors.colRecord': 'Запись',
  'executors.colStand': 'Стенд',
  'executors.filterProduct': 'Продукт',
  'executors.filterKind': 'Вид',
  'executors.filterState': 'Состояние',
  'executors.filterSection': 'Раздел',
  'executors.totals': 'клеток {cells}: «есть» {present}, «заявлен» '
    + '{claimed}, «нет» {absent}, не объявлено {undeclared}',
  'executors.cellUndeclared': 'вид никем не объявлен',
  'executors.cellUndeclaredTip': 'вид вне зоны обоих продуктов',
  'executors.cellTipRow': '{kind} · {symbol} · {file}',
  'executors.rowLang': 'Язык',
  'executors.rowFile': 'Файл',
  'executors.rowSymbol': 'Символ',
  'executors.rowNote': 'Заметка',
  'executors.rowCrKey': 'Ключ CR',
  'executors.problemsCount': 'Замечания привязок: {n}',
  'executors.commonProblemsCount': 'Общие замечания привязок: {n}',
  'executors.commonProblemsHint': 'полнота по видам (B04) судится зонами '
    + 'всех файлов привязок сразу, поэтому эти замечания — не о файле '
    + 'одного продукта, а о записи',
  'executors.standTask': 'Задача',
  'executors.standKind': 'Вид',
  'executors.standResult': 'Исход',
  'executors.standAt': 'Когда',
  'executors.standCommit': 'Код',
  'executors.standNote': 'Словами',
  'executors.standEmpty': 'строк стенда у продукта нет',
  'executors.standProduct': 'Продукт',
  'executors.standExpand': 'Раскрыть строки стенда',
  'executors.standCollapse': 'Свернуть строки стенда',
  'executors.standCounts': '{result}: {n}',
  'executors.expand': 'Раскрыть клетку',
  'executors.collapse': 'Свернуть клетку',

  // ── Экран «Версии и выпуски» ────────────────────────────────────
  'versions.title': 'Версии и выпуски',
  'versions.current': 'Текущая шапка каталога',
  'versions.gates': 'Флаги',
  'versions.gateStand': 'стендовые задачи обязательны',
  'versions.gateWords': 'слова-носители обязательны',
  'versions.draftsBlock': 'Черновики службы: {n}',
  'versions.draftsEmpty': 'черновиков нет',
  'versions.assemble': 'Собрать черновик',
  'versions.discardAll': 'Отменить все',
  'versions.discardAllTitle': 'Отменить все черновики',
  'versions.discardAllDone': 'Черновиков отменено: {n}',
  'versions.discardAllWords': 'Черновики будут удалены; каталог не '
    + 'менялся и не изменится.',
  'versions.assembleTitle': 'Сборка черновика каталога',
  'versions.assembleVersion': 'Версия',
  'versions.assembleNote': 'Заметка сборки',
  'versions.assembleStop': 'Продолжить осознанно: привязки продукта '
    + 'остановятся сторожем',
  'versions.assembleDone': 'Собрано {version}: отпечаток {fingerprint}, '
    + 'изменений {applied}',
  'versions.next': 'Что дальше',
  'versions.release': 'Выпустить версию',
  'versions.releaseTitle': 'Выпуск версии каталога',
  'versions.releaseChecklist': 'Предпроверки выпуска',
  'versions.releaseDone': 'Выпущена {version}: отпечаток {fingerprint}, '
    + 'папка {dir}',
  'versions.checkDiff': 'изменений с прошлого выпуска',
  'versions.releaseServiceChecks': 'перечень считает служба сухим '
    + 'прогоном — на диск не ложится ничего; при нажатии те же '
    + 'проверки повторяются, и любая красная — отказ словами',
  'versions.importPicked': 'выбран файл {file}',
  'versions.draftsOf': '{section} · {id} · {kind} · {by} · {at}',
  'versions.compare': 'Сравнить версии',
  'versions.compareA': 'Слева',
  'versions.compareB': 'Справа',
  'versions.compareLevel': 'Уровень: {level}',
  'versions.levelMajor': 'старшая',
  'versions.levelMinor': 'младшая',
  'versions.levelPatch': 'патч',
  'versions.levelSame': 'без изменений',
  'versions.optionCurrent': 'текущая',
  'versions.optionDrafts': 'черновики',
  'versions.export': 'Выгрузить одним файлом',
  'versions.import': 'Загрузить одним файлом',
  'versions.importTitle': 'Замена каталога файлом',
  'versions.importDrop': 'перетащите файл каталога сюда или выберите '
    + 'кнопкой',
  'versions.importPick': 'Выбрать файл',
  'versions.importConfirm': 'Заменить каталог {from} содержимым файла '
    + '{file}? Черновиков быть не должно.',
  'versions.importForce': 'Понизить версию осознанно',
  'versions.importDone': 'Каталог заменён: {version} · {fingerprint}',
  'versions.colVersion': 'Версия',
  'versions.colDate': 'Дата',
  'versions.colBy': 'Кем',
  'versions.colFingerprint': 'Отпечаток',
  'versions.colCode': 'Код',
  'versions.colChanges': 'Изменений',
  'versions.colDir': 'Папка выпуска',
  'versions.dirOk': 'есть',
  'versions.dirMissing': 'нет',
  'versions.dirMismatch': 'отпечаток разошёлся',
  'versions.journalOfVersions': 'Журнал версий',
  'versions.journalTitle': 'Журнал действий службы',
  'versions.journalEmpty': 'журнал ведётся только на рабочем месте',
  'versions.journalAt': 'Когда',
  'versions.journalActor': 'Кто',
  'versions.journalOp': 'Действие',
  'versions.journalTarget': 'Над чем',
  'versions.journalOutcome': 'Исход',
  'versions.journalWords': 'Словами',
  'versions.suggested': 'изменений с выпуска {base}: {n} '
    + '(старших {major}, младших {minor}, патчей {patch}) → {version}',
  'versions.suggestedNoBase': 'выпусков ещё нет; подсказка версии '
    + '{version}, изменений {n} (старших {major}, младших {minor}, '
    + 'патчей {patch})',
  'versions.empty': 'журнал версий пуст: выпусков службой ещё не было',
  'versions.detail': 'Выпуск {version}',
  'versions.detailCurrent': 'Текущая версия {version}',
  'versions.currentChip': 'текущая версия каталога',
  'versions.noEntry': 'записи журнала версий у этой версии ещё нет: '
    + 'выпуска не было',
  'versions.loadable': 'папка выпуска читается читателем',
  'versions.notLoadable': 'папка выпуска не читается',
  'versions.notFound': 'Версии {version} нет',
  'versions.assembleStopAhead': 'после сборки дверь продукта остановится '
    + 'сторожем — предупреждение службы до нажатия',

  // ── Служебные таблицы ───────────────────────────────────────────
  'changes.colSection': 'Раздел',
  'changes.colId': 'Запись',
  'changes.colKind': 'Что',
  'changes.colNote': 'Словами',
  'changes.colLevel': 'Уровень',
  'changes.empty': 'изменений нет',
  'problems.colWhere': 'Адрес',
  'problems.colCode': 'Код',
  'problems.colWords': 'Словами',
  'problems.empty': 'замечаний нет',
  'problems.counts': 'ошибок {errors}, предупреждений {warnings}',
  'problems.severityError': 'ошибка',
  'problems.severityWarning': 'предупреждение',
} as const
