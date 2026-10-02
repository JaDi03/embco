// What the brain needs from any model provider. Adapters translate this to one vendor's API.

import type { TaskImage } from "../review.ts";

export interface ModelRequest {
  system: string;
  text: string;
  images: TaskImage[];
  schema: Record<string, unknown>; // JSON Schema the reply must follow
}

export interface ModelProvider {
  name: string; // e.g. "anthropic:<model>", recorded for traceability
  /** Returns the parsed JSON reply. Throws if the model refuses, is cut off or returns no JSON. */
  complete(request: ModelRequest): Promise<unknown>;
}
