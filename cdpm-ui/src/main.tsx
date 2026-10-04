import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import './index.css'

// Файл, уроненный мимо зоны загрузки (на холст, на блок без зоны, в
// панель), браузер по умолчанию ОТКРЫВАЕТ вместо панели — вкладка
// уходит с интерфейса (ревью 06.09). Зоны загрузки обрабатывают свои
// события сами и останавливают всплытие; всё остальное гасится здесь.
window.addEventListener('dragover', e => e.preventDefault())
window.addEventListener('drop', e => e.preventDefault())

// HashRouter: интерфейс раздаётся FastAPI как статика по /ui/ —
// маршруты в хеше не требуют серверного отката на index.html.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
)
