const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const dateFmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
const shortDate = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
const weekday = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'UTC' })

export const fmtMoney = (v) => (v === null || v === undefined ? '–' : money.format(Number(v)))
export const fmtHours = (v) => `${Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })} h`
export const fmtDate = (v) => (v ? dateFmt.format(new Date(String(v).slice(0, 10))) : '–')
export const fmtShort = (v) => (v ? shortDate.format(new Date(String(v).slice(0, 10))) : '–')
export const fmtWeekday = (v) => weekday.format(new Date(String(v).slice(0, 10)))
export const fmtPct = (v) => (v === null || v === undefined ? '–' : `${v}%`)

export function todayISO() {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export const ROLE_LABEL = { admin: 'Admin', manager: 'Manager', employee: 'Employee' }

export const BUDGET_STATUS = {
  on_track: { label: 'On track', tone: 'good' },
  at_risk: { label: 'Spending fast', tone: 'warn' },
  over_budget: { label: 'Over budget', tone: 'bad' },
  no_budget: { label: 'No budget set', tone: 'muted' },
}

export const MILESTONE_STATUS = {
  pending: { label: 'Not started', tone: 'muted' },
  in_progress: { label: 'In progress', tone: 'info' },
  completed: { label: 'Completed', tone: 'good' },
}

export function initials(name = '') {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join('')
}
