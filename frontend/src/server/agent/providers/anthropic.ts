// Adapter for Anthropic models. The model id comes from configuration, never from code.

import Anthropic from "@anthropic-ai/sdk";
import type { ModelProvider, ModelRequest } from "./types.ts";

export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

export interface AnthropicOptions {
  model: string;
  effort?: Effort;
  client?: Anthropic; // injected in tests
}

const REVIEW_TIMEOUT_MS = 120_000;

export function createAnthropicProvider(options: AnthropicOptions): ModelProvider {
  const client = options.client ?? new Anthropic({ timeout: REVIEW_TIMEOUT_MS });

  return {
    name: `anthropic:${options.model}`,
    async complete(request: ModelRequest): Promise<unknown> {
      const response = await client.messages.create({
        model: options.model,
        max_tokens: 16000,
        system: request.system,
        messages: [
          {
            role: "user",
            content: [
              ...request.images.map((image) => ({
                type: "image" as const,
                source: { type: "base64" as const, media_type: image.mediaType, data: image.data },
              })),
              { type: "text" as const, text: request.text },
            ],
          },
        ],
        output_config: {
          format: { type: "json_schema", schema: request.schema },
          ...(options.effort ? { effort: options.effort } : {}),
        },
      });

      if (response.stop_reason === "refusal") throw new Error("model refused to review this submission");
      if (response.stop_reason === "max_tokens") throw new Error("model reply was cut off");
      const text = response.content.flatMap((block) => (block.type === "text" ? [block.text] : [])).join("");
      return JSON.parse(text) as unknown;
    },
  };
}
