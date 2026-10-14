import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { Button, ErrorNote } from '../components/ui'

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
      <div className="login-art" aria-hidden="true">
        <svg viewBox="0 0 400 120" preserveAspectRatio="xMidYMid meet">
          <line x1="20" y1="60" x2="380" y2="60" className="art-track" />
          <line x1="20" y1="60" x2="200" y2="60" className="art-done" />
          {[20, 110, 200, 290, 380].map((x, i) => (
            <circle key={x} cx={x} cy="60" r="11" className={i < 2 ? 'art-stop done' : i === 2 ? 'art-stop now' : 'art-stop'} />
          ))}
        </svg>
        <p>Every project, one stop at a time.</p>
      </div>
      <form className="login-card" onSubmit={submit}>
        <h1>Sign in</h1>
        <p className="page-sub">Use the username and password your administrator gave you.</p>
        <label className="field">
          <span className="field-label">Username</span>
          <input name="username" autoComplete="username" required autoFocus />
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
