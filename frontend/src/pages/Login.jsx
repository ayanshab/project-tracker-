import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { Button, ErrorNote } from '../components/ui'
import { Brand } from '../components/Layout'

export default function Login() {
  const { login } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    setBusy(true)
    setError(null)
    try {
      await login(String(fd.get('username')).trim(), String(fd.get('password')))
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login-art">
        <Brand />
        <div className="login-art-body">
          <h2>Every project, every hour, accounted for.</h2>
          <p>Plan milestones, staff teams within a 40-hour week, and track spend against budget, with every change approved and on the record.</p>
          <svg viewBox="0 0 400 60" preserveAspectRatio="xMinYMid meet" aria-hidden="true">
            <line x1="12" y1="30" x2="388" y2="30" className="art-track" />
            <line x1="12" y1="30" x2="200" y2="30" className="art-done" />
            {[12, 106, 200, 294, 388].map((x, i) => (
              <circle key={x} cx={x} cy="30" r="9" className={i < 2 ? 'art-stop done' : i === 2 ? 'art-stop now' : 'art-stop'} />
            ))}
          </svg>
        </div>
        <p className="login-foot">Access is limited to authorized ACME staff. Sign-ins and changes are recorded.</p>
      </div>
      <form className="login-card" onSubmit={submit}>
        <h1>Sign in</h1>
        <p className="page-sub">Use the username and password your administrator gave you.</p>
        <label className="field">
          <span className="field-label">Username</span>
          <input name="username" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} required autoFocus />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <ErrorNote error={error} />
        <Button type="submit" busy={busy} className="btn-block">Sign in</Button>
      </form>
    </div>
  )
}
