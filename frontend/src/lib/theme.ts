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
  scrim_color?: string
}

// Returns a `style` attribute value overriding --color-primary/-hover and
// --color-scrim, or '' (no override — app.css's defaults apply) when the
// event hasn't set anything.
export function themeStyle(theme: EventThemeInfo | null | undefined): string {
  const parts: string[] = []
  const color = theme?.primary_color
  if (color) parts.push(`--color-primary:${color};--color-primary-hover:${darken(color)};`)
  const scrim = theme?.scrim_color
  if (scrim) parts.push(`--color-scrim:${scrim};`)
  return parts.join('')
}

// WCAG relative luminance → readable text color for a filled button/chip of
// this background color, instead of asking the admin to pick one by hand.
export function pickContrastColor(hex: string): string {
  const clean = hex.replace('#', '')
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean
  const num = Number.parseInt(full, 16)
  if (Number.isNaN(num) || full.length !== 6) return '#ffffff'
  const srgb = [16, 8, 0].map((shift) => ((num >> shift) & 0xff) / 255)
  const linear = srgb.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  const luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
  return luminance > 0.45 ? '#17130e' : '#ffffff'
}
