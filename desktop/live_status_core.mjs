export const LIVE_STATES = Object.freeze(['working', 'waiting', 'starting', 'idle', 'failed', 'archived'])

const ACTIVE_STATES = new Set(['working', 'waiting', 'starting'])
const WAITING_VALUES = new Set(['needs-input', 'needs_input', 'waiting', 'waiting-for-input', 'waiting_for_input', 'awaiting-input', 'awaiting_input', 'awaiting_response', 'approval'])
const WORKING_VALUES = new Set(['working', 'running', 'busy', 'streaming'])
const STARTING_VALUES = new Set(['starting', 'queued', 'connecting', 'initializing', 'resuming'])
const FAILED_VALUES = new Set(['failed', 'error', 'errored', 'interrupted', 'cancelled', 'canceled'])

function text(value) {
  return String(value ?? '').trim().toLowerCase()
}

export function normaliseLiveState(link = {}, active = null, focused = {}) {
  if (link.archived || active?.archived || focused.archived) return 'archived'
  if (link.available === false || active?.failed === true || focused.failed === true) return 'failed'

  const values = [active?.status, focused.status, active?.state, focused.state].map(text).filter(Boolean)
  if (values.some(value => WAITING_VALUES.has(value))) return 'waiting'
  if (values.some(value => FAILED_VALUES.has(value))) return 'failed'
  if (values.some(value => STARTING_VALUES.has(value))) return 'starting'
  if (values.some(value => WORKING_VALUES.has(value))) return 'working'
  if (active?.busy === true || focused.busy === true) return 'working'
  if (active?.awaitingResponse === true || focused.awaitingResponse === true) return 'waiting'
  return 'idle'
}

export function activeState(state) {
  return ACTIVE_STATES.has(state)
}

export function statusLabel(state) {
  return {
    archived: 'Archived',
    failed: 'Failed',
    idle: 'Idle',
    starting: 'Starting',
    waiting: 'Needs input',
    working: 'Working'
  }[state] || 'Idle'
}

export function statusPriority(state) {
  return { failed: 5, waiting: 4, working: 3, starting: 2, idle: 1, archived: 0 }[state] ?? 0
}

export function chooseTicketContext(context = {}, focusedStoredSessionId = '') {
  const links = Array.isArray(context.links) ? context.links : []
  const focused = String(focusedStoredSessionId || '').trim()
  const focusedLink = focused ? links.find(link => String(link?.session_id || '') === focused) : null
  if (focusedLink) return { ...context, focusedLink, focused: true }
  return { ...context, focusedLink: null, focused: false }
}

export function notificationTransition(previous, next, epoch = '') {
  if (next === 'waiting' && previous !== 'waiting') return { kind: 'needs-input', key: `needs-input:${epoch}` }
  if (next === 'idle' && ACTIVE_STATES.has(previous)) return { kind: 'completed', key: `completed:${epoch}` }
  return null
}

export function shouldNotify(receipts = {}, transition) {
  if (!transition) return false
  return receipts[transition.key] !== true
}

export function markNotified(receipts = {}, transition, limit = 100) {
  if (!transition?.key) return receipts
  const next = { ...receipts, [transition.key]: true }
  const keys = Object.keys(next)
  if (keys.length <= limit) return next
  return Object.fromEntries(keys.slice(-limit).map(key => [key, true]))
}

export function eventSessionIdentity(event = {}) {
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : event
  return {
    runtimeId: String(payload?.session_id || payload?.id || event?.session_id || '').trim(),
    storedId: String(payload?.session_key || payload?.stored_session_id || event?.session_key || '').trim(),
    type: text(event?.type || event?.event || payload?.type)
  }
}

export function eventState(event = {}) {
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : event
  const value = text(payload?.status || payload?.state || event?.status || event?.state)
  if (value === 'completed' || value === 'complete' || value === 'done' || value === 'finished') return 'idle'
  if (WAITING_VALUES.has(value)) return 'waiting'
  if (FAILED_VALUES.has(value)) return 'failed'
  if (STARTING_VALUES.has(value)) return 'starting'
  if (WORKING_VALUES.has(value)) return 'working'
  if (/complete|finished|done/.test(text(event?.type || event?.event))) return 'idle'
  if (/need|wait|approval|clarify|input/.test(text(event?.type || event?.event))) return 'waiting'
  if (/start|queue|connect|resume/.test(text(event?.type || event?.event))) return 'starting'
  if (/fail|error|cancel|interrupt/.test(text(event?.type || event?.event))) return 'failed'
  if (/message\.delta|message\.start|thinking|reasoning|tool/.test(text(event?.type || event?.event))) return 'working'
  return ''
}
