/** @type {import('tailwindcss').Config} */
// Визуальный язык CodeForge (общий с панелью CDPM): цвета темы — CSS-
// переменные --cf-*, проброшенные как tailwind-цвета cf-*; фирменные
// цвета одинаковы в обеих темах. Правило: на тематических поверхностях
// только text-cf-text / text-cf-text-muted, никаких text-white/gray-*.
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'cf': {
          'bg': 'var(--cf-bg)',
          'panel': 'var(--cf-panel)',
          'border': 'var(--cf-border)',
          'primary': '#4f46e5',
          'secondary': '#8b5cf6',
          'success': '#10b981',
          'warning': '#f59e0b',
          'error': '#ef4444',
          'text': 'var(--cf-text)',
          'text-muted': 'var(--cf-text-muted)',
          'input': 'var(--cf-input-bg)',
          'hover': 'var(--cf-hover)',
          'code': 'var(--cf-code-bg)',
        },
      },
      fontFamily: {
        // Дисциплина контура: без внешних загрузок шрифтов — системный
        // моноширинный стек.
        'mono': ['ui-monospace', 'Consolas', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
