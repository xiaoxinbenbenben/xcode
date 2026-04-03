import React, {useEffect, useMemo, useState} from 'react';
import {Box, render, Text, useApp, useInput} from 'ink';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

import {applyInputKey, getVisibleInputLines, type InputState} from './input_model.js';

type SessionDescriptor = {
	session_id: string;
	session_name: string;
	workspace_root: string;
};

type RuntimeEvent = {
	type: string;
	payload?: Record<string, unknown>;
};

type TimelineItem = {
	id: string;
	kind: 'user' | 'assistant' | 'activity';
	text: string;
};

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '..', '..');

function parseCliArgs(argv: string[]): {workspaceRoot?: string; sessionId?: string} {
	// TUI 第一版只解析两个最小参数：启动 workspace 和恢复 session。
	const args: {workspaceRoot?: string; sessionId?: string} = {};
	for (let index = 0; index < argv.length; index += 1) {
		const value = argv[index];
		if (value === '--workspace' && argv[index + 1]) {
			args.workspaceRoot = argv[index + 1];
			index += 1;
		}
		if (value === '--session' && argv[index + 1]) {
			args.sessionId = argv[index + 1];
			index += 1;
		}
	}
	return args;
}

function runPythonCli(
	args: string[],
	handlers: {
		onStdoutLine?: (line: string) => void;
		onStderrLine?: (line: string) => void;
	},
): Promise<void> {
	// Python CLI 仍然是真正的执行端；Ink 只负责把 JSONL 事件转成状态。
	return new Promise((resolve, reject) => {
		const child = spawn(
			'uv',
			['run', 'python', path.join(REPO_ROOT, 'scripts/cli.py'), ...args],
			{cwd: REPO_ROOT},
		);
		let stdoutBuffer = '';
		let stderrBuffer = '';

		const flushLines = (
			buffer: string,
			handleLine: ((line: string) => void) | undefined,
		): string => {
			let remaining = buffer;
			while (remaining.includes('\n')) {
				const newlineIndex = remaining.indexOf('\n');
				const line = remaining.slice(0, newlineIndex).trim();
				remaining = remaining.slice(newlineIndex + 1);
				if (line && handleLine) {
					handleLine(line);
				}
			}
			return remaining;
		};

		child.stdout.on('data', (chunk: Buffer | string) => {
			stdoutBuffer += chunk.toString();
			stdoutBuffer = flushLines(stdoutBuffer, handlers.onStdoutLine);
		});

		child.stderr.on('data', (chunk: Buffer | string) => {
			stderrBuffer += chunk.toString();
			stderrBuffer = flushLines(stderrBuffer, handlers.onStderrLine);
		});

		child.on('error', reject);
		child.on('close', code => {
			const trailingStdout = stdoutBuffer.trim();
			if (trailingStdout && handlers.onStdoutLine) {
				handlers.onStdoutLine(trailingStdout);
			}
			const trailingStderr = stderrBuffer.trim();
			if (trailingStderr && handlers.onStderrLine) {
				handlers.onStderrLine(trailingStderr);
			}
			if (code === 0) {
				resolve();
				return;
			}
			reject(new Error(`Python CLI exited with code ${code ?? -1}`));
		});
	});
}

async function bootstrapSession(args: {workspaceRoot?: string; sessionId?: string}): Promise<SessionDescriptor> {
	// 会话初始化单独走一次 CLI，避免 TUI 自己生成 session id。
	const cliArgs = ['--print-session-json'];
	if (args.sessionId) {
		cliArgs.push('--session', args.sessionId);
	} else {
		cliArgs.push('--new-session');
		if (args.workspaceRoot) {
			cliArgs.push('--workspace', args.workspaceRoot);
		}
	}

	let descriptor: SessionDescriptor | null = null;
	await runPythonCli(cliArgs, {
		onStdoutLine: line => {
			descriptor = JSON.parse(line) as SessionDescriptor;
		},
	});
	if (descriptor === null) {
		throw new Error('无法初始化 session。');
	}
	return descriptor;
}

async function streamPrompt(
	sessionId: string,
	prompt: string,
	handlers: {
		onEvent: (event: RuntimeEvent) => void;
		onError: (message: string) => void;
	},
): Promise<void> {
	// 每轮 prompt 都复用同一个 session id，这样 TUI 虽是多次进程调用，状态仍然连续。
	await runPythonCli(['--session', sessionId, '--json-events', prompt], {
		onStdoutLine: line => {
			handlers.onEvent(JSON.parse(line) as RuntimeEvent);
		},
		onStderrLine: line => {
			handlers.onError(line);
		},
	});
}

function summarizeEvent(event: RuntimeEvent): string | null {
	// 右侧活动面板默认只展示摘要，不直接把大段 tool_result 或正文塞进去。
	const payload = event.payload ?? {};
	if (event.type === 'tool_started') {
		return `[Tool] ${String(payload.tool_name ?? 'unknown_tool')}`;
	}
	if (event.type === 'tool_result') {
		return `[ToolResult] ${String(payload.tool_name ?? 'unknown_tool')} ${String(payload.summary ?? '')}`.trim();
	}
	if (event.type === 'background_result_arrived') {
		return `[Background] ${String(payload.text ?? '')}`.trim();
	}
	if (event.type === 'team_message_arrived') {
		return `[Team] ${String(payload.from ?? 'unknown')} -> ${String(payload.to ?? 'unknown')} ${String(payload.summary ?? '')}`.trim();
	}
	if (event.type === 'teammate_state_changed') {
		return `[Teammate] ${String(payload.name ?? 'unknown')}: ${String(payload.previous_status ?? 'unknown')} -> ${String(payload.status ?? 'unknown')}`;
	}
	return null;
}

function truncateForPanel(text: string, limit = 72): string {
	// 右侧 activity 先走单行摘要，避免长 JSON/长路径把整个面板撑坏。
	const normalized = text.replace(/\s+/g, ' ').trim();
	if (normalized.length <= limit) {
		return normalized;
	}
	return `${normalized.slice(0, Math.max(0, limit - 1))}…`;
}

function appendAssistantDelta(
	timeline: TimelineItem[],
	assistantId: string,
	delta: string,
): TimelineItem[] {
	// assistant 文本不能只假设自己永远位于时间线最后。
	// 一旦中间插入了 tool/team/background 事件，后续增量仍然应该回写到同一个 assistant turn。
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
	// assistant 文本按“连续输出段”组织。
	// 一旦中间穿插 tool/team/background 事件，下一段 assistant 文本就应该新开一条消息。
	const assistantSegment: TimelineItem = {id: assistantId, kind: 'assistant', text};
	return [
		...timeline,
		assistantSegment,
	].slice(-30);
}

function App(): React.ReactElement {
	const {exit} = useApp();
	const startupArgs = useMemo(() => parseCliArgs(process.argv.slice(2)), []);
	const [session, setSession] = useState<SessionDescriptor | null>(null);
	const [inputState, setInputState] = useState<InputState>({text: '', pendingEscape: false});
	const [timeline, setTimeline] = useState<TimelineItem[]>([]);
	const [status, setStatus] = useState('正在初始化 session...');
	const [busy, setBusy] = useState(false);
	const [errorText, setErrorText] = useState<string | null>(null);

	useInput((inputChunk, key) => {
		if (busy) {
			return;
		}

		// 输入编辑规则统一收口到独立模型里，组件层只负责消费结果。
		const action = applyInputKey(inputState, {inputChunk, key});
		if (action.kind === 'exit') {
			exit();
			return;
		}
		if (action.kind === 'submit') {
			setInputState(action.state);
			void submitPrompt(action.submittedText);
			return;
		}
		if (action.kind === 'update') {
			setInputState(action.state);
		}
	}, {isActive: true});

	useEffect(() => {
		// TUI 启动时先拿到 session 元信息，后续每轮 prompt 都复用它。
		let cancelled = false;
		void bootstrapSession(startupArgs)
			.then(descriptor => {
				if (cancelled) {
					return;
				}
				setSession(descriptor);
				setStatus('就绪');
			})
			.catch(error => {
				if (cancelled) {
					return;
				}
				setErrorText(error instanceof Error ? error.message : String(error));
				setStatus('初始化失败');
			});
		return () => {
			cancelled = true;
		};
	}, [startupArgs]);

	const appendTimeline = (kind: TimelineItem['kind'], text: string): void => {
		// 对话、工具活动、后台/team 事件都统一进一条时间线，UI 更接近聊天流。
		const nextText = kind === 'activity' ? truncateForPanel(text) : text;
		setTimeline(previous => [
			...previous,
			{id: `${Date.now()}-${previous.length}`, kind, text: nextText},
		].slice(-30));
	};

	const submitPrompt = async (prompt: string): Promise<void> => {
		// prompt 一提交就先清空输入框，再把 user turn 立即放进对话区。
		// 这样用户看到的是正常聊天流，而不是“输入框里还残留上一轮内容”。
		if (!session || busy) {
			return;
		}
		if (prompt.trim() === '/quit') {
			exit();
			return;
		}
		if (!prompt.trim()) {
			return;
		}

		setBusy(true);
		setErrorText(null);
		setStatus('正在运行...');
		appendTimeline('user', prompt);
		let currentAssistantId: string | null = null;
		let runFailedMessage: string | null = null;

		try {
			await streamPrompt(session.session_id, prompt, {
				onEvent: event => {
					if (event.type === 'assistant_text_delta') {
						const delta = String(event.payload?.delta ?? '');
						if (!delta) {
							return;
						}
						// assistant 文本首次出现时才创建消息块；如果之前被工具事件打断，就新开一段。
						if (currentAssistantId === null) {
							currentAssistantId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
							const assistantId = currentAssistantId;
							setTimeline(previous => appendAssistantSegment(previous, assistantId, delta));
							return;
						}
						const assistantId = currentAssistantId;
						setTimeline(previous => appendAssistantDelta(previous, assistantId, delta));
						return;
					}
					if (event.type === 'run_failed') {
						runFailedMessage = String(event.payload?.message ?? '当前运行失败。');
						currentAssistantId = null;
						appendTimeline('activity', `[RunFailed] ${runFailedMessage}`);
						return;
					}

					const summary = summarizeEvent(event);
					if (summary) {
						// 一旦出现生命周期事件，后续 assistant 文本应该落到新的消息段里。
						currentAssistantId = null;
						appendTimeline('activity', summary);
					}
				},
				onError: message => {
					currentAssistantId = null;
					appendTimeline('activity', `[stderr] ${message}`);
				},
			});
			if (runFailedMessage) {
				setErrorText(runFailedMessage);
				setStatus('运行失败');
			} else {
				setStatus('就绪');
			}
		} catch (error) {
			const message = runFailedMessage ?? (error instanceof Error ? error.message : String(error));
			setErrorText(message);
			setStatus('运行失败');
		} finally {
			setBusy(false);
		}
	};

	const timelineItems = timeline.slice(-18);
	const inputLines = getVisibleInputLines(inputState.text);

	return (
		<Box flexDirection="column" paddingX={1}>
			<Box marginBottom={1} flexDirection="column">
				<Text color="cyan">xx-coding TUI (Ink MVP)</Text>
				<Text>
					Session: {session?.session_name ?? '加载中...'} {session ? `(${session.session_id})` : ''}
				</Text>
				<Text>Workspace: {session?.workspace_root ?? startupArgs.workspaceRoot ?? '默认工作区'}</Text>
				<Text>Status: {busy ? 'running' : status}</Text>
				{errorText ? <Text color="red">Error: {errorText}</Text> : null}
			</Box>

			<Box flexDirection="column" borderStyle="round" paddingX={1}>
				<Text bold>Timeline</Text>
				{timelineItems.length === 0 ? <Text color="gray">暂无对话</Text> : null}
				{timelineItems.map(item => (
					<Box key={item.id} flexDirection="column" marginBottom={1}>
						{item.kind === 'user' ? <Text color="yellow">You: {item.text}</Text> : null}
						{item.kind === 'assistant' ? <Text>Agent: {item.text || '...'}</Text> : null}
						{item.kind === 'activity' ? <Text color="cyan">Event: {item.text}</Text> : null}
					</Box>
				))}
			</Box>

			<Box marginTop={1} borderStyle="round" paddingX={1} flexDirection="column">
				<Text color="green">{busy ? 'Busy>' : 'You>'}</Text>
				{inputState.text === '' ? (
					<Text color="gray">输入 prompt，/quit 退出</Text>
				) : (
					inputLines.map((line, index) => (
						<Text key={`${index}-${line}`}>
							{line}
							{index === inputLines.length - 1 ? '█' : ''}
						</Text>
					))
				)}
			</Box>
			<Text color="gray">Enter 发送 | Esc 后按 Enter 换行 | /quit 或 Ctrl+C 退出</Text>
			{inputState.pendingEscape ? (
				<Text color="yellow">Esc 已按下：现在按 Enter 会插入换行。</Text>
			) : null}
		</Box>
	);
}

export default App;

// Ink 程序必须显式调用 render() 才会真正进入交互循环。
// 这里只保留一个最小入口，不在启动层额外做复杂参数分发。
render(<App />);
