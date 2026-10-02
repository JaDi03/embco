// Picks the model provider from configuration. Switching model or vendor is an env change.
//   AGENT_PROVIDER  anthropic
//   AGENT_MODEL     model id for that provider
//   AGENT_EFFORT    optional: low | medium | high | xhigh | max

import { createAnthropicProvider, type Effort } from "./anthropic.ts";
import type { ModelProvider } from "./types.ts";

const EFFORTS: readonly Effort[] = ["low", "medium", "high", "xhigh", "max"];

export function providerFromEnv(env: Record<string, string | undefined> = process.env): ModelProvider {
  const provider = env.AGENT_PROVIDER?.trim();
  const model = env.AGENT_MODEL?.trim();
  if (!provider || !model) throw new Error("AGENT_PROVIDER and AGENT_MODEL must be set");

  const effort = env.AGENT_EFFORT?.trim() || undefined;
  if (effort && !EFFORTS.includes(effort as Effort)) throw new Error(`AGENT_EFFORT "${effort}" is not one of ${EFFORTS.join(", ")}`);

  switch (provider) {
    case "anthropic":
      return createAnthropicProvider({ model, effort: effort as Effort | undefined });
    default:
      throw new Error(`unknown AGENT_PROVIDER "${provider}"`);
  }
}
