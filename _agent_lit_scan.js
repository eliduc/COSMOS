export const meta = {
  name: 'pairing-lit-scan',
  description: 'Скан свежих публикаций по crew pairing/rostering, оценка применимости к нашему арсеналу, шорт-лист находок',
  phases: [
    { title: 'Search', detail: 'параллельный поиск по 6 углам' },
    { title: 'Evaluate', detail: 'оценка каждого кандидата против арсенала/пробелов' },
    { title: 'Synthesize', detail: 'ранжирование, запись находок' },
  ],
}

const WATCHLIST = 'C:/work/Sandbox/Testing/TimeFold/crew-demo/final-report/agent-watchlist.md'
const FINDINGS = 'C:/work/Sandbox/Testing/TimeFold/crew-demo/final-report/agent-findings.md'

// window: prefer recent work; args may pass {since: 'YYYY-MM'} else default last ~18 months
const since = (args && args.since) || '2024-06'

const CAND = {
  type: 'object',
  properties: {
    papers: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          authors: { type: 'string' },
          year: { type: 'string' },
          venue: { type: 'string', description: 'журнал/конференция/arXiv id' },
          url: { type: 'string' },
          gist: { type: 'string', description: 'одна фраза: какой метод/идея' },
        },
        required: ['title', 'gist'],
      },
    },
  },
  required: ['papers'],
}

const VERDICT = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    novel: { type: 'boolean', description: 'НЕТ в списке уже разобранных §4 watchlist' },
    gap: { type: 'string', description: 'какой пробел §2 закрывает, или none' },
    implementable_oss: { type: 'boolean', description: 'реализуемо на Timefold+OR-Tools+SCIP/HiGHS без обязательного CPLEX/Gurobi' },
    what_new: { type: 'string', description: 'чего нет в нашем арсенале' },
    verdict: { type: 'string', enum: ['REPORT', 'context', 'not-applicable'] },
    reason: { type: 'string' },
    url: { type: 'string' },
  },
  required: ['title', 'novel', 'verdict', 'reason'],
}

phase('Search')
const ANGLES = [
  { k: 'arxiv-recent', q: `arXiv и препринты с ${since}: airline crew pairing / crew rostering column generation, branch-and-price, набор новых алгоритмов оптимизации. Верни конкретные статьи с ссылками.` },
  { k: 'journals', q: `Свежие (${since}+) статьи в Transportation Science, EJOR, Computers & Operations Research, INFORMS Journal on Computing, OR Spectrum по crew pairing / crew rostering optimization. Конкретные статьи.` },
  { k: 'ml-crew', q: `Machine learning / reinforcement learning для airline crew scheduling, ускорение column generation предсказанием (flight-connection prediction, learned pricing, GNN), с ${since}. Конкретные работы.` },
  { k: 'bnp-accel', q: `Ускорение branch-and-price / column generation: dynamic constraint aggregation, dual stabilization, Ryan-Foster branching, primal heuristics для set partitioning, с ${since}. Конкретные работы, применимые к airline crew.` },
  { k: 'rules-integrated', q: `Интегрированная оптимизация airline crew pairing + rostering, pay-and-credit / preferential bidding (PBS) / seniority моделирование, fatigue-aware и robust crew scheduling, с ${since}.` },
  { k: 'recovery-industry', q: `Airline crew recovery / disruption management алгоритмы и любые индустриальные (Jeppesen, Sabre, Lufthansa Systems, GENCOL/AD OPT) публикации с воспроизводимыми методами, с ${since}.` },
]

const found = await parallel(ANGLES.map(a => () =>
  agent(`Ты — научный сканер по airline crew scheduling. ${a.q}
Используй веб-поиск (WebSearch/WebFetch/tavily через ToolSearch). Верни РЕАЛЬНЫЕ существующие публикации с проверяемыми ссылками; не выдумывай. До 8 самых релевантных.`,
    { label: `search:${a.k}`, phase: 'Search', schema: CAND })))

// dedupe by lowercased title
const seen = new Set()
const cands = []
for (const r of found.filter(Boolean)) {
  for (const p of (r.papers || [])) {
    const key = (p.title || '').toLowerCase().replace(/\s+/g, ' ').trim()
    if (key && !seen.has(key)) { seen.add(key); cands.push(p) }
  }
}
log(`собрано ${cands.length} уникальных кандидатов`)

phase('Evaluate')
const verdicts = await parallel(cands.slice(0, 40).map(p => () =>
  agent(`Оцени применимость публикации к нашему стенду. Сначала ПРОЧИТАЙ файл ${WATCHLIST} (наш арсенал §1, пробелы §2, планка §3, УЖЕ РАЗОБРАННОЕ §4).
ПУБЛИКАЦИЯ: ${JSON.stringify(p)}
Определи: novel (нет в §4?), какой пробел §2 закрывает, реализуемо ли на открытом стеке (без обязательного CPLEX/Gurobi/Xpress), чего нет в нашем арсенале. Вердикт REPORT только если ВСЕ три условия §3 выполнены. Иначе context (полезно как фон) или not-applicable. Будь строг — маркетинг и уже-сделанное это не REPORT. Если нужно, проверь суть статьи веб-поиском.`,
    { label: `eval:${(p.title || '').slice(0, 30)}`, phase: 'Evaluate', schema: VERDICT })))

phase('Synthesize')
const good = verdicts.filter(Boolean)
const reportable = good.filter(v => v.verdict === 'REPORT' && v.novel)
const context = good.filter(v => v.verdict === 'context')

const summary = await agent(`Ты — научный редактор. Дан список оценённых публикаций по crew pairing/rostering.
REPORT-кандидаты (новые, закрывают пробел, реализуемы): ${JSON.stringify(reportable)}
Контекст (фон): ${JSON.stringify(context.slice(0, 15))}

Прочитай ${WATCHLIST} для актуального контекста. Затем:
1) Составь краткую сводку находок на русском: для каждого REPORT-кандидата — что нового, какой наш пробел закрывает, как ориентировочно внедрить на нашем стеке, ожидаемый эффект. Если REPORT-кандидатов нет — честно скажи «принципиально новых применимых подходов не найдено» и перечисли 2-3 самых заметных context-работы одной строкой.
2) Допиши журнал §5 в ${WATCHLIST}: по одной записи на каждый REPORT и заметный context (формат из комментария в §5), и добавь новые REPORT/context статьи в таблицу §4, чтобы в следующий раз не повторяться.
3) Перезапиши файл ${FINDINGS}: заголовок с датой запуска, число просмотренных кандидатов, сводку REPORT-находок (или «новых нет»), и пометку, что для внедрения нужно подтверждение пользователя. Пиши деловым языком.
Верни короткий текст: сколько REPORT-находок и их названия (или «новых нет»).`,
  { label: 'synthesize', phase: 'Synthesize', effort: 'high' })

return {
  candidates_scanned: cands.length,
  reportable: reportable.length,
  reportable_titles: reportable.map(v => v.title),
  summary,
}
