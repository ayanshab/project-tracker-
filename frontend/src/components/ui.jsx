/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { initials } from '../utils/format'

// ---------- Data loading ----------

export function useApi(loader, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const loaderRef = useRef(loader)
  useLayoutEffect(() => { loaderRef.current = loader })

  const run = useCallback((isCurrent = () => true) => loaderRef.current()
    .then((data) => isCurrent() && setState({ data, error: null, loading: false }))
    .catch((error) => isCurrent() && setState((s) => ({ data: s.data, error, loading: false }))), [])

  const reload = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }))
    return run()
  }, [run])

  useEffect(() => {
    let current = true
    run(() => current)
    return () => { current = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { ...state, reload }
}

// ---------- Toasts ----------

const ToastContext = createContext(() => {})

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const push = useCallback((message, tone = 'good') => {
    const id = Math.random().toString(36).slice(2)
    setToasts((t) => [...t, { id, message, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000)
  }, [])
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>{t.message}</div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)

// ---------- Primitives ----------

export function Button({ variant = 'primary', size, busy, children, className = '', ...props }) {
  return (
    <button
      className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : ''} ${className}`}
      disabled={busy || props.disabled}
      {...props}
    >
      {busy ? 'Working…' : children}
    </button>
  )
}

export function Tag({ tone = 'muted', children, title }) {
  return <span className={`tag tag-${tone}`} title={title}>{children}</span>
}

export function Progress({ value, tone, label }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  const t = tone || (value > 100 ? 'bad' : 'info')
  return (
    <div className="progress" role="progressbar" aria-valuenow={v} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className={`progress-fill fill-${t}`} style={{ width: `${v}%` }} />
    </div>
  )
}

export function Avatar({ name, size = 32 }) {
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.38 }} aria-hidden="true">
      {initials(name)}
    </span>
  )
}

export function Stat({ label, value, sub, tone }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ''}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function Panel({ title, actions, children, className = '' }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <p className="empty-text">{children}</p>}
      {action}
    </div>
  )
}

export function Loading() {
  return <div className="loading" aria-busy="true">Loading…</div>
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="error-note" role="alert">
      <span>{error.message || String(error)}</span>
      {onRetry && <Button variant="ghost" size="sm" onClick={onRetry}>Try again</Button>}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  )
}

// ---------- Forms ----------

export function Field({ label, hint, children, wide }) {
  return (
    <label className={`field ${wide ? 'field-wide' : ''}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function Modal({ title, onClose, children, footer }) {
  const ref = useRef(null)
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    ref.current?.querySelector('input, select, textarea, button')?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  )
}

/** A modal form: handles submit, busy state and API errors. */
export function FormModal({ title, submitLabel, onSubmit, onClose, children }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSubmit(new FormData(e.currentTarget))
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }
  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <div className="form-grid">{children}</div>
        <ErrorNote error={error} />
        <div className="form-actions">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" busy={busy}>{submitLabel}</Button>
        </div>
      </form>
    </Modal>
  )
}

/** Run an API action with toast feedback. Returns true on success. */
export function useAction() {
  const toast = useToast()
  return useCallback(
    async (fn, success) => {
      try {
        await fn()
        if (success) toast(success)
        return true
      } catch (err) {
        toast(err.message, 'bad')
        return false
      }
    },
    [toast],
  )
}

export function formToObject(fd, numeric = []) {
  const out = {}
  for (const [k, v] of fd.entries()) {
    if (v === '') continue
    out[k] = numeric.includes(k) ? Number(v) : v
  }
  return out
}
