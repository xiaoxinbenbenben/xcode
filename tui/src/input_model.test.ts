import test from 'node:test';
import assert from 'node:assert/strict';

import {applyInputKey, getVisibleInputLines, type InputState} from './input_model.js';

test('Esc + Enter 会插入换行而不是直接提交', () => {
	const initial: InputState = {text: 'hello', pendingEscape: false};
	const escaped = applyInputKey(initial, {
		inputChunk: '',
		key: {escape: true},
	});
	assert.equal(escaped.kind, 'update');
	const withEscapeState = escaped.state;

	const next = applyInputKey(withEscapeState, {
		inputChunk: '',
		key: {return: true},
	});
	assert.equal(next.kind, 'update');
	assert.equal(next.state.text, 'hello\n');
	assert.equal(next.state.pendingEscape, false);
});

test('普通 Enter 会提交当前输入', () => {
	const initial: InputState = {text: 'hello', pendingEscape: false};
	const next = applyInputKey(initial, {
		inputChunk: '',
		key: {return: true},
	});
	assert.equal(next.kind, 'submit');
	assert.equal(next.submittedText, 'hello');
	assert.equal(next.state.text, '');
});

test('输入区只展示最后几行', () => {
	const lines = getVisibleInputLines('1\n2\n3\n4\n5\n6\n7');
	assert.deepEqual(lines, ['2', '3', '4', '5', '6', '7']);
});
