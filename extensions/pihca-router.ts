import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_ROOT = resolve(PACKAGE_ROOT, "skills/high-content-microscopy");
const INTAKE_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_intake.py");
const PREPARE_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_prepare.py");
const NUCLEI_PILOT_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_nuclei_pilot.py");
const CELL_PILOT_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_cell_pilot.py");
const REVIEW_UI_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_review_ui.py");
const VISION_REVIEW_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_vision_review.py");
const WORKFLOW_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_workflow.py");
const FILTER_REVIEW_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_filter_review.py");
const HELDOUT_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_heldout.py");
const RELEASE_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_release.py");
const PRODUCTION_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_production.py");
const PLATE_REVIEW_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_plate_review.py");
const RECOVER_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_recover.py");
const DEFAULT_PROFILE = resolve(SKILL_ROOT, "configs/hcsai-dapi-phalloidin.json");
const CONFIG_ROOT = resolve(SKILL_ROOT, "configs");
const TEMPLATES_SCRIPT = resolve(SKILL_ROOT, "scripts/hca_templates.py");
const TRIGGER = /\b(?:pi\s*hca|pihca)\b/i;
const DEICTIC_INPUT = /\b(?:this|these|here|current|directory|folder|cwd)\b/i;
const STATE_ENTRY = "pihca-workflow-state";

type Phase = "inactive" | "plate_selection_required" | "assay_contract_required" | "runtime_setup_required"
	| "pilot_segmentation_required" | "nuclei_review_required" | "cell_segmentation_required"
	| "cell_review_required" | "filter_review_required" | "heldout_validation_required"
	| "release_approval_required" | "production_canary_required" | "batch_approval_required"
	| "batch_running" | "plate_qc_required" | "complete";

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
Call pihca_list_templates and recommend the closest assay graph. Ask only for its unresolved channel roles, listed confirmations, nuclear guidance, and human versus automated optimization. Segmentation tuning may proceed blinded to treatments and controls. Once confirmed, call pihca_prepare immediately; do not merely describe the command, inspect treatment identities in image metadata, or call HCA scripts through bash.`;
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
	if (state.phase === "cell_segmentation_required") return `Current phase: cell-object tuning.${selected}\nCall pihca_tune_cells. It uses accepted nuclei as guidance when the selected template includes them, and runs cell-only otherwise.`;
	if (state.phase === "cell_review_required") return `Current phase: cell and relationship review.${selected}\nOpen pihca_review_segmentation for stage cell, then call pihca_accept_review only after a named review is approved.`;
	if (state.phase === "filter_review_required") return `Current phase: filter review.${selected}\nCall pihca_review_filters to preview exclusions. Call pihca_accept_filters only for explicit no-filter settings or filters supported by accepted exclusion evidence.`;
	if (state.phase === "heldout_validation_required") return `Current phase: held-out validation.${selected}\nCall pihca_run_heldout. Record its validation only after visual review covers the configured minimum wells and fields.`;
	if (state.phase === "release_approval_required") return `Current phase: release approval.${selected}\nAsk for the named operator and reviewer, then call pihca_approve_release. Do not run production yet.`;
	if (state.phase === "production_canary_required") return `Current phase: production canary.${selected}\nCall pihca_run_canary on one untouched well using the immutable release.`;
	if (state.phase === "batch_approval_required") return `Current phase: batch approval.${selected}\nPresent the canary result and wait for explicit user approval to run this plate. Only then call pihca_submit_batch.`;
	if (state.phase === "batch_running") return `Current phase: batch running.${selected}\nCall pihca_status to report journaled progress. Do not start another plate.`;
	if (state.phase === "plate_qc_required") return `Current phase: plate QC.${selected}\nCall pihca_review_plate, then call pihca_complete_plate_qc only with its approved named review. Do not interpret biology before completion.`;
	return "";
}

export default function pihcaRouter(pi: ExtensionAPI) {
	let intakeOnlyTurn = false;
	let releaseApprovalTurn = false;
	let batchApprovalTurn = false;
	let state: WorkflowState = { active: false, phase: "inactive", acquisitions: [] };

	const persist = () => pi.appendEntry<WorkflowState>(STATE_ENTRY, state);
	const syncWorkflow = () => {
		if (!state.workflowState || !existsSync(state.workflowState)) return;
		const workflow = JSON.parse(readFileSync(state.workflowState, "utf8")) as { phase?: Phase };
		if (workflow.phase) state = { ...state, phase: workflow.phase };
		persist();
	};
	const runWorkflow = async (script: string, args: string[], timeout = 120_000) => {
		const result = await pi.exec(process.env.PIHCA_PYTHON ?? "python3", [script, ...args], { timeout });
		if (result.code === 0) syncWorkflow();
		return result;
	};
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
		name: "pihca_list_templates",
		label: "List PiHCA Assay Templates",
		description: "List executable bundled assay templates, their channel assumptions, and required confirmations.",
		parameters: Type.Object({}),
		async execute() {
			const result = await runWorkflow(TEMPLATES_SCRIPT, ["list"]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

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
			config_template: Type.String({ description: "Template ID from pihca_list_templates, a confirmed config path, or bundled-dapi-phalloidin" }),
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
			const catalogProfile = resolve(CONFIG_ROOT, `hcsai-${params.config_template}.json`);
			const profile = params.config_template === "bundled-dapi-phalloidin" ? DEFAULT_PROFILE
				: (existsSync(catalogProfile) ? catalogProfile : params.config_template);
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
		name: "pihca_accept_review",
		label: "Accept PiHCA Segmentation Review",
		description: "Validate a completed named nuclei or cell review, copy its selected Cellpose parameters into a new immutable config version, and advance one guarded workflow phase.",
		parameters: Type.Object({
			stage: Type.String({ description: "nucleus or cell" }),
			review: Type.String({ description: "Approved human-review.json" }),
			workflow_state: Type.Optional(Type.String({ description: "Defaults to the active workflow state" })),
		}),
		async execute(_toolCallId, params) {
			if (params.stage !== "nucleus" && params.stage !== "cell") return { content: [{ type: "text", text: "stage must be nucleus or cell" }] };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(WORKFLOW_SCRIPT, ["--workflow-state", workflow, "accept-review", "--stage", params.stage, "--review", params.review]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : `PiHCA review acceptance failed:\n${result.stderr || result.stdout}` }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_tune_cells",
		label: "Tune PiHCA Cells",
		description: "Run a bounded cell-object Cellpose sweep, using accepted nuclear labels for guidance and relationship QC when configured.",
		parameters: Type.Object({
			workflow_state: Type.Optional(Type.String()), diameters: Type.Optional(Type.String()),
			flow_thresholds: Type.Optional(Type.String()), cellprob_thresholds: Type.Optional(Type.String()),
		}),
		async execute(_toolCallId, params) {
			if (state.phase !== "cell_segmentation_required") return { content: [{ type: "text", text: `Cannot tune cells during phase ${state.phase}.` }] };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const args = ["--workflow-state", workflow];
			if (params.diameters) args.push(`--diameters=${params.diameters}`);
			if (params.flow_thresholds) args.push(`--flow-thresholds=${params.flow_thresholds}`);
			if (params.cellprob_thresholds) args.push(`--cellprob-thresholds=${params.cellprob_thresholds}`);
			const result = await runWorkflow(CELL_PILOT_SCRIPT, args, 30 * 60_000);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : `PiHCA cell tuning failed:\n${result.stderr || result.stdout}` }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_review_segmentation",
		label: "Review PiHCA Segmentation",
		description: "Open the local review UI for nuclei or cell candidates; cell review includes relationship metrics in candidate evidence.",
		parameters: Type.Object({ candidates: Type.String(), mode: Type.String(), stage: Type.String() }),
		async execute(_toolCallId, params) {
			const expected = params.stage === "cell" ? "cell_review_required" : "nuclei_review_required";
			if (state.phase !== expected) return { content: [{ type: "text", text: `Cannot review ${params.stage} during phase ${state.phase}.` }] };
			if (params.mode !== "human" && params.mode !== "automated") return { content: [{ type: "text", text: "mode must be human or automated" }] };
			const candidates = resolveCandidate(params.candidates, process.cwd());
			if (!candidates) return { content: [{ type: "text", text: `Candidate file not found: ${params.candidates}` }] };
			const reviewDir = resolve(dirname(candidates), `${params.mode}-review`);
			if (params.mode === "human") {
				const result = await runWorkflow(REVIEW_UI_SCRIPT, ["start", "--candidates", candidates, "--output-dir", reviewDir, "--open-browser"]);
				return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr }], details: { state, reviewDir } };
			}
			const build = await runWorkflow(REVIEW_UI_SCRIPT, ["build", "--candidates", candidates, "--output-dir", reviewDir]);
			if (build.code !== 0) return { content: [{ type: "text", text: build.stderr || build.stdout }] };
			const template = resolve(reviewDir, "vision-review.pending.json");
			const contract = await runWorkflow(VISION_REVIEW_SCRIPT, ["template", "--candidates", candidates, "--output", template]);
			return { content: [{ type: "text", text: `${build.stdout}\n${contract.stdout}` }], details: { state, reviewDir, template } };
		},
	});

	pi.registerTool({
		name: "pihca_accept_filters",
		label: "Accept PiHCA Filters",
		description: "Version explicit no-filter settings or reviewed size/intensity filters. Non-empty filters require accepted exclusion evidence.",
		parameters: Type.Object({ review: Type.String(), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(WORKFLOW_SCRIPT, ["--workflow-state", workflow, "accept-filters", "--review", params.review]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_review_filters",
		label: "Review PiHCA Filters",
		description: "Apply proposed filters to accepted pilot labels and show before/after exclusion overlays for human or vision review.",
		parameters: Type.Object({ segmentation_review: Type.String(), mode: Type.Optional(Type.String()), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			if (state.phase !== "filter_review_required") return { content: [{ type: "text", text: `Cannot review filters during phase ${state.phase}.` }] };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const mode = params.mode ?? "human";
			const reviewDir = resolve(dirname(workflow), "filter-review");
			const action = mode === "human" ? "start" : "build";
			const result = await runWorkflow(FILTER_REVIEW_SCRIPT, [action, "--workflow-state", workflow,
				"--review", params.segmentation_review, "--output-dir", reviewDir], 10 * 60_000);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state, reviewDir } };
		},
	});

	pi.registerTool({
		name: "pihca_record_heldout",
		label: "Record PiHCA Held-Out Validation",
		description: "Validate and record independent held-out evidence after its visual review is complete.",
		parameters: Type.Object({ validation: Type.String(), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(WORKFLOW_SCRIPT, ["--workflow-state", workflow, "record-heldout", "--validation", params.validation]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_run_heldout",
		label: "Run PiHCA Held-Out Validation",
		description: "Run the accepted configuration on deterministic untouched wells and open visual review for every held-out field.",
		parameters: Type.Object({ workers: Type.Optional(Type.Number()), mode: Type.Optional(Type.String()), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			if (state.phase !== "heldout_validation_required") return { content: [{ type: "text", text: `Cannot run held-out validation during phase ${state.phase}.` }] };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(HELDOUT_SCRIPT, ["run", "--workflow-state", workflow,
				"--workers", String(params.workers ?? 1), "--mode", params.mode ?? "human"], 60 * 60_000);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_approve_release",
		label: "Approve PiHCA Release",
		description: "Create an immutable release binding config, runtime, manifest, all stage reviews, held-out evidence, and named approval.",
		parameters: Type.Object({ operator: Type.String(), reviewer: Type.String(), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			if (!releaseApprovalTurn) return { content: [{ type: "text", text: "Release creation requires explicit user approval in the current turn, including the named operator and reviewer." }], details: { state, error: "approval required" } };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(RELEASE_SCRIPT, ["create", "--workflow-state", workflow, "--operator", params.operator, "--reviewer", params.reviewer]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_run_canary",
		label: "Run PiHCA Production Canary",
		description: "Run one untouched well with the exact approved release before production approval.",
		parameters: Type.Object({ well: Type.Optional(Type.String()), gpus: Type.Optional(Type.String()), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const args = ["--workflow-state", workflow, "canary", "--gpus", params.gpus ?? "auto"];
			if (params.well) args.push("--well", params.well);
			const result = await runWorkflow(PRODUCTION_SCRIPT, args, 60 * 60_000);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_submit_batch",
		label: "Submit Approved PiHCA Plate",
		description: "After explicit user approval, submit one release and one plate to the guarded queue with parallel wells.",
		parameters: Type.Object({ operator: Type.String(), workers: Type.Optional(Type.Number()), retries: Type.Optional(Type.Number()), gpus: Type.Optional(Type.String()), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			if (!batchApprovalTurn) return { content: [{ type: "text", text: "Batch submission requires explicit user approval in the current turn." }], details: { state, error: "approval required" } };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(PRODUCTION_SCRIPT, ["--workflow-state", workflow, "submit", "--operator", params.operator,
				"--workers", String(params.workers ?? 1), "--retries", String(params.retries ?? 1), "--gpus", params.gpus ?? "auto"]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_status",
		label: "PiHCA Status",
		description: "Report the persisted workflow phase and journaled production progress without rediscovery.",
		parameters: Type.Object({ workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const script = state.phase === "batch_running" ? PRODUCTION_SCRIPT : WORKFLOW_SCRIPT;
			const action = state.phase === "batch_running" ? ["--workflow-state", workflow, "status"] : ["--workflow-state", workflow, "status"];
			const result = await runWorkflow(script, action);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_complete_plate_qc",
		label: "Complete PiHCA Plate QC",
		description: "Validate an approved plate-level visual review, generate final numeric and HTML reports, and create a portable share bundle.",
		parameters: Type.Object({ review: Type.String(), workflow_state: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			if (state.phase !== "plate_qc_required") return { content: [{ type: "text", text: `Cannot complete plate QC during phase ${state.phase}.` }] };
			const workflow = params.workflow_state ?? state.workflowState;
			if (!workflow) return { content: [{ type: "text", text: "No active PiHCA workflow state." }] };
			const result = await runWorkflow(PRODUCTION_SCRIPT, ["--workflow-state", workflow, "complete-plate-qc", "--review", params.review], 10 * 60_000);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_review_plate",
		label: "Review PiHCA Plate",
		description: "Generate the production report and open sampled plate overlays for final human QC.",
		parameters: Type.Object({ mode: Type.Optional(Type.String()) }),
		async execute(_toolCallId, params) {
			if (state.phase !== "plate_qc_required" || !state.workflowState) return { content: [{ type: "text", text: `Cannot review plate during phase ${state.phase}.` }] };
			const workflow = JSON.parse(readFileSync(state.workflowState, "utf8")) as { batch_run?: string };
			if (!workflow.batch_run) return { content: [{ type: "text", text: "Workflow has no production run directory." }] };
			const result = await runWorkflow(PLATE_REVIEW_SCRIPT, [params.mode === "automated" ? "build" : "start", "--run-dir", workflow.batch_run], 10 * 60_000);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
		},
	});

	pi.registerTool({
		name: "pihca_archive_staging",
		label: "Archive PiHCA Staging",
		description: "Move stale staging directories into timestamped recovery storage without deleting evidence.",
		parameters: Type.Object({ run_dir: Type.String() }),
		async execute(_toolCallId, params) {
			const result = await runWorkflow(RECOVER_SCRIPT, ["--run-dir", params.run_dir]);
			return { content: [{ type: "text", text: result.code === 0 ? result.stdout : result.stderr || result.stdout }], details: { state } };
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
		releaseApprovalTurn = state.phase === "release_approval_required"
			&& /\bapprove\b[\s\S]*\brelease\b|\brelease\b[\s\S]*\bapprove\b/i.test(event.text);
		batchApprovalTurn = state.phase === "batch_approval_required"
			&& (/\b(?:approve|approved|run|start|submit|proceed)\b[\s\S]*\b(?:batch|plate|production)\b/i.test(event.text)
				|| /^\s*(?:yes|y|approved?|proceed)\s*[.!]?\s*$/i.test(event.text));
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
			return { systemPrompt: `${event.systemPrompt}\n\n## Active PiHCA Assay Session\nUse only the registered pihca_* tools for workflow transitions. Act as a rigorous microscopy assay expert and advance exactly one guarded phase per decision. Never invoke skill_manage, repeat intake, infer treatment from image metadata, choose segmentation by count, invent shell commands, mutate approved artifacts, or submit an unapproved batch.\n${statePrompt(state)}` };
	});

	pi.on("tool_call", (event) => {
		if (intakeOnlyTurn) return { block: true, reason: "PiHCA intake is complete; present its result and await the next decision." };
		if (!state.active) return undefined;
		if (event.toolName === "skill_manage") return { block: true, reason: "PiHCA is already active; do not invoke skill_manage." };
		if (event.toolName === "bash") {
			const command = String((event.input as { command?: string }).command ?? "");
			if (/\bhca_[a-z0-9_]+\.py\b|\bpython\S*\s+-m\s+hca_[a-z0-9_]+\b|\bpihca(?:-(?:runner|queue))?\s+(?:runner|queue|release|production|workflow)\b|\bpihca-(?:runner|queue)\b|\b(?:find|ls\s+-R|grep\s+-r)\b/.test(command)) {
				return { block: true, reason: "Use the guarded pihca_* workflow tools instead of direct analysis, production, or discovery commands." };
			}
		}
		return undefined;
	});

	pi.on("agent_end", () => { intakeOnlyTurn = false; releaseApprovalTurn = false; batchApprovalTurn = false; });
}
