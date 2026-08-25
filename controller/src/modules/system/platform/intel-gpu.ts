import { readdirSync, readFileSync, realpathSync } from "node:fs";
import { basename, join } from "node:path";
import { Effect } from "effect";
import type { GpuInfo } from "../../models/types";
import { resolveBinary, runCommandAsyncEffect } from "../../../core/command";

type IntelPciGpu = {
  path: string;
  address: string;
  deviceId: string;
  classCode: string;
  driver: string | null;
};

type IntelXpuSmiStats = {
  memoryTotalMb: number | null;
  memoryUsedMb: number | null;
  powerDraw: number | null;
};

type CounterSample = {
  at: number;
  value: number;
};

const PCI_DEVICES_DIR = "/sys/bus/pci/devices";
const DRM_DIR = "/sys/class/drm";
const ARC_PRO_B70_DEVICE_ID = "0xe223";
const ARC_PRO_B70_MEMORY_MB = 32_656;
const INTEL_CONTAINER_NAME = "local-studio-llm";
const utilizationSamples = new Map<string, CounterSample>();
const energySamples = new Map<string, CounterSample>();

const readText = (path: string): string | null => {
  try {
    return readFileSync(path, "utf8").trim();
  } catch {
    return null;
  }
};

const readNumber = (path: string): number | null => {
  const text = readText(path);
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
};

const readDeviceDriver = (devicePath: string): string | null => {
  try {
    return basename(realpathSync(join(devicePath, "driver")));
  } catch {
    return null;
  }
};

const isIntelComputeGpu = (gpu: IntelPciGpu): boolean => {
  if (gpu.driver === "xe") return true;
  if (gpu.deviceId.toLowerCase() === ARC_PRO_B70_DEVICE_ID) return true;
  return gpu.classCode.toLowerCase().startsWith("0x03");
};

const discoverIntelPciGpus = (): IntelPciGpu[] => {
  try {
    return readdirSync(PCI_DEVICES_DIR, { withFileTypes: true })
      .filter((entry) => entry.isSymbolicLink() || entry.isDirectory())
      .map((entry) => {
        const path = join(PCI_DEVICES_DIR, entry.name);
        const vendor = readText(join(path, "vendor"))?.toLowerCase();
        if (vendor !== "0x8086") return null;

        const gpu: IntelPciGpu = {
          path,
          address: entry.name,
          deviceId: readText(join(path, "device")) ?? "",
          classCode: readText(join(path, "class")) ?? "",
          driver: readDeviceDriver(path),
        };
        return isIntelComputeGpu(gpu) ? gpu : null;
      })
      .filter((entry): entry is IntelPciGpu => Boolean(entry))
      .sort((a, b) => a.address.localeCompare(b.address));
  } catch {
    return [];
  }
};

const findDrmDevicePaths = (pciPath: string): string[] => {
  try {
    return readdirSync(DRM_DIR, { withFileTypes: true })
      .filter((entry) => entry.name.startsWith("card"))
      .map((entry) => {
        const devicePath = join(DRM_DIR, entry.name, "device");
        try {
          return realpathSync(devicePath) === realpathSync(pciPath)
            ? join(DRM_DIR, entry.name, "device")
            : null;
        } catch {
          return null;
        }
      })
      .filter((entry): entry is string => Boolean(entry));
  } catch {
    return [];
  }
};

const readFirstNumber = (paths: string[]): number | null => {
  for (const path of paths) {
    const value = readNumber(path);
    if (value !== null) return value;
  }
  return null;
};

const findHwmonPaths = (pciPath: string): string[] => {
  try {
    return readdirSync(join(pciPath, "hwmon"), { withFileTypes: true })
      .filter((entry) => entry.name.startsWith("hwmon"))
      .map((entry) => join(pciPath, "hwmon", entry.name));
  } catch {
    return [];
  }
};

const readHwmonMetric = (hwmonPaths: string[], fileName: string): number | null =>
  readFirstNumber(hwmonPaths.map((path) => join(path, fileName)));

const readLabeledHwmonMetric = (
  hwmonPaths: string[],
  metric: string,
  labels: readonly string[],
): number | null => {
  for (const hwmonPath of hwmonPaths) {
    let entries: string[];
    try {
      entries = readdirSync(hwmonPath);
    } catch {
      continue;
    }
    for (const entry of entries.filter((name) => new RegExp(`^${metric}\\d+_label$`).test(name))) {
      const label = readText(join(hwmonPath, entry))?.toLowerCase();
      if (!label || !labels.includes(label)) continue;
      const input = entry.replace(/_label$/, "_input");
      const value = readNumber(join(hwmonPath, input));
      if (value !== null) return value;
    }
  }
  return null;
};

const readFirstHwmonInput = (hwmonPaths: string[], metric: string): number | null => {
  for (const hwmonPath of hwmonPaths) {
    let entries: string[];
    try {
      entries = readdirSync(hwmonPath).sort();
    } catch {
      continue;
    }
    for (const entry of entries.filter((name) => new RegExp(`^${metric}\\d+_input$`).test(name))) {
      const value = readNumber(join(hwmonPath, entry));
      if (value !== null) return value;
    }
  }
  return null;
};

const sampledRate = (
  samples: Map<string, CounterSample>,
  key: string,
  value: number | null,
): { elapsedMs: number; valueDelta: number } | null => {
  if (value === null) return null;
  const at = Date.now();
  const previous = samples.get(key);
  samples.set(key, { at, value });
  if (!previous || at <= previous.at || value < previous.value) return null;
  return { elapsedMs: at - previous.at, valueDelta: value - previous.value };
};

const readUtilization = (gpu: IntelPciGpu): { available: boolean; value: number } => {
  const idle = readNumber(join(gpu.path, "tile0", "gt0", "gtidle", "idle_residency_ms"));
  const rate = sampledRate(utilizationSamples, gpu.address, idle);
  if (idle === null) return { available: false, value: 0 };
  if (!rate || rate.elapsedMs < 50) return { available: true, value: 0 };
  const busyFraction = 1 - rate.valueDelta / rate.elapsedMs;
  return {
    available: true,
    value: Math.round(Math.max(0, Math.min(1, busyFraction)) * 100),
  };
};

const readPowerFromEnergy = (hwmonPaths: string[], key: string): number | null => {
  const energy = readHwmonMetric(hwmonPaths, "energy1_input");
  const rate = sampledRate(energySamples, key, energy);
  if (!rate || rate.elapsedMs < 50) return null;
  return Number((rate.valueDelta / rate.elapsedMs / 1000).toFixed(1));
};

const nullableNumber = (value: string | undefined): number | null => {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

const readIntelXpuSmiStats = (index: number): Effect.Effect<IntelXpuSmiStats | null> => {
  const docker = resolveBinary("docker");
  if (!docker) return Effect.succeed(null);
  return runCommandAsyncEffect(
    docker,
    [
      "exec",
      INTEL_CONTAINER_NAME,
      "xpu-smi",
      "--query-gpu=power.draw,memory.used,memory.total",
      `--id=${index}`,
      "--format=csv,noheader,nounits",
    ],
    { timeoutMs: 2_000, maxOutputBytes: 16_384 },
  ).pipe(
    Effect.map((result) => {
      if (result.status !== 0) return null;
      const values = result.stdout
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .reverse()
        .map((line) => line.split(",").map((value) => value.trim()))
        .find((parts) => parts.length === 3);
      if (!values) return null;
      return {
        powerDraw: nullableNumber(values[0]),
        memoryUsedMb: nullableNumber(values[1]),
        memoryTotalMb: nullableNumber(values[2]),
      };
    }),
  );
};

const readIntelName = (gpu: IntelPciGpu): Effect.Effect<string> => {
  if (gpu.deviceId.toLowerCase() === ARC_PRO_B70_DEVICE_ID) {
    return Effect.succeed("Intel Arc Pro B70");
  }
  const lspci = resolveBinary("lspci");
  if (lspci) {
    return runCommandAsyncEffect(lspci, ["-s", gpu.address.replace(/^0000:/, "")], {
      timeoutMs: 2_000,
    }).pipe(
      Effect.map((result) => {
        if (result.status === 0 && result.stdout) {
          const name = result.stdout.replace(/^[0-9a-f:.]+\s+/i, "").trim();
          if (name) return name;
        }
        return "Intel Arc GPU";
      }),
    );
  }

  return Effect.succeed("Intel Arc GPU");
};

export const getGpuInfoFromIntelSysfs = (): Effect.Effect<GpuInfo[]> =>
  Effect.sync(discoverIntelPciGpus).pipe(
    Effect.flatMap((gpus) =>
      Effect.forEach(gpus, (gpu, index) =>
        Effect.gen(function* () {
          const drmDevicePaths = findDrmDevicePaths(gpu.path);
          const xpuSmiStats = yield* readIntelXpuSmiStats(index);
          const reportedMemoryTotal = readFirstNumber(
            drmDevicePaths.map((path) => join(path, "mem_info_vram_total")),
          );
          const reportedMemoryUsed = readFirstNumber(
            drmDevicePaths.map((path) => join(path, "mem_info_vram_used")),
          );
          const smiMemoryTotal =
            xpuSmiStats?.memoryTotalMb !== null && xpuSmiStats?.memoryTotalMb !== undefined
              ? xpuSmiStats.memoryTotalMb * 1024 * 1024
              : null;
          const smiMemoryUsed =
            xpuSmiStats?.memoryUsedMb !== null && xpuSmiStats?.memoryUsedMb !== undefined
              ? xpuSmiStats.memoryUsedMb * 1024 * 1024
              : null;
          const memoryTotal =
            reportedMemoryTotal ??
            smiMemoryTotal ??
            (gpu.deviceId.toLowerCase() === ARC_PRO_B70_DEVICE_ID
              ? ARC_PRO_B70_MEMORY_MB * 1024 * 1024
              : 0);
          const memoryUsed = reportedMemoryUsed ?? smiMemoryUsed ?? 0;
          const memoryFree = Math.max(0, memoryTotal - memoryUsed);
          const hwmonPaths = findHwmonPaths(gpu.path);
          const temperatureInput =
            readLabeledHwmonMetric(hwmonPaths, "temp", ["pkg", "gpu", "card"]) ??
            readFirstHwmonInput(hwmonPaths, "temp");
          const temperature = Math.round((temperatureInput ?? 0) / 1000);
          const powerInput = readHwmonMetric(hwmonPaths, "power1_input");
          const sampledPower = readPowerFromEnergy(hwmonPaths, gpu.address);
          const powerDraw =
            xpuSmiStats?.powerDraw ??
            (powerInput !== null
              ? Number((powerInput / 1_000_000).toFixed(1))
              : (sampledPower ?? 0));
          const powerLimit = Number(
            ((readHwmonMetric(hwmonPaths, "power1_cap") ?? 0) / 1_000_000).toFixed(1),
          );
          const utilization = readUtilization(gpu);
          const toMb = (bytes: number): number => Math.max(0, Math.round(bytes / 1024 / 1024));

          return {
            index,
            name: yield* readIntelName(gpu),
            memory_total_mb: toMb(memoryTotal),
            memory_used_mb: toMb(memoryUsed),
            memory_free_mb: toMb(memoryFree),
            memory_usage_available:
              (reportedMemoryTotal !== null && reportedMemoryUsed !== null) ||
              (smiMemoryTotal !== null && smiMemoryUsed !== null),
            utilization_pct: utilization.value,
            utilization_available: utilization.available,
            temp_c: temperature,
            temperature_available: temperatureInput !== null,
            power_draw: powerDraw,
            power_limit: powerLimit,
            power_available:
              typeof xpuSmiStats?.powerDraw === "number" ||
              powerInput !== null ||
              sampledPower !== null,
          };
        }),
      ),
    ),
  );
