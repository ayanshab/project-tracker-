import { useState } from 'react'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { Avatar, Button, ErrorNote, Field, PageHeader, Panel, Tag, useToast } from '../components/ui'
import { ROLE_LABEL } from '../utils/format'

function useSubmit(fn) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    const form = e.currentTarget
    setBusy(true)
    setError(null)
    try {
      await fn(new FormData(form), form)
    } catch (err) {
      setError(err)
    }
    setBusy(false)
  }
  return { busy, error, submit }
}

export default function Profile() {
  const { user, refresh, logout } = useAuth()
  const toast = useToast()

  const details = useSubmit(async (fd) => {
    await api.patch(`/users/${user.id}`, { name: fd.get('name'), email: fd.get('email') })
    await refresh()
    toast('Profile saved')
  })
  const password = useSubmit(async (fd, form) => {
    if (fd.get('new_password') !== fd.get('confirm')) throw new Error('The new passwords do not match.')
    await api.post('/auth/change-password', { current_password: fd.get('current_password'), new_password: fd.get('new_password') })
    form.reset()
    toast('Password changed')
  })

  return (
    <>
      <PageHeader title="Profile" actions={<Button variant="ghost" onClick={logout}>Sign out</Button>} />
      <Panel>
        <div className="person person-lg">
          <Avatar name={user.name} size={56} />
          <div>
            <div className="pcard-name">{user.name}</div>
            <div className="row-tags"><Tag tone="info">{ROLE_LABEL[user.role]}</Tag><span className="muted">{user.job_title}</span></div>
            <div className="muted small">Employee ID {user.employee_number}, username {user.username}</div>
          </div>
        </div>
      </Panel>
      <div className="grid-2">
        <Panel title="Your details">
          <form className="form" onSubmit={details.submit}>
            <div className="form-grid">
              <Field label="Full name" wide><input name="name" required defaultValue={user.name} /></Field>
              <Field label="Email" wide><input name="email" type="email" required defaultValue={user.email} /></Field>
            </div>
            <ErrorNote error={details.error} />
            <div className="form-actions"><Button type="submit" busy={details.busy}>Save details</Button></div>
            <p className="muted small">Your job title, role, and manager are set by an admin.</p>
          </form>
        </Panel>
        <Panel title="Change password">
          <form className="form" onSubmit={password.submit}>
            <div className="form-grid">
              <Field label="Current password" wide><input name="current_password" type="password" autoComplete="current-password" required /></Field>
              <Field label="New password" hint="At least 8 characters"><input name="new_password" type="password" minLength={8} autoComplete="new-password" required /></Field>
              <Field label="Repeat new password"><input name="confirm" type="password" minLength={8} autoComplete="new-password" required /></Field>
            </div>
            <ErrorNote error={password.error} />
            <div className="form-actions"><Button type="submit" busy={password.busy}>Change password</Button></div>
          </form>
        </Panel>
      </div>
    </>
  )
}
