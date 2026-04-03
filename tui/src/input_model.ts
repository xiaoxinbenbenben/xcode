export type InputState = {
	text: string;
	pendingEscape: boolean;
};

export type InputKey = {
	inputChunk: string;
	key: {
		ctrl?: boolean;
		return?: boolean;
		backspace?: boolean;
		delete?: boolean;
		tab?: boolean;
		escape?: boolean;
	};
};

export type InputAction =
	| {kind: 'noop'; state: InputState}
	| {kind: 'update'; state: InputState}
	| {kind: 'submit'; state: InputState; submittedText: string}
	| {kind: 'exit'; state: InputState};

export const MAX_VISIBLE_INPUT_LINES = 6;

export function getVisibleInputLines(text: string): string[] {
	// 输入区只保留最后几行可见内容，避免长 prompt 把整个 TUI 顶飞。
	const lines = text.split('\n');
	return lines.slice(-MAX_VISIBLE_INPUT_LINES);
}

export function applyInputKey(state: InputState, event: InputKey): InputAction {
	const {inputChunk, key} = event;

	// Ctrl+C 仍然是整个 TUI 的硬退出键，不参与输入编辑。
	if (key.ctrl && inputChunk === 'c') {
		return {kind: 'exit', state};
	}

	// Esc 本身不插入文本，只标记“下一次 Enter 视为换行”。
	if (key.escape) {
		return {
			kind: 'update',
			state: {
				...state,
				pendingEscape: true,
			},
		};
	}

	// 多行输入只保留 Esc + Enter，避免终端里不稳定的快捷键分支继续污染体验。
	if (key.return && state.pendingEscape) {
		return {
			kind: 'update',
			state: {
				text: `${state.text}\n`,
				pendingEscape: false,
			},
		};
	}

	if (key.return) {
		if (!state.text.trim()) {
			return {
				kind: 'noop',
				state: {
					...state,
					pendingEscape: false,
				},
			};
		}
		return {
			kind: 'submit',
			submittedText: state.text,
			state: {
				text: '',
				pendingEscape: false,
			},
		};
	}

	if (key.backspace || key.delete) {
		return {
			kind: 'update',
			state: {
				text: state.text.slice(0, -1),
				pendingEscape: false,
			},
		};
	}

	if (key.tab) {
		return {
			kind: 'update',
			state: {
				text: `${state.text}\t`,
				pendingEscape: false,
			},
		};
	}

	if (!inputChunk) {
		return {
			kind: 'noop',
			state: {
				...state,
				pendingEscape: false,
			},
		};
	}

	return {
		kind: 'update',
		state: {
			text: `${state.text}${inputChunk}`,
			pendingEscape: false,
		},
	};
}
