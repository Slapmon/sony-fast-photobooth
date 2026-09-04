// Per-event color override (config/event.py's EventTheme). Applied as an
// inline CSS-custom-property override on a page's root element rather than
// baked into app.css, since the same app.css serves every event.

function darken(hex: string, amount = 0.18): string {
  const clean = hex.replace('#', '')
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean
  const num = Number.parseInt(full, 16)
  if (Number.isNaN(num)) return hex
  const channel = (shift: number) => {
    const value = (num >> shift) & 0xff
    return Math.max(0, Math.round(value * (1 - amount)))
      .toString(16)
      .padStart(2, '0')
  }
  return `#${channel(16)}${channel(8)}${channel(0)}`
}

export interface EventThemeInfo {
  primary_color: string
}

// Returns a `style` attribute value overriding --color-primary/-hover, or
// '' (no override — app.css's default palette applies) when the event
// hasn't set a color.
export function themeStyle(theme: EventThemeInfo | null | undefined): string {
  const color = theme?.primary_color
  if (!color) return ''
  return `--color-primary:${color};--color-primary-hover:${darken(color)};`
}
