import test from 'node:test';
import assert from 'node:assert/strict';

type TimelineItem = {
	id: string;
	kind: 'user' | 'assistant' | 'activity';
	text: string;
};

function appendAssistantDelta(
	timeline: TimelineItem[],
	assistantId: string,
	delta: string,
): TimelineItem[] {
	return timeline.map(item => {
		if (item.id !== assistantId || item.kind !== 'assistant') {
			return item;
		}
		return {
			...item,
			text: `${item.text}${delta}`,
		};
	});
}

function appendAssistantSegment(
	timeline: TimelineItem[],
	assistantId: string,
	text: string,
): TimelineItem[] {
	return [
		...timeline,
		{id: assistantId, kind: 'assistant', text},
	];
}

test('没有事件打断时，assistant 增量继续写回同一个消息段', () => {
	const timeline: TimelineItem[] = [
		{id: 'u1', kind: 'user', text: 'hello'},
		{id: 'a1', kind: 'assistant', text: '前半句'},
	];

	const next = appendAssistantDelta(timeline, 'a1', '，后半句');
	assert.equal(next[1]?.text, '前半句，后半句');
});

test('工具事件打断后，后续 assistant 文本应新开一条消息段', () => {
	const timeline: TimelineItem[] = [
		{id: 'u1', kind: 'user', text: 'hello'},
		{id: 'a1', kind: 'assistant', text: '先思考一下'},
		{id: 'e1', kind: 'activity', text: '[Tool] Read'},
		{id: 'e2', kind: 'activity', text: '[ToolResult] done'},
	];

	const next = appendAssistantSegment(timeline, 'a2', '最终回答');
	assert.equal(next[1]?.text, '先思考一下');
		assert.equal(next[2]?.text, '[Tool] Read');
		assert.equal(next[3]?.text, '[ToolResult] done');
	assert.equal(next[4]?.text, '最终回答');
});
