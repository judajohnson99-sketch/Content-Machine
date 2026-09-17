// A fresh id per click (architecture plan §6/§11: "generated once per user
// click"). crypto.randomUUID isn't guaranteed in every test/runtime
// environment, hence the fallback - this never needs to be
// cryptographically strong, only unique enough to key one PipelineRun.
export function newClientRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
