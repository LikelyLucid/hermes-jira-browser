import assert from 'node:assert/strict'
import {
  activeState,
  chooseTicketContext,
  eventSessionIdentity,
  eventState,
  eventStateForSession,
  markNotified,
  normaliseLiveState,
  notificationTransition,
  rememberEventState,
  shouldNotify,
  ticketStateMap
} from './live_status_core.mjs'

assert.equal(normaliseLiveState({}, { status: 'working' }), 'working')
assert.equal(normaliseLiveState({}, { status: 'waiting' }), 'waiting')
assert.equal(normaliseLiveState({}, { status: 'starting' }), 'starting')
assert.equal(normaliseLiveState({}, { status: 'failed' }), 'failed')
assert.equal(normaliseLiveState({ archived: true }, { status: 'working' }), 'archived')
assert.equal(normaliseLiveState({}, { status: 'waiting', busy: true }), 'waiting')
assert.equal(activeState('waiting'), true)
assert.equal(activeState('idle'), false)

const context = chooseTicketContext(
  { selectedKey: 'ABC-1', links: [{ session_id: 'stored-1' }, { session_id: 'stored-2' }] },
  'stored-2'
)
assert.equal(context.focusedLink.session_id, 'stored-2')
assert.equal(chooseTicketContext({ links: [] }, 'stored-2').focused, false)

const waiting = notificationTransition('working', 'waiting', 'turn-1')
assert.deepEqual(waiting, { kind: 'needs-input', key: 'needs-input:turn-1' })
const completed = notificationTransition('working', 'idle', 'turn-1')
assert.deepEqual(completed, { kind: 'completed', key: 'completed:turn-1' })
assert.equal(notificationTransition('waiting', 'working', 'turn-1'), null)
assert.equal(notificationTransition('failed', 'idle', 'turn-1'), null)
let receipts = {}
assert.equal(shouldNotify(receipts, completed), true)
receipts = markNotified(receipts, completed)
assert.equal(shouldNotify(receipts, completed), false)

assert.deepEqual(eventSessionIdentity({ type: 'message.complete', payload: { session_id: 'runtime-1', session_key: 'stored-1' } }), {
  runtimeId: 'runtime-1', storedId: 'stored-1', type: 'message.complete'
})
assert.equal(eventState({ type: 'message.complete', payload: { session_key: 'stored-1' } }), 'idle')
assert.equal(eventState({ type: 'clarify.request', payload: { session_key: 'stored-1' } }), 'waiting')
assert.equal(eventState({ type: 'turn.error', payload: { session_key: 'stored-1' } }), 'failed')

const owner = { connectionId: 'gateway-a', profile: 'default', targetProfile: 'default' }
let eventStates = new Map()
eventStates = rememberEventState(eventStates, {
  type: 'turn.error',
  payload: { session_key: 'stored-1', session_id: 'runtime-1' },
  owner
})
assert.equal(eventStateForSession(eventStates, owner, { session_key: 'stored-1', id: 'runtime-1', status: 'idle' }), 'failed')
assert.equal(eventStateForSession(eventStates, owner, { session_key: 'stored-1', id: 'runtime-1', status: 'working' }), 'failed')
assert.equal(eventStateForSession(eventStates, { connectionId: 'gateway-b', profile: 'default', targetProfile: 'default' }, { session_key: 'stored-1' }), '')

assert.deepEqual(ticketStateMap([
  { ticketKey: 'ABC-1', state: 'idle' },
  { ticketKey: 'ABC-1', state: 'failed' },
  { ticketKey: 'ABC-2', state: 'archived' }
]), { 'ABC-1': 'failed', 'ABC-2': 'archived' })
