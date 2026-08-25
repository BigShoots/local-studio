import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const DEFAULT_PORT = 8080;

function parseEnv(contents: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of contents.split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

function environmentFileFromShow(output: string): string | null {
  const value = output.match(/^EnvironmentFiles=(.+)$/m)?.[1]?.trim();
  if (!value) return null;
  const withoutMetadata = value.replace(/\s+\(ignore_errors=(?:yes|no)\)\s*$/, "").trim();
  const unquoted = withoutMetadata.replace(/^['"]|['"]$/g, "");
  return unquoted.startsWith("-") ? unquoted.slice(1) : unquoted;
}

function controllerUrl(values: Record<string, string>): string {
  const port = Number(values.LOCAL_STUDIO_PORT || process.env.LOCAL_STUDIO_PORT || DEFAULT_PORT);
  return `http://127.0.0.1:${Number.isInteger(port) && port > 0 ? port : DEFAULT_PORT}`;
}

async function waitForController(url: string, apiKey: string): Promise<boolean> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${url}/health`, {
        headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined,
        signal: AbortSignal.timeout(1_500),
      });
      if (response.ok) return true;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

export interface LocalControllerBootstrap {
  apiKey: string;
  service: string;
  url: string;
}

export async function startLocalController(): Promise<LocalControllerBootstrap | null> {
  if (process.platform !== "linux") return null;
  const requestedPort = Number(process.env.LOCAL_STUDIO_PORT || DEFAULT_PORT);
  const port = Number.isInteger(requestedPort) && requestedPort > 0 ? requestedPort : DEFAULT_PORT;
  const service =
    process.env.LOCAL_STUDIO_CONTROLLER_SERVICE || `local-studio-controller-${port}.service`;
  try {
    const { stdout } = await execFileAsync("systemctl", [
      "--user",
      "show",
      service,
      "--property=EnvironmentFiles",
    ]);
    const envFile = environmentFileFromShow(stdout);
    if (!envFile) return null;
    const values = parseEnv(await readFile(envFile, "utf8"));
    const apiKey = values.LOCAL_STUDIO_API_KEY?.trim() || "";
    const url = controllerUrl(values);
    await execFileAsync("systemctl", ["--user", "start", service]);
    if (!(await waitForController(url, apiKey))) return null;
    process.env.LOCAL_STUDIO_API_KEY = apiKey;
    process.env.LOCAL_STUDIO_BACKEND_URL = url;
    process.env.BACKEND_URL = url;
    return { apiKey, service, url };
  } catch {
    return null;
  }
}
