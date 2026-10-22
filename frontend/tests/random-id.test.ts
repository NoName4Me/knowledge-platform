import assert from "node:assert/strict";
import test from "node:test";
import { randomId } from "../src/lib/random-id.ts";

test("randomId 生成 UUID，并可回退到 getRandomValues", () => {
  const original = globalThis.crypto;
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {
        getRandomValues(values: Uint8Array) {
          values.fill(0xab);
          return values;
        },
      },
    });
    assert.match(
      randomId(),
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  } finally {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: original,
    });
  }
});
