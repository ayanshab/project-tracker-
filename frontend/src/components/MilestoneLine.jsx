import { fmtShort } from '../utils/format'

/**
 * Milestones drawn as stations on a line, because they run in strict order:
 * a station can only be reached after the one before it.
 *   filled  = completed, ringed = in progress, hollow = not started.
 * `compact` renders a small inline version for list rows.
 */
export default function MilestoneLine({ milestones = [], compact = false, onSelect, selectedId }) {
  if (!milestones.length) {
    return compact ? <span className="line-none">No milestones yet</span> : null
  }
  const doneCount = milestones.filter((m) => m.status === 'completed').length
  const reached = milestones.length > 1 ? (Math.max(doneCount - 0.5, 0) / (milestones.length - 1)) * 100 : 0

  if (compact) {
    return (
      <div className="mline mline-compact" aria-label={`${doneCount} of ${milestones.length} milestones completed`}>
        <div className="mline-track"><div className="mline-reached" style={{ width: `${Math.min(reached, 100)}%` }} /></div>
        {milestones.map((m, i) => (
          <span
            key={m.id}
            className={`mstop mstop-${m.status} ${m.overdue ? 'mstop-late' : ''}`}
            style={{ left: milestones.length > 1 ? `${(i / (milestones.length - 1)) * 100}%` : '0%' }}
            title={`#${m.position} ${m.name}`}
          />
        ))}
      </div>
    )
  }

  return (
    <ol className="mline mline-full" aria-label="Milestone sequence">
      {milestones.map((m, i) => {
        const late = m.status !== 'completed' && m.overdue
        return (
          <li key={m.id} className={`mstation ${selectedId === m.id ? 'is-selected' : ''}`}>
            {i > 0 && <span className={`mlink ${milestones[i - 1].status === 'completed' ? 'mlink-done' : ''}`} aria-hidden="true" />}
            <button type="button" className="mstation-btn" onClick={() => onSelect?.(m)}>
              <span className={`mstop mstop-${m.status} ${late ? 'mstop-late' : ''}`} aria-hidden="true" />
              <span className="mstation-name">{m.name}</span>
              <span className="mstation-dates">{fmtShort(m.start_date)} to {fmtShort(m.end_date)}</span>
              <span className="sr-only">
                {m.status === 'completed' ? 'Completed' : m.status === 'in_progress' ? 'In progress' : 'Not started'}
                {late ? ', overdue' : ''}
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}
