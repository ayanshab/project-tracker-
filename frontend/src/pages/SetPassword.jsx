import { useState } from 'react'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { Button, ErrorNote } from '../components/ui'

/** Shown instead of the app while a user is still on a temporary password. */
export default function SetPassword() {
  const { user, refresh, logout } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    setError(null)
    if (fd.get('new_password') !== fd.get('confirm')) {
      setError(new Error('The new passwords do not match.'))
      return
    }
    setBusy(true)
    try {
      await api.post('/auth/change-password', { current_password: fd.get('current_password'), new_password: fd.get('new_password') })
      await refresh()
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <div className="login login-single">
      <form className="login-card" onSubmit={submit}>
        <h1>Choose your password</h1>
        <p className="page-sub">
          Welcome, {user.name.split(' ')[0]}. You signed in with a temporary password, so pick your own before continuing.
        </p>
        <label className="field">
          <span className="field-label">Temporary password</span>
          <input name="current_password" type="password" autoComplete="current-password" required autoFocus />
        </label>
        <label className="field">
          <span className="field-label">New password</span>
          <input name="new_password" type="password" autoComplete="new-password" minLength={8} required />
          <span className="field-hint">At least 8 characters, different from the temporary one.</span>
        </label>
        <label className="field">
          <span className="field-label">Repeat new password</span>
          <input name="confirm" type="password" autoComplete="new-password" minLength={8} required />
        </label>
        <ErrorNote error={error} />
        <Button type="submit" busy={busy} className="btn-block">Save password and continue</Button>
        <button type="button" className="link-btn" onClick={logout}>Sign out</button>
      </form>
    </div>
  )
}
