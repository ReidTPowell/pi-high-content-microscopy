import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { existsSync, statSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_ROOT = resolve(PACKAGE_ROOT, "skills/high-content-microscopy");
const INTAKE_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_intake.py");
const PREPARE_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_prepare.py");
const NUCLEI_PILOT_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_nuclei_pilot.py");
const REVIEW_UI_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_review_ui.py");
const VISION_REVIEW_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_vision_review.py");
const DEFAULT_PROFILE = resolve(SKILL_ROOT, "configs/hcsai-dapi-phalloidin.json");
const TRIGGER = /\b(?:pi\s*hca|pihca)\b/i;
const DEICTIC_INPUT = /\b(?:this|these|here|current|directory|folder|cwd)\b/i;
const STATE_ENTRY = "pihca-workflow-state";

type Phase = "inactive" | "plate_selection_required" | "assay_contract_required" | "runtime_setup_required" | "pilot_segmentation_required" | "nuclei_review_required";

interface Acquisition {
	acquisition: string;
	images: number;
	wells: number;
	channels: Array<number | string>;
}

interface WorkflowState {
	active: boolean;
	phase: Phase;
	input?: string;
	acquisitions: Acquisition[];
	selectedAcquisition?: string;
	workflowState?: string;
}

function resolveCandidate(value: string, cwd: string): string | undefined {
	const expanded = value.replace(/^~/, process.env.HOME ?? "");
	const candidate = isAbsolute(expanded) ? expanded : resolve(cwd, expanded);
	return existsSync(candidate) ? candidate : undefined;
}

function extractExistingPath(text: string, cwd: string): string | undefined {
	for (const match of text.matchAll(/["']([^"']+)["']/g)) {
		const candidate = match[1] && resolveCandidate(match[1], cwd);
		if (candidate) return candidate;
	}
	const slash = text.indexOf("/");
	if (slash >= 0) {
		let candidate = text.slice(slash).trim().replace(/[.,;:!?]+$/, "");
		while (candidate) {
			const resolved = resolveCandidate(candidate, cwd);
			if (resolved) return resolved;
			const shortened = candidate.replace(/\s+\S+$/, "");
			if (shortened === candidate) break;
			candidate = shortened;
		}
	}
	if (DEICTIC_INPUT.test(text) && existsSync(cwd) && statSync(cwd).isDirectory()) return cwd;
	return undefined;
}

function selectedAcquisition(text: string, acquisitions: Acquisition[]): string | undefined {
	const ordinal = text.match(/\b(first|second|third|fourth|last)\b/i)?.[1]?.toLowerCase();
	const index = ordinal === "first" ? 0 : ordinal === "second" ? 1 : ordinal === "third" ? 2
		: ordinal === "fourth" ? 3 : ordinal === "last" ? acquisitions.length - 1 : -1;
	if (index >= 0 && index < acquisitions.length) return acquisitions[index].acquisition;
	return acquisitions.find((item) => text.includes(item.acquisition) || text.includes(dirname(item.acquisition).split("/").pop() ?? ""))?.acquisition;
}

function statePrompt(state: WorkflowState): string {
	const selected = state.selectedAcquisition ? `\nSelected acquisition: ${state.selectedAcquisition}` : "";
	if (state.phase === "plate_selection_required") {
		return `Current phase: plate selection. Resolve ordinal or barcode choices from the authoritative acquisition list. Do not rediscover files.${selected}`;
	}
	if (state.phase === "assay_contract_required") {
		return `Current phase: assay contract.${selected}
Ask only for unresolved channel roles, primary/secondary objects, nuclear guidance, and human versus automated optimization. Segmentation tuning may proceed blinded to treatments and controls. Once those segmentation facts are confirmed, call pihca_prepare immediately; do not merely describe the command, inspect treatment identities in image metadata, or call HCA scripts through bash.`;
	}
	if (state.phase === "pilot_segmentation_required") {
		return `Current phase: pilot segmentation.${selected}
The preconfiguration and paired pilot-field plan exist at ${state.workflowState ?? "the pihca_prepare result"}. Call pihca_tune_nuclei now. Do not repeat intake or assay-contract questions or run Cellpose through bash. Nuclei must be reviewed before secondary cell tuning, relationship QC, and filter tuning.`;
	}
	if (state.phase === "runtime_setup_required") {
		return `Current phase: runtime setup.${selected}\nThe assay packet and paired pilot fields exist, but the active Python interpreter lacks a requested engine. Report the exact missing modules from preconfiguration and create/select a locked PiHCA runtime. Do not repeat intake or inspect images ad hoc.`;
	}
	if (state.phase === "nuclei_review_required") {
		return `Current phase: nuclei review.${selected}\nCall pihca_review_nuclei in human or automated mode. Do not tune secondary cells until one nuclei candidate is reviewed and accepted.`;
	}
	return "";
}

export default function pihcaRouter(pi: ExtensionAPI) {
	let intakeOnlyTurn = false;
	let state: WorkflowState = { active: false, phase: "inactive", acquisitions: [] };

	const persist = () => pi.appendEntry<WorkflowState>(STATE_ENTRY, state);
	const reconstruct = (ctx: ExtensionContext) => {
		state = { active: false, phase: "inactive", acquisitions: [] };
		for (const entry of ctx.sessionManager.getBranch()) {
			if (entry.type === "custom" && entry.customType === STATE_ENTRY && entry.data) {
				state = entry.data as WorkflowState;
			}
		}
	};

	pi.on("session_start", async (_event, ctx) => reconstruct(ctx));
	pi.on("session_tree", async (_event, ctx) => reconstruct(ctx));

	pi.registerTool({
		name: "pihca_prepare",
		label: "Prepare PiHCA Pilot",
		description: "Version a confirmed single-plate assay config and build its manifest, metadata curation, validation, well plan, QC sample, and reproducible workflow state. Use after segmentation channel/object roles are confirmed. Treatment labels are optional for blinded segmentation optimization.",
		promptSnippet: "Prepare a confirmed single-plate PiHCA pilot without ad hoc shell discovery",
		promptGuidelines: [
			"Use pihca_prepare as soon as one plate and segmentation channel/object roles are confirmed.",
			"Set blinded=true when treatment/control identities are unavailable or the user requests blinded tuning.",
		],
		parameters: Type.Object({
			acquisition: Type.String({ description: "Exact selected acquisition directory from PiHCA intake" }),
			config_template: Type.String({ description: "Confirmed assay template path, or bundled-dapi-phalloidin only after DAPI nuclei and phalloidin cell boundaries are explicitly confirmed" }),
			optimization_mode: Type.Optional(Type.String({ description: "human (default) or automated" })),
			blinded: Type.Optional(Type.Boolean({ description: "Tune segmentation without treatment/control identities" })),
			endpoint: Type.Optional(Type.String({ description: "Biological endpoint when known" })),
			plate_map: Type.Optional(Type.String({ description: "Optional plate-map CSV" })),
			workers: Type.Optional(Type.Number({ description: "Parallel wells planned for later batch execution; default 1" })),
		}),
		async execute(_toolCallId, params) {
			const acquisition = resolveCandidate(params.acquisition, process.cwd());
			if (!acquisition || !statSync(acquisition).isDirectory()) {
				return { content: [{ type: "text", text: `Invalid acquisition directory: ${params.acquisition}` }], details: { state, error: "invalid acquisition" } };
			}
			if (state.selectedAcquisition && resolve(state.selectedAcquisition) !== resolve(acquisition)) {
				return { content: [{ type: "text", text: "The requested acquisition does not match the plate selected during intake." }], details: { state, error: "plate mismatch" } };
			}
			const mode = params.optimization_mode ?? "human";
			if (mode !== "human" && mode !== "automated") {
				return { content: [{ type: "text", text: "optimization_mode must be human or automated" }], details: { state, error: "invalid mode" } };
			}
			const profile = params.config_template === "bundled-dapi-phalloidin" ? DEFAULT_PROFILE : params.config_template;
			const args = [PREPARE_SCRIPT, "--input", acquisition, "--config-template", profile,
				"--optimization-mode", mode, "--workers", String(params.workers ?? 1)];
			if (params.blinded) args.push("--blinded");
			if (params.endpoint) args.push("--endpoint", params.endpoint);
			if (params.plate_map) args.push("--plate-map", params.plate_map);
			const result = await pi.exec(process.env.PIHCA_PYTHON ?? "python3", args, { timeout: 120_000 });
			if (result.code !== 0) {
				return { content: [{ type: "text", text: `PiHCA preconfiguration failed:\n${result.stderr || result.stdout}` }], details: { state, error: "prepare failed" } };
			}
			const payload = JSON.parse(result.stdout) as { workflow_state: string; phase: Phase };
			state = { ...state, active: true, phase: payload.phase, selectedAcquisition: acquisition, workflowState: payload.workflow_state };
			persist();
			return { content: [{ type: "text", text: result.stdout }], details: { state, payload } };
		},
	});

	pi.registerTool({
		name: "pihca_review_nuclei",
		label: "Review PiHCA Nuclei",
		description: "Start the local HTML nuclei-candidate review UI, or build the structured image assets and vision-review template for an image-capable model.",
		promptSnippet: "Open human or automated visual QC for PiHCA nuclei candidates",
		promptGuidelines: ["Call pihca_review_nuclei after pihca_tune_nuclei; never select candidates by count alone."],
		parameters: Type.Object({
			candidates: Type.String({ description: "candidates.json returned by pihca_tune_nuclei" }),
			mode: Type.String({ description: "human or automated" }),
		}),
		async execute(_toolCallId, params) {
			if (state.phase !== "nuclei_review_required") {
				return { content: [{ type: "text", text: `Cannot review nuclei during phase ${state.phase}.` }], details: { state, error: "invalid phase" } };
			}
			if (params.mode !== "human" && params.mode !== "automated") {
				return { content: [{ type: "text", text: "mode must be human or automated" }], details: { state, error: "invalid mode" } };
			}
			const candidates = resolveCandidate(params.candidates, process.cwd());
			if (!candidates) {
				return { content: [{ type: "text", text: `Candidate file not found: ${params.candidates}` }], details: { state, error: "missing candidates" } };
			}
			const reviewDir = resolve(dirname(candidates), `${params.mode}-review`);
			const runtime = process.env.PIHCA_PYTHON ?? "python3";
			if (params.mode === "human") {
				const result = await pi.exec(runtime, [REVIEW_UI_SCRIPT, "start", "--candidates", candidates, "--output-dir", reviewDir, "--open-browser"], { timeout: 120_000 });
				if (result.code !== 0) return { content: [{ type: "text", text: `PiHCA review UI failed:\n${result.stderr || result.stdout}` }], details: { state, error: "review UI failed" } };
				return { content: [{ type: "text", text: result.stdout }], details: { state, mode: params.mode, reviewDir } };
			}
			const build = await pi.exec(runtime, [REVIEW_UI_SCRIPT, "build", "--candidates", candidates, "--output-dir", reviewDir], { timeout: 120_000 });
			if (build.code !== 0) return { content: [{ type: "text", text: `PiHCA vision assets failed:\n${build.stderr || build.stdout}` }], details: { state, error: "vision assets failed" } };
			const template = resolve(reviewDir, "vision-review.pending.json");
			const contract = await pi.exec(runtime, [VISION_REVIEW_SCRIPT, "template", "--candidates", candidates, "--output", template], { timeout: 120_000 });
			if (contract.code !== 0) return { content: [{ type: "text", text: `PiHCA vision contract failed:\n${contract.stderr || contract.stdout}` }], details: { state, error: "vision contract failed" } };
			return { content: [{ type: "text", text: `${build.stdout}\n${contract.stdout}\nInspect every raw/overlay PNG with the active image-capable model and complete the structured template.` }], details: { state, mode: params.mode, reviewDir, template } };
		},
	});

	pi.registerTool({
		name: "pihca_tune_nuclei",
		label: "Tune PiHCA Nuclei",
		description: "Run the bounded, versioned nuclei Cellpose sweep for the median-ranked paired pilot field prepared by pihca_prepare. Returns review-required raw and overlay candidates; it never auto-approves a winner.",
		promptSnippet: "Advance a prepared PiHCA workflow into bounded nuclei Cellpose tuning",
		promptGuidelines: ["Call pihca_tune_nuclei when the active PiHCA phase is pilot_segmentation_required."],
		parameters: Type.Object({
			workflow_state: Type.String({ description: "workflow-state.json returned by pihca_prepare" }),
			field_index: Type.Optional(Type.Number({ description: "0 low, 1 median (default), 2 high acquisition-intensity rank" })),
			diameters: Type.Optional(Type.String({ description: "Bounded comma-separated Cellpose diameters; default auto,18,24" })),
			flow_thresholds: Type.Optional(Type.String({ description: "Bounded comma-separated flow thresholds; default 0.3,0.4" })),
			cellprob_thresholds: Type.Optional(Type.String({ description: "Bounded comma-separated cell-probability thresholds; default -1,0" })),
		}),
		async execute(_toolCallId, params) {
			if (state.phase !== "pilot_segmentation_required") {
				return { content: [{ type: "text", text: `Cannot tune nuclei during phase ${state.phase}.` }], details: { state, error: "invalid phase" } };
			}
			const args = [NUCLEI_PILOT_SCRIPT, "--workflow-state", params.workflow_state];
			if (params.field_index !== undefined) args.push("--field-index", String(params.field_index));
			if (params.diameters) args.push(`--diameters=${params.diameters}`);
			if (params.flow_thresholds) args.push(`--flow-thresholds=${params.flow_thresholds}`);
			if (params.cellprob_thresholds) args.push(`--cellprob-thresholds=${params.cellprob_thresholds}`);
			const result = await pi.exec(process.env.PIHCA_PYTHON ?? "python3", args, { timeout: 30 * 60_000 });
			if (result.code !== 0) {
				return { content: [{ type: "text", text: `PiHCA nuclei tuning failed:\n${result.stderr || result.stdout}` }], details: { state, error: "nuclei tuning failed" } };
			}
			const payload = JSON.parse(result.stdout) as { workflow_state: string };
			state = { ...state, phase: "nuclei_review_required", workflowState: payload.workflow_state };
			persist();
			return { content: [{ type: "text", text: result.stdout }], details: { state, payload } };
		},
	});

	pi.on("input", async (event, ctx) => {
		if (event.text.includes("[PiHCA router:")) return { action: "continue" };
		if (TRIGGER.test(event.text)) {
			if (state.active && !extractExistingPath(event.text, ctx.cwd)) {
				return { action: "transform", text: `${event.text}\n\n[PiHCA router: resume existing workflow]\n${statePrompt(state)}` };
			}
			const inputPath = extractExistingPath(event.text, ctx.cwd);
			if (!inputPath) {
				return { action: "transform", text: `${event.text}\n\n[PiHCA router] Ask for one existing microscopy directory. Do not invoke skill_manage or use tools until the user supplies it.` };
			}
			const result = await pi.exec("python3", [INTAKE_SCRIPT, "--input", inputPath], { timeout: 60_000 });
			if (result.code !== 0) {
				return { action: "transform", text: `${event.text}\n\n[PiHCA router] Deterministic intake failed. Report this exact error and do not fall back to ls/find:\n${result.stderr || result.stdout}` };
			}
			const payload = JSON.parse(result.stdout) as { status: string; acquisitions: Acquisition[] };
			state = { active: true, phase: payload.acquisitions.length > 1 ? "plate_selection_required" : "assay_contract_required",
				input: inputPath, acquisitions: payload.acquisitions, selectedAcquisition: payload.acquisitions.length === 1 ? payload.acquisitions[0].acquisition : undefined };
			persist();
			intakeOnlyTurn = true;
			const presentation = JSON.stringify({
				status: payload.status,
				acquisitions: payload.acquisitions,
				next_decision: payload.acquisitions.length > 1 ? "select exactly one acquisition" : "confirm the assay contract",
			}, null, 2);
			return { action: "transform", text: `${event.text}\n\n[PiHCA router: authoritative intake completed]\n${presentation}\n\nDo not call tools this turn. Present the compact inventory and the single next decision. If multiple plates are listed, ask only for one plate selection. Otherwise ask the assay-contract questions together. Do not infer channel biology.` };
		}

		if (state.active && state.phase === "plate_selection_required") {
			const selected = selectedAcquisition(event.text, state.acquisitions);
			if (selected) {
				state = { ...state, phase: "assay_contract_required", selectedAcquisition: selected };
				persist();
				return { action: "transform", text: `${event.text}\n\n[PiHCA router: plate selected]\n${statePrompt(state)}\nDo not call discovery tools. Recommend blinded optimization when controls are unavailable.` };
			}
		}

		if (state.active) return { action: "transform", text: `${event.text}\n\n[PiHCA router: active workflow]\n${statePrompt(state)}` };
		return { action: "continue" };
	});

	pi.on("before_agent_start", (event) => {
		if (!state.active) return undefined;
		return { systemPrompt: `${event.systemPrompt}\n\n## Active PiHCA Assay Session\nUse the installed high-content-microscopy workflow and its pihca_prepare, pihca_tune_nuclei, and pihca_review_nuclei tools. Act as a rigorous microscopy assay expert and advance exactly one phase per decision. Never invoke skill_manage, repeat intake, infer biological treatment from image metadata, choose segmentation by object count, or submit an unapproved batch.\n${statePrompt(state)}` };
	});

	pi.on("tool_call", (event) => {
		if (intakeOnlyTurn) return { block: true, reason: "PiHCA intake is complete; present its result and await the next decision." };
		if (!state.active || state.phase === "pilot_segmentation_required" || state.phase === "runtime_setup_required") return undefined;
		if (event.toolName === "skill_manage") return { block: true, reason: "PiHCA is already active; do not invoke skill_manage." };
		if (event.toolName === "bash") {
			const command = String((event.input as { command?: string }).command ?? "");
			if (/\bhca_(?:preconfigure|metadata)\.py\b|\b(?:find|ls\s+-R|grep\s+-r)\b/.test(command)) {
				return { block: true, reason: "Use authoritative intake state and pihca_prepare instead of ad hoc discovery or direct preconfiguration commands." };
			}
		}
		return undefined;
	});

	pi.on("agent_end", () => { intakeOnlyTurn = false; });
}
