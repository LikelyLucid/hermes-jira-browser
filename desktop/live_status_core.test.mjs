import assert from 'node:assert/strict'
import {
  activeState,
  chooseTicketContext,
  eventSessionIdentity,
  eventState,
  markNotified,
  normaliseLiveState,
  notificationTransition,
  shouldNotify
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
let receipts = {}
assert.equal(shouldNotify(receipts, completed), true)
receipts = markNotified(receipts, completed)
assert.equal(shouldNotify(receipts, completed), false)

assert.deepEqual(eventSessionIdentity({ type: 'message.complete', payload: { session_id: 'runtime-1', session_key: 'stored-1' } }), {
  runtimeId: 'runtime-1', storedId: 'stored-1', type: 'message.complete'
})
assert.equal(eventState({ type: 'message.complete', payload: { session_key: 'stored-1' } }), 'idle')
assert.equal(eventState({ type: 'clarify.request', payload: { session_key: 'stored-1' } }), 'waiting')
