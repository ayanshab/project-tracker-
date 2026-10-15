/* eslint-disable react-refresh/only-export-components */
// Pickers used inside the "new project" and "new milestone" forms, so a manager can
// staff the work in the same step as creating it instead of a second trip to another tab.
import { useEffect, useState } from 'react'
import { api } from '../services/api'
import { fmtHours, fmtShort } from '../utils/format'

/**
 * Live capacity check for one person, shown before anything is saved.
 * Asks the server what their busiest week looks like between from and until, with `hours` added.
 * projectId excludes the assignment being edited so it isn't counted twice.
 */
export function CapacityNote({ userId, hours, from, until, projectId, fallbackFrom, fallbackUntil }) {
  const [result, setResult] = useState(null) // {key, data | error}
  const h = Number(hours) || 0
  const start = from || fallbackFrom || ''
  const end = until || fallbackUntil || ''
  const key = `${userId}|${h}|${start}|${end}|${projectId || ''}`
  const active = Boolean(userId) && h > 0
  useEffect(() => {
    if (!active) return undefined
    let live = true
    const t = setTimeout(() => {
      api.get('/projects/capacity', { user_id: userId, hours: h, from: start || undefined, to: end || undefined, project_id: projectId || undefined })
        .then((d) => live && setResult({ key, data: d }))
        .catch((e) => live && setResult({ key, error: e.message }))
    }, 350)
    return () => { live = false; clearTimeout(t) }
  }, [active, key, userId, h, start, end, projectId])

  if (!active) return null
  const state = result && result.key === key ? (result.error ? { error: result.error } : result.data) : { loading: true }
  if (!state) return null
  if (state.error) return <div className="cap-note muted" role="status">Couldn't check capacity: {state.error}</div>
  if (state.loading) return <div className="cap-note muted" role="status">Checking capacity…</div>
  const { status, booked_peak: peak, with_new_peak: total, over_by: over, worst_week: worst, weekly_cap: cap, weeks_over } = state
  const period = `${fmtShort(state.from)} to ${fmtShort(state.to)}`
  if (status === 'over') {
    return (
      <div className="cap-note over" role="alert">
        Over by {fmtHours(over)} a week: already booked {fmtHours(peak)} in the week of {fmtShort(worst)}
        {weeks_over > 1 ? ` and ${weeks_over - 1} other week${weeks_over > 2 ? 's' : ''}` : ''}.
        {' '}Reduce to {fmtHours(Math.max(cap - peak, 0))} or change the dates. Saving will be refused.
      </div>
    )
  }
  if (status === 'near') {
    return <div className="cap-note near" role="status">Tight: this takes them to {fmtHours(total)} of {cap} in their busiest week ({period}). {fmtHours(cap - total)} would be left.</div>
  }
  return <div className="cap-note ok" role="status">Fits: {fmtHours(total)} of {cap} in their busiest week ({period}){peak ? `, ${fmtHours(peak)} of it on other projects` : ''}.</div>
}

/**
 * Choose people and weekly hours for a new project.
 * people: [{id, name, job_title, hours_this_week}]; value: {userId: hours}; onChange(nextValue)
 */
export function TeamPicker({ people, value, onChange, loading, later = true, minDate, maxDate, projectId }) {
  const toggle = (p) => {
    const next = { ...value }
    if (p.id in next) delete next[p.id]
    else next[p.id] = { hours: Math.max(Math.min(8, 40 - (p.hours_this_week || 0)), 0.5), from: '', until: '' }
    onChange(next)
  }
  const set = (id, patch) => onChange({ ...value, [id]: { ...value[id], ...patch } })
  const chosen = Object.keys(value).length

  return (
    <fieldset className="picker field-wide">
      <legend className="field-label">{later ? 'Team' : 'People'} <span className="muted">({later ? 'optional, ' : ''}{chosen} chosen)</span></legend>
      {loading ? <p className="muted small">Loading people…</p> : people.length ? (
        <ul className="pick-list">
          {people.map((p) => {
            const on = p.id in value
            const free = Math.max(40 - (p.hours_this_week || 0), 0)
            return (
              <li key={p.id} className={on ? 'on' : ''}>
                <label className="pick-main">
                  <input type="checkbox" checked={on} onChange={() => toggle(p)} />
                  <span>
                    {p.name}
                    <span className="muted small"> {p.job_title || p.role}</span>
                  </span>
                </label>
                <span className={`small ${free <= 0 ? 'text-bad' : 'muted'}`}>{fmtHours(free)} free / week</span>
                {on && (
                  <div className="pick-detail">
                    <label className="pick-hours">
                      <input type="number" min="0.5" max="40" step="0.5" value={value[p.id].hours} onChange={(e) => set(p.id, { hours: e.target.value })} aria-label={`Hours per week for ${p.name}`} />
                      <span className="muted small">h / week</span>
                    </label>
                    <label className="pick-date">
                      <span className="muted small">From</span>
                      <input type="date" min={minDate} max={maxDate} value={value[p.id].from} onChange={(e) => set(p.id, { from: e.target.value })} aria-label={`Start date for ${p.name}`} />
                    </label>
                    <label className="pick-date">
                      <span className="muted small">Until</span>
                      <input type="date" min={value[p.id].from || minDate} max={maxDate} value={value[p.id].until} onChange={(e) => set(p.id, { until: e.target.value })} aria-label={`End date for ${p.name}`} />
                    </label>
                    <CapacityNote userId={p.id} hours={value[p.id].hours} from={value[p.id].from} until={value[p.id].until} projectId={projectId} fallbackFrom={minDate} fallbackUntil={maxDate} />
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      ) : <p className="muted small">No one is available to add yet.</p>}
      <span className="field-hint">Free hours are as of this week. Nobody can go over 40 hours a week across projects, and the note under each chosen person checks their busiest week in the period before you save. Leave From empty to start today (or at the project start), and Until empty to stay to the end.{later && ' You can change the team later on the Team tab.'}</span>
    </fieldset>
  )
}

/**
 * Choose people for a new milestone from the project team.
 * team: project.members; busy: {userId: 'milestone name'} for people already on another open milestone
 */
export function MilestonePeoplePicker({ team, busy, value, onChange }) {
  const toggle = (id) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id])
  return (
    <fieldset className="picker field-wide">
      <legend className="field-label">People <span className="muted">(optional, {value.length} chosen)</span></legend>
      {team.length ? (
        <ul className="pick-list">
          {team.map((x) => {
            const elsewhere = busy[x.user_id]
            return (
              <li key={x.user_id} className={value.includes(x.user_id) ? 'on' : ''}>
                <label className="pick-main">
                  <input type="checkbox" checked={value.includes(x.user_id)} disabled={Boolean(elsewhere)} onChange={() => toggle(x.user_id)} />
                  <span>{x.name}<span className="muted small"> {x.job_title}</span></span>
                </label>
                {elsewhere && <span className="muted small">On "{elsewhere}"</span>}
              </li>
            )
          })}
        </ul>
      ) : <p className="muted small">No one is on the project team yet. Add people on the Team tab first.</p>}
      <span className="field-hint">Each person can have one open milestone per project.</span>
    </fieldset>
  )
}

/** Editable list of starting tasks: rows of {title, assigned_to}. */
export function TaskRows({ rows, onChange, assignees }) {
  const update = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const remove = (i) => onChange(rows.filter((_, j) => j !== i))
  const add = () => onChange([...rows, { title: '', assigned_to: '' }])
  return (
    <fieldset className="picker field-wide">
      <legend className="field-label">Tasks <span className="muted">(optional)</span></legend>
      <div className="task-rows">
        {rows.map((r, i) => (
          <div key={i} className="task-row">
            <input
              value={r.title}
              placeholder={`Task ${i + 1}`}
              maxLength={200}
              aria-label={`Task ${i + 1} title`}
              onChange={(e) => update(i, { title: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); if (r.title.trim()) add() }
              }}
            />
            <select value={r.assigned_to} onChange={(e) => update(i, { assigned_to: e.target.value })} aria-label={`Task ${i + 1} assignee`}>
              <option value="">Anyone on the milestone</option>
              {assignees.map((a) => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}
            </select>
            <button type="button" className="icon-btn" aria-label={`Remove task ${i + 1}`} onClick={() => remove(i)}>×</button>
          </div>
        ))}
      </div>
      <button type="button" className="link-btn add-row" onClick={add}>+ Add a task</button>
      <span className="field-hint">Press Enter in a task to start the next one.</span>
    </fieldset>
  )
}

/** Turn the picker's value into API assignments. */
export function toAssignments(value) {
  return Object.entries(value).map(([user_id, v]) => ({
    user_id,
    hours_per_week: Number(v.hours),
    ...(v.from ? { assigned_from: v.from } : {}),
    ...(v.until ? { assigned_to: v.until } : {}),
  }))
}
