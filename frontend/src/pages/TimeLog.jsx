import { useState } from 'react'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { Button, Empty, ErrorNote, Field, Loading, PageHeader, Panel, formToObject, useAction, useApi } from '../components/ui'
import { fmtHours, fmtShort, fmtWeekday, todayISO } from '../utils/format'

export default function TimeLog() {
  const { user } = useAuth()
  const act = useAction()
  const [projectId, setProjectId] = useState('')
  const [busy, setBusy] = useState(false)
  const { data, error, loading, reload } = useApi(async () => {
    const [week, entries] = await Promise.all([api.get('/budgets/time/week'), api.get('/budgets/time', { user_id: user.id })])
    return { week, entries: entries.entries }
  }, [])
  const project = projectId || data?.week.assignments[0]?.project_id || ''
  const milestones = useApi(() => (project ? api.get('/milestones', { project_id: project }).then((d) => d.milestones) : Promise.resolve([])), [project])

  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  const { week } = data
  const mine = data.entries

  const submit = async (e) => {
    e.preventDefault()
    const form = e.currentTarget
    setBusy(true)
    const ok = await act(() => api.post('/budgets/time', formToObject(new FormData(form), ['hours'])), 'Time logged')
    setBusy(false)
    if (ok) { form.reset(); reload() }
  }

  return (
    <>
      <PageHeader title="My time" subtitle="Log the hours you work on each project. You can log up to 8 hours a day and 40 hours a week in total." />
      <div className="grid-2">
        <Panel title="Log time">
          {week.assignments.length ? (
            <form className="form" onSubmit={submit}>
              <div className="form-grid">
                <Field label="Project" wide>
                  <select name="project_id" value={project} onChange={(e) => setProjectId(e.target.value)} required>
                    {week.assignments.map((a) => <option key={a.project_id} value={a.project_id}>{a.project_name} ({a.hours_per_week} h/week planned)</option>)}
                  </select>
                </Field>
                <Field label="Milestone" wide hint="Optional. Only milestones that have started.">
                  <select name="milestone_id" defaultValue="" key={project}>
                    <option value="">No specific milestone</option>
                    {(milestones.data || []).filter((m) => m.status !== 'pending').map((m) => <option key={m.id} value={m.id}>{m.position}. {m.name}</option>)}
                  </select>
                </Field>
                <Field label="Date"><input name="work_date" type="date" max={todayISO()} defaultValue={todayISO()} required /></Field>
                <Field label="Hours"><input name="hours" type="number" min="0.25" max="8" step="0.25" required /></Field>
                <Field label="Note" wide><input name="note" maxLength={500} placeholder="What you worked on" /></Field>
              </div>
              <div className="form-actions"><Button type="submit" busy={busy}>Log time</Button></div>
            </form>
          ) : (
            <Empty title="No projects to log time on">You can log time once your manager adds you to a project.</Empty>
          )}
        </Panel>

        <Panel title={`Week of ${fmtShort(week.week_start)}`}>
          <div className="week-bars">
            {week.days.map((d) => (
              <div key={d.date} className="week-day">
                <div className="week-bar"><div className={`week-fill ${d.hours >= 8 ? 'full' : ''}`} style={{ height: `${(d.hours / 8) * 100}%` }} /></div>
                <span className="week-h num">{d.hours || ''}</span>
                <span className="week-label">{fmtWeekday(d.date)}</span>
              </div>
            ))}
          </div>
          <p className="muted">{fmtHours(week.total_hours)} logged, {fmtHours(week.remaining_hours)} left this week.</p>
        </Panel>
      </div>

      <Panel title="Recent entries">
        {mine.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Date</th><th>Project</th><th>Milestone</th><th>Note</th><th className="r">Hours</th><th /></tr></thead>
              <tbody>
                {mine.slice(0, 50).map((t) => (
                  <tr key={t.id}>
                    <td>{fmtShort(t.work_date)}</td><td>{t.project_name}</td><td>{t.milestone_name || '–'}</td><td>{t.note || '–'}</td>
                    <td className="r num">{t.hours}</td>
                    <td className="r"><button className="link-btn danger" onClick={() => act(() => api.del(`/budgets/time/${t.id}`), 'Entry deleted').then(reload)}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty title="No time logged yet" />}
      </Panel>
    </>
  )
}
