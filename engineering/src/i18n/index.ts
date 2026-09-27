import { createI18n, type Composer } from 'vue-i18n'
import zhCN from '@/locales/zh-CN'
import en from '@/locales/en'

const SUPPORTED_LOCALES = ['zh-CN', 'en'] as const
type SupportedLocale = typeof SUPPORTED_LOCALES[number]

function getInitialLocale(): SupportedLocale {
  const saved = localStorage.getItem('app_locale')
  if (saved && (SUPPORTED_LOCALES as readonly string[]).includes(saved)) {
    return saved as SupportedLocale
  }
  const browser = navigator.language
  if (browser.startsWith('zh')) return 'zh-CN'
  return 'en'
}

const initialLocale = getInitialLocale()

const i18n = createI18n({
  legacy: false,
  locale: initialLocale,
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': zhCN,
    en: en,
  },
})

function setLocale(locale: SupportedLocale) {
  const composer = i18n.global as Composer
  composer.locale.value = locale
  localStorage.setItem('app_locale', locale)
}

export { i18n, setLocale, SUPPORTED_LOCALES }
export type { SupportedLocale }
