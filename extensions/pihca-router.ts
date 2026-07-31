import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const INTAKE_SCRIPT = resolve(PACKAGE_ROOT, "skills/high-content-microscopy/scripts/hca_intake.py");
const TRIGGER = /\b(?:pi\s*hca|pihca)\b/i;

function extractExistingPath(text: string): string | undefined {
	for (const match of text.matchAll(/["']([^"']+)["']/g)) {
		const candidate = match[1]?.replace(/^~/, process.env.HOME ?? "");
		if (candidate && existsSync(candidate)) return candidate;
	}
	const slash = text.indexOf("/");
	if (slash < 0) return undefined;
	let candidate = text.slice(slash).trim().replace(/[.,;:!?]+$/, "");
	while (candidate) {
		if (existsSync(candidate)) return candidate;
		const shortened = candidate.replace(/\s+\S+$/, "");
		if (shortened === candidate) break;
		candidate = shortened;
	}
	return undefined;
}

export default function pihcaRouter(pi: ExtensionAPI) {
	let intakeOnlyTurn = false;
	let pihcaSession = false;

	pi.on("input", async (event) => {
		if (!TRIGGER.test(event.text)) return { action: "continue" };
		pihcaSession = true;
		const inputPath = extractExistingPath(event.text);
		if (!inputPath) {
			return {
				action: "transform",
				text: `${event.text}\n\n[PiHCA router] Activate the high-content-microscopy skill. Ask for one existing microscopy input path before using tools.`,
			};
		}
		const result = await pi.exec("python3", [INTAKE_SCRIPT, "--input", inputPath], { timeout: 60_000 });
		if (result.code !== 0) {
			return {
				action: "transform",
				text: `${event.text}\n\n[PiHCA router] Deterministic intake failed. Report this exact error and do not fall back to ls/find:\n${result.stderr || result.stdout}`,
			};
		}
		intakeOnlyTurn = true;
		return {
			action: "transform",
			text: `${event.text}\n\n[PiHCA router: authoritative intake completed]\n${result.stdout}\n\nDo not call tools this turn. Present this compact inventory, require one plate selection when listed, and ask the four returned assay questions together. Do not infer channel biology, segment, or launch a batch.`,
		};
	});

	pi.on("before_agent_start", (event) => {
		if (!pihcaSession) return undefined;
		return {
			systemPrompt: `${event.systemPrompt}\n\n## Active PiHCA Assay Session\nUse the installed high-content-microscopy skill as the authoritative workflow. Act as a rigorous microscopy assay expert. Use HCS.ai metadata-native commands, one plate at a time, immutable pilots, reviewed segmentation/filter parameters, and parallel wells only after approval. Never replace intake with broad ls/find output, infer channel biology, choose segmentation by object count, or submit an unapproved batch.`,
		};
	});

	pi.on("tool_call", (event) => {
		if (intakeOnlyTurn) {
			return { block: true, reason: "PiHCA intake is already complete; present its result and await assay answers." };
		}
		return undefined;
	});

	pi.on("agent_end", () => {
		intakeOnlyTurn = false;
	});
}
