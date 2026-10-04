/** Initialises, persists, and exposes the active colour theme. */
import * as React from 'react'

export type Theme = 'light' | 'dark'
const STORAGE_KEY = 'phlo-theme'

/** Runs before paint (inlined in <head>) so the page never flashes the wrong theme. */
export const themeInitScript = `(function(){try{var t=localStorage.getItem('${STORAGE_KEY}');if(t!=='light'&&t!=='dark'){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.classList.toggle('dark',t==='dark')}catch(e){}})()`

const ThemeContext = React.createContext<{
  theme: Theme
  setTheme: (t: Theme) => void
}>({
  theme: 'light',
  setTheme: () => {},
})

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>('light')

  React.useEffect(() => {
    setThemeState(
      document.documentElement.classList.contains('dark') ? 'dark' : 'light',
    )
    const onStorage = (e: StorageEvent) => {
      if (
        e.key === STORAGE_KEY &&
        (e.newValue === 'light' || e.newValue === 'dark')
      )
        apply(e.newValue)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const apply = (t: Theme) => {
    document.documentElement.classList.toggle('dark', t === 'dark')
    setThemeState(t)
  }

  const setTheme = React.useCallback((t: Theme) => {
    apply(t)
    try {
      localStorage.setItem(STORAGE_KEY, t)
    } catch {
      /* private mode */
    }
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => React.useContext(ThemeContext)
