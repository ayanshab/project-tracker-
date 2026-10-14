import { Empty, Panel, Tag } from './ui'
import { fmtDate, fmtHours, fmtMoney } from '../utils/format'

const HEALTH = {
  good: ['On track', 'good'],
  fair: ['Slipping', 'warn'],
  poor: ['Off track', 'bad'],
}

function Index({ label, value, health, explain }) {
  const [text, tone] = HEALTH[health] || ['Not enough data', 'muted']
  return (
    <div className="fc-index">
      <div className="fc-index-top">
        <span className="fc-index-value num">{value ?? '–'}</span>
        <Tag tone={tone}>{text}</Tag>
      </div>
      <div className="fc-index-label">{label}</div>
      <p className="muted small">{explain}</p>
    </div>
  )
}

/** Planned vs earned progress on one bar: where the work should be today, and where it is. */
function ProgressCompare({ planned, earned }) {
  return (
    <div className="fc-compare" aria-label={`${earned}% of the work done, ${planned}% planned by today`}>
      <div className="fc-track">
        <div className="fc-earned" style={{ width: `${Math.min(earned, 100)}%` }} />
        <div className="fc-planned" style={{ left: `${Math.min(planned, 100)}%` }}><span>Planned today {planned}%</span></div>
      </div>
      <div className="legend"><span><i className="sw sw-labor" /> Done {earned}%</span></div>
    </div>
  )
}

export default function Forecast({ project }) {
  const f = project.forecast
  if (!f || !f.available) {
    return <Panel title="Forecast"><Empty title="No forecast yet">{f?.reason || 'Add milestones to get a forecast.'}</Empty></Panel>
  }
  const showCost = f.cpi !== undefined
  const late = f.days_late
  const budget = Number(project.planned_budget || 0)

  return (
    <>
      <Panel title="Where the work stands">
        <ProgressCompare planned={f.planned_pct} earned={f.earned_pct} />
        <p className="muted small">
          Done counts completed milestones fully, and milestones in progress by the share of tasks checked off
          (or half, if they have no tasks). Planned comes from each milestone's dates. Confidence: {f.confidence}.
        </p>
      </Panel>

      <div className="grid-2">
        <Panel title="Schedule">
          <Index label="Schedule performance (SPI)" value={f.spi} health={f.schedule_health}
            explain="Work done divided by work planned so far. 1.00 is on schedule; 0.80 means 20% behind." />
          <dl className="fc-facts">
            <dt>Planned finish</dt><dd>{fmtDate(project.end_date)}</dd>
            <dt>Forecast finish</dt>
            <dd className={late > 7 ? 'bad' : ''}>{f.forecast_finish ? fmtDate(f.forecast_finish) : '–'}</dd>
            <dt>Difference</dt>
            <dd className={late > 7 ? 'bad' : late < 0 ? 'good' : ''}>
              {late === null || late === undefined ? '–' : late > 0 ? `${late} days late` : late < 0 ? `${-late} days early` : 'On the date'}
            </dd>
          </dl>
        </Panel>

        {showCost ? (
          <Panel title="Cost">
            <Index label="Cost performance (CPI)" value={f.cpi} health={f.cost_health}
              explain="Value of work done divided by what it cost. Below 1.00 means each dollar buys less work than planned." />
            <dl className="fc-facts">
              <dt>Budget</dt><dd>{budget ? fmtMoney(budget) : 'Not set'}</dd>
              <dt>Forecast final cost</dt><dd className={f.forecast_variance < 0 ? 'bad' : ''}>{fmtMoney(f.forecast_cost)}</dd>
              <dt>Difference</dt>
              <dd className={f.forecast_variance < 0 ? 'bad' : f.forecast_variance > 0 ? 'good' : ''}>
                {f.forecast_variance === null || f.forecast_variance === undefined ? '–'
                  : f.forecast_variance < 0 ? `${fmtMoney(-f.forecast_variance)} over` : `${fmtMoney(f.forecast_variance)} under`}
              </dd>
              <dt>Forecast total hours</dt><dd>{f.forecast_hours ? `${fmtHours(f.forecast_hours)} of ${fmtHours(project.budget_hours)}` : '–'}</dd>
            </dl>
          </Panel>
        ) : (
          <Panel title="Cost"><p className="muted">Cost forecasts are visible to the project manager and admins.</p></Panel>
        )}
      </div>

      <details className="fc-method">
        <summary>How the forecast works</summary>
        <p>
          This is earned value management, the method most project offices use. Planned value is the share of work
          scheduled by today. Earned value is the share actually done. Actual cost is labor (hours times pay rate) plus
          other costs. Forecast final cost is the budget divided by CPI, and forecast finish is the planned duration
          divided by SPI. Forecasts firm up as more work is completed.
        </p>
      </details>
    </>
  )
}
